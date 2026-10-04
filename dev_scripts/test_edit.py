"""Fusion-side test of gear editing (drawing.make_gear with edit=True). Run through the Fusion MCP server.

Creates a NEW scratch design document and prints PASS/FAIL lines.
"""
import importlib
import math
import os
import sys
import types

import adsk.core
import adsk.fusion

ROOT = r'C:\Users\greg\AppData\Roaming\Autodesk\Autodesk Fusion 360\API\AddIns\GearGremlin'
PKG = 'gg_dev'


def load_drawing():
    for name in [m for m in sys.modules if m == PKG or m.startswith(PKG + '.')]:
        del sys.modules[name]
    for name, path in [(PKG, ROOT), (PKG + '.commands', os.path.join(ROOT, 'commands')),
                       (PKG + '.commands.gearProfile', os.path.join(ROOT, 'commands', 'gearProfile'))]:
        mod = types.ModuleType(name)
        mod.__path__ = [path]
        sys.modules[name] = mod
    return importlib.import_module(PKG + '.commands.gearProfile.drawing')


results = []


def check(label, ok, detail=''):
    results.append(f'{"PASS" if ok else "FAIL"}  {label}  {detail}')


def run(context):
    app = adsk.core.Application.get()
    dr = load_drawing()
    gm = dr.gm

    app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    design = adsk.fusion.Design.cast(app.activeProduct)
    root = design.rootComponent
    sketch = root.sketches.add(root.xYConstructionPlane)
    circles = sketch.sketchCurves.sketchCircles
    P = lambda x, y: adsk.core.Point3D.create(x / 10, y / 10, 0)
    alpha = math.radians(20)

    a = circles.addByCenterRadius(P(0, 0), 2.2)
    b = circles.addByCenterRadius(P(37, 0), 1.5)
    sketch.geometricConstraints.addTangent(a, b)
    pa = gm.GearParams(2.0, 22, alpha, 0.05)
    pb = gm.GearParams(2.0, 15, alpha, 0.05)
    ra = dr.make_gear(a, pa, finalize=True)
    rb = dr.make_gear(b, pb, partner=a, finalize=True, reference_circles=True)
    rec_a, rec_b = dr.read_gear(a), dr.read_gear(b)
    check('ids assigned', rec_a.editable and rec_b.editable and rec_a.gear_id != rec_b.gear_id)
    check('B records its partner', rec_b.mesh_with == rec_a.gear_id)
    check('A parts tagged', len(dr.gear_parts(sketch, rec_a.gear_id)) == len(ra.entities), str(len(ra.entities)))
    check('B parts include ref circles', len(dr.gear_parts(sketch, rec_b.gear_id)) == len(rb.entities))
    tooth = ra.entities[5]
    check('gear_for_entity(tooth) finds A', dr.gear_for_entity(tooth) == a)
    check('gear_for_entity(circle) finds B', dr.gear_for_entity(b) == b)
    check('creating on a gear is blocked', not dr.make_gear(a, pa).ok)

    # Extrude A's gear region, then edit A and see whether the extrude survives.
    prof = None
    for i in range(sketch.profiles.count):
        p = sketch.profiles.item(i)
        c = p.areaProperties().centroid
        if math.hypot(c.x, c.y) < 0.5:
            prof = p
    ext = root.features.extrudeFeatures.addSimple(prof, adsk.core.ValueInput.createByString('5 mm'),
                                                  adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    check('extrude created', ext is not None and ext.healthState == adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState)

    # A user's own line attached to a tooth point must survive edits of that gear.
    tip_arc = next(e for e in ra.entities if e.objectType.endswith('SketchArc'))
    p0 = tip_arc.startSketchPoint
    user_line = sketch.sketchCurves.sketchLines.addByTwoPoints(
        p0, adsk.core.Point3D.create(p0.geometry.x * 1.5, p0.geometry.y * 1.5, 0))

    curves_before = sketch.sketchCurves.count
    # Same geometry, different backlash: B stays aligned (backlash doesn't change phase).
    pa2 = gm.GearParams(2.0, 22, alpha, 0.15)
    re1 = dr.make_gear(a, pa2, finalize=True, edit=True)
    check('edit backlash ok', re1.ok, str(re1.errors))
    check('old curves replaced, not added', sketch.sketchCurves.count == curves_before,
          f'{curves_before} -> {sketch.sketchCurves.count}')
    check('same id kept', dr.read_gear(a).gear_id == rec_a.gear_id)
    check("user's attached line survives the edit", user_line.isValid)
    check('no dependent warning when still aligned',
          not any('no longer lines up' in w for w in re1.warnings), str(re1.warnings))
    check('A record updated', abs(dr.read_gear(a).params.backlash - 0.15) < 1e-12)
    check('extrude stays attached through the edit', re1.repointed == [ext.name] and
          ext.healthState == adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState,
          f'{re1.repointed} health={ext.healthState} error={ext.errorOrWarningMessage[:60]!r}')

    # Rotate A by half a tooth: B no longer meshes.
    re2 = dr.make_gear(a, pa2, rotation_offset=pa2.angular_pitch / 2, finalize=True, edit=True)
    check('half-tooth rotation warns about B', any('15-tooth' in w for w in re2.warnings), str(re2.warnings))

    # More teeth: A grows, B is held, so A moves; B no longer meshes.
    b_center = dr.circle_center_mm(b)
    re3 = dr.make_gear(a, gm.GearParams(2.0, 24, alpha, 0.05), finalize=True, edit=True)
    check('teeth change ok', re3.ok, str(re3.errors))
    check('B held during A edit', math.dist(b_center, dr.circle_center_mm(b)) < 1e-6)
    check('A resized to 48', abs(dr.circle_radius_mm(a) * 2 - 48) < 1e-6)
    # A slides along the line of centers, so whether B still meshes depends on the phase; compare
    # the warning against an independent mesh check rather than assuming.
    still_meshed = gm.mesh_error(gm.GearParams(2.0, 24, alpha, 0.05), dr.circle_center_mm(a), re3.theta0,
                                 pb, dr.circle_center_mm(b), dr.read_gear(b).theta0) < 1e-6
    check('teeth change warns about B iff mesh broke',
          any('15-tooth' in w for w in re3.warnings) != still_meshed, f'still_meshed={still_meshed}')
    re3b = dr.make_gear(a, gm.GearParams(2.0, 24, alpha, 0.05), rotation_offset=0.05, finalize=True, edit=True)
    check('teeth change + offset warns about B', any('15-tooth' in w for w in re3b.warnings), str(re3b.warnings))
    re3 = dr.make_gear(a, gm.GearParams(2.0, 24, alpha, 0.05), finalize=True, edit=True)
    check('A tagged curves = new entities', len(dr.gear_parts(sketch, rec_a.gear_id)) == len(re3.entities))

    # Editing B to re-align with A clears it (B's mesh_with is A).
    re4 = dr.make_gear(b, pb, partner=a, finalize=True, edit=True, reference_circles=False)
    check('re-align B ok', re4.ok, str(re4.errors))
    check('B ref circles removed', len(dr.gear_parts(sketch, rec_b.gear_id)) == len(re4.entities))
    ca, cb = dr.circle_center_mm(a), dr.circle_center_mm(b)
    check('B meshes again', gm.mesh_error(gm.GearParams(2.0, 24, alpha, 0.05), ca, dr.read_gear(a).theta0,
                                          pb, cb, dr.read_gear(b).theta0) < 1e-9)
    check('profiles after edits', sketch.profiles.count == 2, f'count={sketch.profiles.count}')

    # Version-1 gears can be read but not edited.
    v1 = circles.addByCenterRadius(P(120, 0), 2.0)
    v1.attributes.add(dr.ATTR_GROUP, dr.ATTR_NAME,
                      '{"version": 1, "type": "external", "module_mm": 2.0, "pressure_angle_deg": 20.0, '
                      '"teeth": 20, "theta0_rad": 0.0, "backlash_mm": 0.0}')
    rv = dr.make_gear(v1, gm.GearParams(2.0, 20, alpha), edit=True)
    check('v1 gear edit blocked', not rv.ok, str(rv.errors))

    app.activeViewport.fit()
    report = '\n'.join(results) + f'\n{sum(r.startswith("PASS") for r in results)}/{len(results)} passed'
    with open(os.path.join(ROOT, 'tests', 'out', 'test_edit.txt'), 'w', encoding='utf-8') as f:
        f.write(report)  # MCP calls can time out on slow recomputes; the file survives
    print(report)
