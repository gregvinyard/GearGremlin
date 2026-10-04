"""Fusion-side test: editing a gear keeps extrudes made from it attached (re-pointed to the new
profile). Run through the Fusion MCP server; creates a NEW scratch design. Results also go to
tests/out/test_repoint.txt, written as it goes.
"""
import importlib
import math
import os
import sys
import time
import types

import adsk.core
import adsk.fusion

ROOT = r'C:\Users\greg\AppData\Roaming\Autodesk\Autodesk Fusion 360\API\AddIns\GearGremlin'
PKG = 'gg_dev'
OUT = os.path.join(ROOT, 'tests', 'out', 'test_repoint.txt')
HEALTHY = adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState
results = []


def check(label, ok, detail=''):
    line = f'{"PASS" if ok else "FAIL"}  {label}  {detail}'
    results.append(line)
    with open(OUT, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def load():
    for name in [m for m in sys.modules if m == PKG or m.startswith(PKG + '.')]:
        del sys.modules[name]
    for name, path in [(PKG, ROOT), (PKG + '.commands', os.path.join(ROOT, 'commands'))]:
        mod = types.ModuleType(name)
        mod.__path__ = [path]
        sys.modules[name] = mod
    return importlib.import_module(PKG + '.commands.gearProfile.drawing')


def region(sketch, loops, near=None):
    """The profile with this many loops (and, if given, whose centroid is near a point, in cm)."""
    best = None
    for i in range(sketch.profiles.count):
        p = sketch.profiles.item(i)
        if p.profileLoops.count != loops:
            continue
        c = p.areaProperties().centroid
        d = 0 if near is None else math.hypot(c.x - near[0], c.y - near[1])
        if best is None or d < best[0]:
            best = (d, p)
    return best[1] if best else None


def area_mm2(profiles) -> float:
    return sum(p.areaProperties().area * 100 for p in profiles)


def extrude(root, profiles, height='5 mm'):
    value = profiles[0] if len(profiles) == 1 else None
    if value is None:
        value = adsk.core.ObjectCollection.create()
        for p in profiles:
            value.add(p)
    return root.features.extrudeFeatures.addSimple(value, adsk.core.ValueInput.createByString(height),
                                                   adsk.fusion.FeatureOperations.NewBodyFeatureOperation)


def volume_mm3(feature) -> float:
    return sum(feature.bodies.item(i).volume for i in range(feature.bodies.count)) * 1000


def run(context):
    if os.path.exists(OUT):
        os.remove(OUT)
    app = adsk.core.Application.get()
    dr = load()
    gm = dr.gm
    app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    design = adsk.fusion.Design.cast(app.activeProduct)
    root = design.rootComponent
    P = lambda x, y: adsk.core.Point3D.create(x / 10, y / 10, 0)
    alpha = math.radians(20)

    # --- 1. external gear with a bore: gear-minus-bore extruded ---------------------------
    sk = root.sketches.add(root.xYConstructionPlane)
    a = sk.sketchCurves.sketchCircles.addByCenterRadius(P(0, 0), 2.2)
    dr.make_gear(a, gm.GearParams(2.0, 22, alpha, 0.05), finalize=True)
    sk.sketchCurves.sketchCircles.addByCenterRadius(a.centerSketchPoint, 0.3)       # the user's bore
    ext1 = extrude(root, [region(sk, 2)])
    check('gear with bore extruded', ext1.healthState == HEALTHY, f'{volume_mm3(ext1):.1f} mm3')

    preview = dr.make_gear(a, gm.GearParams(2.0, 22, alpha, 0.1), finalize=False, edit=True)
    check('preview doesn\'t re-point', not preview.repointed and not preview.not_repointed)
    coll = adsk.core.ObjectCollection.create()   # undo the preview by hand (Fusion does this in the dialog)
    for e in preview.entities:
        coll.add(e)
    design.deleteEntities(coll)
    rec = dr.read_gear(a)
    dr.make_gear(a, rec.params, finalize=True, edit=True)   # restore a clean gear after the manual undo
    ext1.timelineObject.rollTo(True)
    ext1.profile = region(sk, 2)
    design.timeline.moveToEnd()
    # A fillet on the bore's top edge: a downstream feature that must survive the edits.
    top = ext1.endFaces.item(0)
    bore_edge = next(e for e in (top.edges.item(i) for i in range(top.edges.count))
                     if adsk.core.Circle3D.cast(e.geometry) is not None
                     and abs(adsk.core.Circle3D.cast(e.geometry).radius - 0.3) < 1e-6)
    edges = adsk.core.ObjectCollection.create()
    edges.add(bore_edge)
    fillet_input = root.features.filletFeatures.createInput()
    fillet_input.addConstantRadiusEdgeSet(edges, adsk.core.ValueInput.createByString('0.5 mm'), True)
    fillet = root.features.filletFeatures.add(fillet_input)
    check('fillet on the bore made', fillet.healthState == HEALTHY)
    # The body now includes the fillet; it removes the same volume whatever the teeth look like.
    fillet_removed = area_mm2([region(sk, 2)]) * 5 - volume_mm3(ext1)

    for label, params in (('same tooth count (backlash 0.05 → 0.2)', gm.GearParams(2.0, 22, alpha, 0.2)),
                          ('tooth count 22 → 26', gm.GearParams(2.0, 26, alpha, 0.2)),
                          ('stub teeth', gm.GearParams(2.0, 26, alpha, 0.2, gm.EXTERNAL, 0.8))):
        t = time.perf_counter()
        r = dr.make_gear(a, params, finalize=True, edit=True)
        dt = time.perf_counter() - t
        expected = area_mm2([region(sk, 2)]) * 5 - fillet_removed
        check(f'{label}: re-pointed', r.ok and r.repointed == [ext1.name] and not r.not_repointed,
              f'{r.repointed} {r.not_repointed} {dt:.2f} s')
        check(f'{label}: extrude healthy and follows new shape',
              ext1.healthState == HEALTHY and abs(volume_mm3(ext1) - expected) < 0.01,
              f'{volume_mm3(ext1):.1f} vs {expected:.1f} mm3 {ext1.errorOrWarningMessage[:60]!r}')
    check('timeline marker back at the end', design.timeline.markerPosition == design.timeline.count)
    check('downstream fillet survives three edits', fillet.healthState == HEALTHY,
          repr(fillet.errorOrWarningMessage[:80]))

    # --- 2. ring with a user-drawn outer rim -------------------------------------------------
    sk2 = root.sketches.add(root.xZConstructionPlane)
    rg = sk2.sketchCurves.sketchCircles.addByCenterRadius(P(0, 0), 4.0)
    dr.make_gear(rg, gm.GearParams(2.0, 40, alpha, 0.05, gm.INTERNAL), finalize=True)
    sk2.sketchCurves.sketchCircles.addByCenterRadius(rg.centerSketchPoint, 5.0)       # the rim
    ext2 = extrude(root, [region(sk2, 2)])
    r = dr.make_gear(rg, gm.GearParams(2.0, 40, alpha, 0.15, gm.INTERNAL), finalize=True, edit=True)
    expected = area_mm2([region(sk2, 2)]) * 5
    check('ring with rim: re-pointed', r.repointed == [ext2.name],
          f'{r.repointed} {r.not_repointed} {dr.repoint_errors}')
    check('ring with rim: extrude follows new shape',
          ext2.healthState == HEALTHY and abs(volume_mm3(ext2) - expected) < 0.01,
          f'{volume_mm3(ext2):.1f} vs {expected:.1f} mm3')

    # --- 3. one extrude using two regions: gear-minus-bore and the bore itself ----------------
    sk3 = root.sketches.add(root.yZConstructionPlane)
    b = sk3.sketchCurves.sketchCircles.addByCenterRadius(P(0, 0), 1.5)
    dr.make_gear(b, gm.GearParams(2.0, 15, alpha, 0.05), finalize=True)
    sk3.sketchCurves.sketchCircles.addByCenterRadius(b.centerSketchPoint, 0.4)
    ext3 = extrude(root, [region(sk3, 2), region(sk3, 1, near=(0, 0))])
    r = dr.make_gear(b, gm.GearParams(2.0, 15, alpha, 0.15), finalize=True, edit=True)
    expected = area_mm2([region(sk3, 2), region(sk3, 1, near=(0, 0))]) * 5
    check('two-region extrude: re-pointed', r.repointed == [ext3.name],
          f'{r.repointed} {r.not_repointed} {dr.repoint_errors}')
    check('two-region extrude: both regions kept',
          ext3.healthState == HEALTHY and abs(volume_mm3(ext3) - expected) < 0.01,
          f'{volume_mm3(ext3):.1f} vs {expected:.1f} mm3')
    check('earlier extrudes still healthy', ext1.healthState == HEALTHY and ext2.healthState == HEALTHY)

    # --- 4. gear-only extrude (no bore): Fusion loses this one on its own -----------------------
    sk4 = root.sketches.add(root.xYConstructionPlane)
    g = sk4.sketchCurves.sketchCircles.addByCenterRadius(P(0, 120), 2.0)
    dr.make_gear(g, gm.GearParams(2.0, 20, alpha, 0.05), finalize=True)
    ext4 = extrude(root, [region(sk4, 1, near=(0, 12))])
    r = dr.make_gear(g, gm.GearParams(2.0, 24, alpha, 0.1), finalize=True, edit=True)
    expected = area_mm2([region(sk4, 1, near=(0, 12))]) * 5
    check('gear-only extrude: re-pointed', r.repointed == [ext4.name],
          f'{r.repointed} {r.not_repointed} {dr.repoint_errors}')
    check('gear-only extrude: follows new shape',
          ext4.healthState == HEALTHY and abs(volume_mm3(ext4) - expected) < 0.01,
          f'{volume_mm3(ext4):.1f} vs {expected:.1f} mm3 {ext4.errorOrWarningMessage[:60]!r}')

    # --- 5. gear-only region plus an unrelated region of the same sketch, in one extrude ----------
    sk5 = root.sketches.add(root.xYConstructionPlane)
    h = sk5.sketchCurves.sketchCircles.addByCenterRadius(P(0, -120), 2.0)
    dr.make_gear(h, gm.GearParams(2.0, 20, alpha, 0.05), finalize=True)
    sk5.sketchCurves.sketchCircles.addByCenterRadius(P(60, -120), 0.5)   # a separate boss
    ext5 = extrude(root, [region(sk5, 1, near=(0, -12)), region(sk5, 1, near=(6, -12))])
    r = dr.make_gear(h, gm.GearParams(2.0, 20, alpha, 0.2), finalize=True, edit=True)
    expected = area_mm2([region(sk5, 1, near=(0, -12)), region(sk5, 1, near=(6, -12))]) * 5
    check('gear + separate region: re-pointed', r.repointed == [ext5.name],
          f'{r.repointed} {r.not_repointed} {dr.repoint_errors}')
    check('gear + separate region: follows new shape',
          ext5.healthState == HEALTHY and abs(volume_mm3(ext5) - expected) < 0.01,
          f'{volume_mm3(ext5):.1f} vs {expected:.1f} mm3 {ext5.errorOrWarningMessage[:60]!r}')

    app.activeViewport.fit()
    results.append(f'INFO  last re-point errors: {dr.repoint_errors}')
    judged = [x for x in results if not x.startswith('INFO')]
    report = '\n'.join(results) + f'\n{sum(x.startswith("PASS") for x in judged)}/{len(judged)} passed'
    print(report)
