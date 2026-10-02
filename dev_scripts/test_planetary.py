"""Fusion-side test of building a planetary set with the tool's workflow. Run through the Fusion MCP server.

Creates a NEW scratch design: sun at the origin, 3 planets on 120° construction lines, each
tangent to the sun and the ring, ring concentric with the sun. Then: sun → planets (mesh with
sun) → ring (mesh with planet 1), and checks every planet meshes with the ring.
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
    sk = root.sketches.add(root.xYConstructionPlane)
    curves = sk.sketchCurves
    cons = sk.geometricConstraints
    dims = sk.sketchDimensions
    P = lambda x, y: adsk.core.Point3D.create(x / 10, y / 10, 0)
    alpha = math.radians(20)

    # Roughly drawn, as a user would: sun ~19 mm radius, planets ~10, ring ~39.
    sun = curves.sketchCircles.addByCenterRadius(P(0, 0), 1.9)
    sun.centerSketchPoint.isFixed = True
    ring = curves.sketchCircles.addByCenterRadius(P(0, 0), 3.9)
    cons.addCoincident(ring.centerSketchPoint, sun.centerSketchPoint)
    planets, spokes = [], []
    for k in range(3):
        ang = math.radians(90 + 120 * k)
        pl = curves.sketchCircles.addByCenterRadius(P(29 * math.cos(ang), 29 * math.sin(ang)), 1.0)
        spoke = curves.sketchLines.addByTwoPoints(sun.centerSketchPoint, pl.centerSketchPoint)
        spoke.isConstruction = True
        cons.addTangent(sun, pl)
        cons.addTangent(ring, pl)
        planets.append(pl)
        spokes.append(spoke)
    cons.addVertical(spokes[0])
    for k in (1, 2):
        dims.addAngularDimension(spokes[0], spokes[k], P(10 * math.cos(math.radians(90 + 60 * k)),
                                                         10 * math.sin(math.radians(90 + 60 * k))))

    zs, zp, zr = 20, 10, 40
    r = dr.make_gear(sun, gm.GearParams(2.0, zs, alpha, 0.05), finalize=True)
    check('sun', r.ok, str(r.errors + r.warnings))
    for i, pl in enumerate(planets):
        r = dr.make_gear(pl, gm.GearParams(2.0, zp, alpha, 0.05), partner=sun, finalize=True)
        check(f'planet {i + 1}', r.ok, str(r.errors + r.warnings))
    r = dr.make_gear(ring, gm.GearParams(2.0, zr, alpha, 0.05, gm.INTERNAL), partner=planets[0], finalize=True)
    check('ring', r.ok, str(r.errors + r.warnings))

    rec_ring = dr.read_gear(ring)
    c_ring = dr.circle_center_mm(ring)
    for i, pl in enumerate(planets):
        rec = dr.read_gear(pl)
        if rec is None or rec_ring is None:
            check(f'planet {i + 1} meshes with ring', False, 'a gear failed to generate')
            continue
        c = dr.circle_center_mm(pl)
        err = gm.mesh_error(rec_ring.params, c_ring, rec_ring.theta0, rec.params, c, rec.theta0)
        dist = math.dist(c, c_ring)
        check(f'planet {i + 1} meshes with ring', err < 1e-6 and abs(dist - 30) < 1e-6,
              f'mesh error {err:.2e} teeth, center distance {dist:.6f}')
    areas = sorted(round(sk.profiles.item(i).areaProperties().area * 100, 1) for i in range(sk.profiles.count))
    check('profiles', True, f'count={sk.profiles.count} areas mm2={areas}')
    curves_before = sk.sketchCurves.count

    # Wrong ring size for this sun/planet pair: constraints fix the ring at 80 mm, 42 teeth needs 84.
    bad = dr.make_gear(ring, gm.GearParams(2.0, 42, alpha, 0.05, gm.INTERNAL), partner=planets[0], edit=True)
    check('inconsistent tooth count gives a clear error', not bad.ok and '⌀80.00' in ' '.join(bad.errors),
          str(bad.errors))
    check('failed edit keeps the old ring', sk.sketchCurves.count == curves_before,
          f'{curves_before} -> {sk.sketchCurves.count}')

    app.activeViewport.fit()
    print('\n'.join(results))
    print(f'{sum(x.startswith("PASS") for x in results)}/{len(results)} passed')
