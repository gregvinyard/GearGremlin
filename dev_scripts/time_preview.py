"""Measure what one dialog input change costs, uncached, split into gearmath and Fusion time.
Run through the Fusion MCP server; creates a NEW scratch design. Results also go to tests/out/time_preview.txt.

A: gear command, the ring of a 20/10/40 x3 planetary set (sun and planets already made), Mesh with = planet 1.
   One input change = the tooth-height note + one preview (make_gear without finalize).
B: planetary helper on the same borderline set: rules + suggestions + preview drawing.
Each change uses a new backlash value, so nothing is served from cache.
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
OUT = os.path.join(ROOT, 'tests', 'out', 'time_preview.txt')


def load():
    for name in [m for m in sys.modules if m == PKG or m.startswith(PKG + '.')]:
        del sys.modules[name]
    for name, path in [(PKG, ROOT), (PKG + '.commands', os.path.join(ROOT, 'commands'))]:
        mod = types.ModuleType(name)
        mod.__path__ = [path]
        sys.modules[name] = mod
    return (importlib.import_module(PKG + '.commands.gearProfile.drawing'),
            importlib.import_module(PKG + '.commands.planetary.layout'),
            importlib.import_module(PKG + '.commands.common.inputs'))


def run(context):
    app = adsk.core.Application.get()
    dr, lay, ci = load()
    gm = dr.gm
    alpha = math.radians(20)
    lines = []

    # gearmath time: wrap the gearmath entry points the Fusion layer calls.
    spent = {'gm': 0.0}
    depth = {'n': 0}

    def timed(fn):
        def w(*a, **k):
            depth['n'] += 1
            t = time.perf_counter()
            try:
                return fn(*a, **k)
            finally:
                depth['n'] -= 1
                if depth['n'] == 0:
                    spent['gm'] += time.perf_counter() - t
        return w
    for name in ('gear_radii', 'pair_report', 'check_params', 'build_profile', 'check_mesh', 'align_theta0',
                 'trim_failure_message', 'factor_range', 'pair_contact_ratio', 'check_planetary',
                 'suggest_planetary', 'factor_max', 'mesh_error', 'same_system'):
        setattr(gm, name, timed(getattr(gm, name)))

    app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    root = adsk.fusion.Design.cast(app.activeProduct).rootComponent
    res = gm.check_planetary(2.0, alpha, 1.0, 0.05, 20, 10, 3)
    sk = root.sketches.add(root.xYConstructionPlane)
    drawn = lay.draw_planetary(sk.originPoint, res)
    dr.make_gear(drawn['sun'], res.sun, finalize=True)
    for p in drawn['planets']:
        dr.make_gear(p, res.planet, partner=drawn['sun'], finalize=True)

    lines.append('A: ring preview in a 20/10/40 x3 set (Mesh with = planet 1), per input change:')
    for i, b in enumerate((0.06, 0.07, 0.08)):
        ring = gm.GearParams(2.0, 40, alpha, b, gm.INTERNAL, 1.0)
        planet = dr.read_gear(drawn['planets'][0]).params
        spent['gm'] = 0.0
        t = time.perf_counter()
        ci.height_note(ring, planet)
        t_note = time.perf_counter() - t
        gm_note = spent['gm']
        spent['gm'] = 0.0
        t = time.perf_counter()
        r = dr.make_gear(drawn['ring'], ring, partner=drawn['planets'][0], finalize=False)
        t_prev = time.perf_counter() - t
        gm_prev = spent['gm']
        coll = adsk.core.ObjectCollection.create()
        for e in r.entities:
            coll.add(e)
        root.parentDesign.deleteEntities(coll)
        lines.append(f'  change {i + 1}: total {t_note + t_prev:.2f} s = note {t_note:.2f} s (gearmath {gm_note:.2f}) '
                     f'+ preview {t_prev:.2f} s (gearmath {gm_prev:.2f}, Fusion {t_prev - gm_prev:.2f})')

    lines.append('B: Planetary Set helper on 20/10/40 x3, per input change:')
    for i, b in enumerate((0.06, 0.07, 0.08)):
        spent['gm'] = 0.0
        t = time.perf_counter()
        r = gm.check_planetary(2.0, alpha, 1.0, b, 20, 10, 3)
        t_rules = time.perf_counter() - t
        t = time.perf_counter()
        gm.suggest_planetary(2.0, alpha, 1.0, b, 20, 10, 3)
        t_sugg = time.perf_counter() - t
        sk2 = root.sketches.add(root.xZConstructionPlane)
        t = time.perf_counter()
        lay.draw_planetary(sk2.originPoint, r)
        t_draw = time.perf_counter() - t
        sk2.deleteMe()
        lines.append(f'  change {i + 1}: total {t_rules + t_sugg + t_draw:.2f} s = rules {t_rules:.2f} + '
                     f'suggestions {t_sugg:.2f} (both gearmath) + preview drawing {t_draw:.2f} (Fusion)')
    report = '\n'.join(lines)
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(report)
    print(report)
