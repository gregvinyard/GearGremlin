"""Fusion-side test of the planetary helper and the gear command on a planetary set. Run through the
Fusion MCP server. Creates a NEW scratch design document. Results also go to tests/out/test_planetary.txt.

For each case: draw the set with layout.draw_planetary, make the gears in the recommended order
(sun → planets with Mesh with = sun → ring with Mesh with = planet 1), then check meshing, trim,
and that the sketch has exactly one region per gear plus the free space (no slivers).
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
OUT = os.path.join(ROOT, 'tests', 'out', 'test_planetary.txt')


def load():
    for name in [m for m in sys.modules if m == PKG or m.startswith(PKG + '.')]:
        del sys.modules[name]
    for name, path in [(PKG, ROOT), (PKG + '.commands', os.path.join(ROOT, 'commands')),
                       (PKG + '.commands.gearProfile', os.path.join(ROOT, 'commands', 'gearProfile')),
                       (PKG + '.commands.planetary', os.path.join(ROOT, 'commands', 'planetary'))]:
        mod = types.ModuleType(name)
        mod.__path__ = [path]
        sys.modules[name] = mod
    dr = importlib.import_module(PKG + '.commands.gearProfile.drawing')
    lay = importlib.import_module(PKG + '.commands.planetary.layout')
    return dr, lay


results = []


def check(label, ok, detail=''):
    results.append(f'{"PASS" if ok else "FAIL"}  {label}  {detail}')


def build(dr, lay, root, plane, zs, zp, n, backlash):
    gm = dr.gm
    alpha = math.radians(20)
    res = gm.check_planetary(2.0, alpha, 1.0, backlash, zs, zp, n)
    tag = f'{zs}/{zp}/{res.ring.teeth} x{n} B={backlash}'
    check(f'{tag}: helper rules pass', res.ok, str([(r.label, r.ok, r.detail) for r in res.rules]))
    sk = root.sketches.add(plane)
    t = time.perf_counter()
    drawn = lay.draw_planetary(sk.originPoint, res)
    check(f'{tag}: circles drawn', len(drawn['planets']) == n, f'{(time.perf_counter() - t) * 1000:.0f} ms')
    plan = dr.read_plan(drawn['ring'])
    check(f'{tag}: ring has a plan', plan is not None and plan.role == 'ring' and plan.teeth == res.ring.teeth)

    def gear(circle, params, partner):
        t0 = time.perf_counter()
        r = dr.make_gear(circle, params, partner=partner, finalize=True)
        return r, (time.perf_counter() - t0) * 1000

    r, ms = gear(drawn['sun'], res.sun, None)
    check(f'{tag}: sun', r.ok, f'{r.errors} {ms:.0f} ms')
    for i, p in enumerate(drawn['planets']):
        r, ms = gear(p, res.planet, drawn['sun'])
        check(f'{tag}: planet {i + 1}', r.ok and not any('no longer lines up' in w for w in r.warnings),
              f'{r.errors} {[w for w in r.warnings if "undercut" not in w]} {ms:.0f} ms')
    r, ms = gear(drawn['ring'], res.ring, drawn['planets'][0])
    check(f'{tag}: ring', r.ok and not any('no longer lines up' in w for w in r.warnings),
          f'{r.errors} {r.warnings} {r.infos} {ms:.0f} ms')
    expected_tip = gm.gear_radii(res.ring, (res.planet,)).tip
    check(f'{tag}: ring trimmed per plan', abs(r.profile.radii.tip - expected_tip) < 1e-9 and
          r.profile.radii.tip > res.ring.tip_radius, f'tip {r.profile.radii.tip:.3f} vs nominal {res.ring.tip_radius:.3f}')

    rec_ring = dr.read_gear(drawn['ring'])
    c_ring = dr.circle_center_mm(drawn['ring'])
    errs = []
    for p in drawn['planets']:
        rec = dr.read_gear(p)
        errs.append(gm.mesh_error(rec_ring.params, c_ring, rec_ring.theta0, rec.params, dr.circle_center_mm(p), rec.theta0))
    check(f'{tag}: every planet meshes with the ring', max(errs) < 1e-6, f'max error {max(errs):.1e} teeth')
    check(f'{tag}: ring tip stored', abs(rec_ring.tip_radius - expected_tip) < 1e-9)
    areas = sorted(round(sk.profiles.item(i).areaProperties().area * 100, 2) for i in range(sk.profiles.count))
    if backlash > 0:
        check(f'{tag}: regions = sun + planets + free space', sk.profiles.count == n + 2,
              f'count={sk.profiles.count} smallest areas mm2={areas[:4]}')
    else:
        # At zero backlash conjugate flanks touch by definition, so Fusion may split the free space
        # into pockets between touching tooth pairs. Recorded for the report, not judged.
        results.append(f'INFO  {tag}: regions {sk.profiles.count} (expected {n + 2} without flank contact), '
                       f'smallest areas mm2={areas[:4]}')


def run(context):
    if os.path.exists(OUT):
        os.remove(OUT)
    app = adsk.core.Application.get()
    dr, lay = load()
    app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    design = adsk.fusion.Design.cast(app.activeProduct)
    root = design.rootComponent
    build(dr, lay, root, root.xYConstructionPlane, 20, 10, 3, 0.05)
    build(dr, lay, root, root.xZConstructionPlane, 20, 10, 3, 0.0)
    build(dr, lay, root, root.yZConstructionPlane, 24, 18, 4, 0.05)
    app.activeViewport.fit()
    judged = [r for r in results if not r.startswith('INFO')]
    report = '\n'.join(results) + f'\n{sum(r.startswith("PASS") for r in judged)}/{len(judged)} passed'
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(report)
    print(report)
