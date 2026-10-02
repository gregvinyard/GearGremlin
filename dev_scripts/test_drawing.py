"""Fusion-side test of commands/gearProfile/drawing.py. Run through the Fusion MCP server.

Creates a NEW scratch design document (never touches existing ones), draws tangent
circles, generates gears with drawing.make_gear, and prints checks as PASS/FAIL lines.
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
    """Import the add-in code fresh under a private package name, so it never collides with the loaded add-in."""
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


def max_radius(entities, center_mm, to_mm):
    best = 0.0
    for e in entities:
        for pt in (e.startSketchPoint, e.endSketchPoint):
            g = pt.geometry
            best = max(best, math.hypot(to_mm(g.x) - center_mm[0], to_mm(g.y) - center_mm[1]))
    return best


def run(context):
    app = adsk.core.Application.get()
    dr = load_drawing()
    gm = dr.gm

    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    design = adsk.fusion.Design.cast(app.activeProduct)
    root = design.rootComponent
    sketch = root.sketches.add(root.xYConstructionPlane)
    circles = sketch.sketchCurves.sketchCircles
    cons = sketch.geometricConstraints
    P = lambda x, y: adsk.core.Point3D.create(x / 10, y / 10, 0)
    alpha = math.radians(20)

    # --- external pair: A (43 mm drawn → 22 teeth @ m2) and B tangent to it -----------
    a = circles.addByCenterRadius(P(0, 0), 2.15)
    b = circles.addByCenterRadius(P(21.5 + 15.4, 0), 1.54)
    cons.addTangent(a, b)
    za = gm.suggest_teeth(dr.circle_radius_mm(a) * 2, 2.0)
    zb = gm.suggest_teeth(dr.circle_radius_mm(b) * 2, 2.0)
    check('suggested teeth', (za, zb) == (22, 15), f'{za}, {zb}')
    pa = gm.GearParams(2.0, za, alpha, 0.1)
    pb = gm.GearParams(2.0, zb, alpha, 0.1)
    ra = dr.make_gear(a, pa, finalize=True)
    check('gear A ok', ra.ok, str(ra.errors + ra.warnings))
    a_center_before = dr.circle_center_mm(a)
    rb = dr.make_gear(b, pb, partner=a, reference_circles=True, finalize=True)
    check('gear B ok', rb.ok, str(rb.errors + rb.warnings))
    a_center_after = dr.circle_center_mm(a)
    moved = math.hypot(a_center_after[0] - a_center_before[0], a_center_after[1] - a_center_before[1])
    check('gear A held still during B resize', moved < 1e-6, f'moved {moved:.2e} mm')
    check('A center unfixed afterwards', not a.centerSketchPoint.isFixed)
    check('A diameter', abs(dr.circle_radius_mm(a) * 2 - 44.0) < 1e-6, f'{dr.circle_radius_mm(a) * 2:.6f}')
    check('B diameter', abs(dr.circle_radius_mm(b) * 2 - 30.0) < 1e-6, f'{dr.circle_radius_mm(b) * 2:.6f}')
    ca, cb = dr.circle_center_mm(a), dr.circle_center_mm(b)
    dist = math.hypot(cb[0] - ca[0], cb[1] - ca[1])
    check('still tangent after resize', abs(dist - 37.0) < 1e-6, f'{dist:.6f}')
    check('A tip radius', abs(max_radius(ra.entities, ca, dr.to_mm) - 24.0) < 1e-6,
          f'{max_radius(ra.entities, ca, dr.to_mm):.6f}')
    check('B tip radius', abs(max_radius(rb.entities[:-2], cb, dr.to_mm) - 17.0) < 1e-6)
    check('B mesh condition', abs(((gm.phase(math.atan2(cb[1] - ca[1], cb[0] - ca[0]), ra.theta0, 22)
                                   + gm.phase(math.atan2(ca[1] - cb[1], ca[0] - cb[0]), rb.theta0, 15)) % 1)
                                  - 0.5) < 1e-9)
    check('attribute stored', dr.read_gear(b) is not None and dr.read_gear(b).params.teeth == 15)
    check('pitch circles are construction', a.isConstruction and b.isConstruction)
    check('external pair: 2 profiles', sketch.profiles.count == 2, f'count={sketch.profiles.count}')

    # --- ring + pinion in a second sketch ----------------------------------------------
    s2 = root.sketches.add(root.xZConstructionPlane)
    c2 = s2.sketchCurves.sketchCircles
    ring = c2.addByCenterRadius(P(0, 0), 6.1)
    pin = c2.addByCenterRadius(P(0, 40), 1.9)
    s2.geometricConstraints.addTangent(ring, pin)
    pr = gm.GearParams(2.0, gm.suggest_teeth(122, 2.0), alpha, 0.1, gm.INTERNAL)
    pp = gm.GearParams(2.0, gm.suggest_teeth(38, 2.0), alpha, 0.1)
    rr = dr.make_gear(ring, pr, finalize=True)
    check('ring ok', rr.ok, str(rr.errors + rr.warnings))
    rp = dr.make_gear(pin, pp, partner=ring, finalize=True)
    check('pinion ok', rp.ok, str(rp.errors + rp.warnings))
    cr, cp = dr.circle_center_mm(ring), dr.circle_center_mm(pin)
    dist = math.hypot(cp[0] - cr[0], cp[1] - cr[1])
    check('ring/pinion internally tangent', abs(dist - (61 - 19)) < 1e-6, f'{dist:.6f}')
    check('ring+pinion: 2 profiles', s2.profiles.count == 2, f'count={s2.profiles.count}')

    # --- error paths -------------------------------------------------------------------
    s3 = root.sketches.add(root.yZConstructionPlane)
    fixed = s3.sketchCurves.sketchCircles.addByCenterRadius(P(0, 0), 2.0)
    fixed.isFixed = True
    rf = dr.make_gear(fixed, gm.GearParams(2.0, 20, alpha))
    check('fixed circle reports error', not rf.ok, str(rf.errors))
    plain = s3.sketchCurves.sketchCircles.addByCenterRadius(P(60, 0), 2.0)
    rn = dr.make_gear(plain, gm.GearParams(2.0, 20, alpha), partner=plain)
    check('non-gear partner blocked', not rn.ok, str(rn.errors))
    expr = s3.sketchCurves.sketchCircles.addByCenterRadius(P(120, 0), 2.0)
    d = s3.sketchDimensions.addDiameterDimension(expr, P(130, 10))
    d.parameter.expression = '40 mm * 1'
    check('expression warning', bool(dr.dimension_expression_warning(expr)), dr.dimension_expression_warning(expr))
    over = s3.sketchCurves.sketchCircles.addByCenterRadius(P(180, 0), 2.0)
    s3.sketchCurves.sketchCircles.addByCenterRadius(P(240, 0), 2.0)
    other = s3.sketchCurves.sketchCircles.item(s3.sketchCurves.sketchCircles.count - 1)
    s3.sketchDimensions.addDiameterDimension(other, P(250, 10))
    s3.geometricConstraints.addEqual(over, other)
    ro = dr.make_gear(over, gm.GearParams(2.0, 25, alpha))
    check('over-constrained circle reports error', not ro.ok, str(ro.errors))

    # --- default backlash (0.05 mm): loops must not touch or split into extra regions --
    for label, plane, ring_case in (('external B=0.05', root.xYConstructionPlane, False),
                                    ('ring B=0.05', root.xZConstructionPlane, True)):
        sk = root.sketches.add(plane)
        cc = sk.sketchCurves.sketchCircles
        if ring_case:
            g1 = cc.addByCenterRadius(P(0, 0), 6.0)
            g2 = cc.addByCenterRadius(P(0, 40), 2.0)
            p1 = gm.GearParams(2.0, 60, alpha, 0.05, gm.INTERNAL)
            p2 = gm.GearParams(2.0, 20, alpha, 0.05)
        else:
            g1 = cc.addByCenterRadius(P(0, 0), 2.2)
            g2 = cc.addByCenterRadius(P(37, 0), 1.5)
            p1 = gm.GearParams(2.0, 22, alpha, 0.05)
            p2 = gm.GearParams(2.0, 15, alpha, 0.05)
        sk.geometricConstraints.addTangent(g1, g2)
        r1 = dr.make_gear(g1, p1, finalize=True)
        r2 = dr.make_gear(g2, p2, partner=g1, finalize=True)
        check(f'{label}: gears ok', r1.ok and r2.ok, str(r1.errors + r2.errors))
        check(f'{label}: 2 profiles', sk.profiles.count == 2, f'count={sk.profiles.count}')

    app.activeViewport.fit()
    print('\n'.join(results))
    print(f'{sum(r.startswith("PASS") for r in results)}/{len(results)} passed')
