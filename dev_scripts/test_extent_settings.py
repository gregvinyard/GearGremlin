"""Fusion-side test: editing a gear keeps every extrude/revolve setting, not just the profile.
Setting a profile through the API resets a symmetric extent's "Whole Length" to "Half Length";
drawing.py restores it. Creates a NEW scratch design with differently-configured features of one gear
sketch, edits the gear, and checks each feature's settings and body extent are unchanged.
Results also go to tests/out/test_extent_settings.txt.
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
OUT = os.path.join(ROOT, 'tests', 'out', 'test_extent_settings.txt')


def log(s):
    with open(OUT, 'a', encoding='utf-8') as f:
        f.write(s + '\n')


def load():
    for name in [m for m in sys.modules if m == PKG or m.startswith(PKG + '.')]:
        del sys.modules[name]
    for name, path in [(PKG, ROOT), (PKG + '.commands', os.path.join(ROOT, 'commands'))]:
        mod = types.ModuleType(name)
        mod.__path__ = [path]
        sys.modules[name] = mod
    return importlib.import_module(PKG + '.commands.gearProfile.drawing')


def snapshot(f) -> dict:
    s = {'extentType': f.extentType, 'operation': f.operation, 'thin': f.isThinExtrude}
    for label, ext in (('one', f.extentOne), ('two', f.extentTwo)):
        if ext is None:
            continue
        s[f'{label}.type'] = ext.objectType.split('::')[-1]
        sym = adsk.fusion.SymmetricExtentDefinition.cast(ext)
        if sym:
            s[f'{label}.fullLength'] = sym.isFullLength
            s[f'{label}.distance'] = adsk.fusion.ModelParameter.cast(sym.distance).expression
        dist = adsk.fusion.DistanceExtentDefinition.cast(ext)
        if dist:
            s[f'{label}.distance'] = dist.distance.expression
    for label in ('taperAngleOne', 'taperAngleTwo'):
        try:
            p = getattr(f, label)
            s[label] = p.expression if p else None
        except Exception:
            pass
    body = f.bodies.item(0) if f.bodies.count else None
    if body:
        b = body.boundingBox
        s['z range'] = (round(b.minPoint.z * 10, 3), round(b.maxPoint.z * 10, 3))
    return s


def run(context):
    if os.path.exists(OUT):
        os.remove(OUT)
    app = adsk.core.Application.get()
    dr = load()
    gm = dr.gm
    app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    design = adsk.fusion.Design.cast(app.activeProduct)
    root = design.rootComponent
    sk = root.sketches.add(root.xYConstructionPlane)
    g = sk.sketchCurves.sketchCircles.addByCenterRadius(adsk.core.Point3D.create(0, 0, 0), 2.0)
    dr.make_gear(g, gm.GearParams(2.0, 20, math.radians(20), 0.05), finalize=True)

    def gear_region():
        return next(sk.profiles.item(i) for i in range(sk.profiles.count))

    extrudes = root.features.extrudeFeatures
    VI = adsk.core.ValueInput.createByString
    feats = {}

    def make(name, setup):
        inp = extrudes.createInput(gear_region(), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        setup(inp)
        feats[name] = extrudes.add(inp)

    make('symmetric whole length', lambda i: i.setSymmetricExtent(VI('10 mm'), True))
    make('symmetric half length', lambda i: i.setSymmetricExtent(VI('10 mm'), False))
    make('one side flipped', lambda i: i.setOneSideExtent(adsk.fusion.DistanceExtentDefinition.create(VI('6 mm')),
                                                          adsk.fusion.ExtentDirections.NegativeExtentDirection))
    make('two sides', lambda i: i.setTwoSidesExtent(adsk.fusion.DistanceExtentDefinition.create(VI('3 mm')),
                                                    adsk.fusion.DistanceExtentDefinition.create(VI('7 mm'))))
    # A revolve about a line beside the gear, symmetric 90°.
    axis = sk.sketchCurves.sketchLines.addByTwoPoints(adsk.core.Point3D.create(5, -1, 0),
                                                      adsk.core.Point3D.create(5, 1, 0))
    revolves = root.features.revolveFeatures
    rin = revolves.createInput(gear_region(), axis, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    rin.setAngleExtent(True, VI('90 deg'))
    rev = revolves.add(rin)

    def rev_snapshot():
        d = adsk.fusion.AngleExtentDefinition.cast(rev.extentDefinition)
        b = rev.bodies.item(0).boundingBox if rev.bodies.count else None
        return {'isSymmetric': d.isSymmetric, 'angle': d.angle.expression,
                'z range': (round(b.minPoint.z * 10, 3), round(b.maxPoint.z * 10, 3)) if b else None}

    before = {name: snapshot(f) for name, f in feats.items()}
    rev_before = rev_snapshot()
    r = dr.make_gear(g, gm.GearParams(2.0, 20, math.radians(20), 0.2), finalize=True, edit=True)
    log(f'edit: re-pointed {r.repointed}, not {r.not_repointed}, errors {dr.repoint_errors}, '
        f'restored {dr.repoint_restored}')
    rev_after = rev_snapshot()
    results = [('revolve symmetric 90', rev_before == rev_after, f'{rev_before} -> {rev_after}')]
    for name, f in feats.items():
        after = snapshot(f)
        diffs = {k: (before[name].get(k), after.get(k)) for k in set(before[name]) | set(after)
                 if before[name].get(k) != after.get(k)}
        results.append((name, not diffs, f'changed {diffs}' if diffs else ''))
    for name, ok, detail in results:
        log(f'{"PASS" if ok else "FAIL"}  {name}: settings and body extent unchanged  {detail}')
    log(f'{sum(ok for _, ok, _ in results)}/{len(results)} passed')
    print(open(OUT, encoding='utf-8').read())
