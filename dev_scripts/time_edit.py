"""Time one gear edit with and without an extrude made from the gear. Run through the Fusion MCP server.

Creates a NEW scratch design. Results go to tests/out/time_edit.txt (MCP calls can time out).
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
OUT = os.path.join(ROOT, 'tests', 'out', 'time_edit.txt')


def load_drawing():
    for name in [m for m in sys.modules if m == PKG or m.startswith(PKG + '.')]:
        del sys.modules[name]
    for name, path in [(PKG, ROOT), (PKG + '.commands', os.path.join(ROOT, 'commands')),
                       (PKG + '.commands.gearProfile', os.path.join(ROOT, 'commands', 'gearProfile'))]:
        mod = types.ModuleType(name)
        mod.__path__ = [path]
        sys.modules[name] = mod
    return importlib.import_module(PKG + '.commands.gearProfile.drawing')


def log(line):
    with open(OUT, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def timed(label, fn):
    t = time.perf_counter()
    result = fn()
    adsk.doEvents()
    log(f'{label}: {time.perf_counter() - t:.2f} s')
    return result


def run(context):
    if os.path.exists(OUT):
        os.remove(OUT)
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
    timed('create A (22 teeth)', lambda: dr.make_gear(a, pa, finalize=True))
    timed('create B (15 teeth, mesh)', lambda: dr.make_gear(b, gm.GearParams(2.0, 15, alpha, 0.05), partner=a,
                                                             finalize=True))
    timed('edit A, no extrude', lambda: dr.make_gear(a, gm.GearParams(2.0, 22, alpha, 0.1), finalize=True, edit=True))

    prof = None
    for i in range(sketch.profiles.count):
        p = sketch.profiles.item(i)
        c = p.areaProperties().centroid
        if math.hypot(c.x, c.y) < 0.5:
            prof = p
    timed('extrude A', lambda: root.features.extrudeFeatures.addSimple(
        prof, adsk.core.ValueInput.createByString('5 mm'), adsk.fusion.FeatureOperations.NewBodyFeatureOperation))
    timed('edit A, with extrude', lambda: dr.make_gear(a, gm.GearParams(2.0, 22, alpha, 0.05), finalize=True,
                                                       edit=True))
    timed('edit A again, with extrude', lambda: dr.make_gear(a, gm.GearParams(2.0, 22, alpha, 0.1), finalize=True,
                                                             edit=True))
    log('done')
    print(open(OUT, encoding='utf-8').read())
