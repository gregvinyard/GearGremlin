"""Fusion-side test of rack-and-pinion meshing (make_gear with a rack partner, make_rack with a gear
partner). Run through the Fusion MCP server.

Creates a NEW scratch design document (never touches existing ones) and prints PASS/FAIL lines.
Results also go to tests/out/test_rack_mesh.txt.
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
OUT = os.path.join(ROOT, 'tests', 'out', 'test_rack_mesh.txt')


def load_rack_drawing():
    """Import the add-in code fresh under a private package name, so it never collides with the loaded add-in."""
    for name in [m for m in sys.modules if m == PKG or m.startswith(PKG + '.')]:
        del sys.modules[name]
    for name, path in [(PKG, ROOT), (PKG + '.commands', os.path.join(ROOT, 'commands')),
                       (PKG + '.commands.gearProfile', os.path.join(ROOT, 'commands', 'gearProfile'))]:
        mod = types.ModuleType(name)
        mod.__path__ = [path]
        sys.modules[name] = mod
    return importlib.import_module(PKG + '.commands.gearProfile.rack_drawing')


results = []


def check(label, ok, detail=''):
    results.append(f'{"PASS" if ok else "FAIL"}  {label}  {detail}')


def P(x, y):
    return adsk.core.Point3D.create(x / 10, y / 10, 0)


def live_error(rd, circle, line):
    """Mesh error (teeth) of a gear and a rack, from their records and live geometry."""
    gm, dr = rd.gm, rd.drawing
    gear = dr.read_gear(circle)
    pose = rd.rack_pose(line, rd.read_rack(line))
    return gm.rack_mesh_error(gear.params, dr.circle_center_mm(circle), gear.theta0, pose)


def tangent_gap(rd, circle, line):
    """|distance from the gear's center to the rack's line − its pitch radius|, in mm."""
    gm, dr = rd.gm, rd.drawing
    pose = rd.rack_pose(line, rd.read_rack(line))
    _, h = gm.rack_contact(pose, dr.circle_center_mm(circle))
    return abs(h - dr.circle_radius_mm(circle))


def run(context):
    app = adsk.core.Application.get()
    rd = load_rack_drawing()
    gm, dr = rd.gm, rd.drawing
    alpha = math.radians(20)
    rack_p = gm.RackParams(2.0, alpha, 0.05)

    app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    design = adsk.fusion.Design.cast(app.activeProduct)
    root = design.rootComponent

    # --- rack first, then a gear meshed with it (Mesh with = the rack's line) -----------------
    sk = root.sketches.add(root.xYConstructionPlane)
    cons = sk.geometricConstraints
    line = sk.sketchCurves.sketchLines.addByTwoPoints(P(-60, 0), P(60, 0))
    circle = sk.sketchCurves.sketchCircles.addByCenterRadius(P(7, 13), 1.3)
    cons.addTangent(circle, line)
    rr = rd.make_rack(line, rack_p, gm.LEFT, 0.0, 6.0, finalize=True)
    check('rack made', rr.ok, str(rr.errors))
    ends = (line.startSketchPoint.geometry, line.endSketchPoint.geometry)
    rg = dr.make_gear(circle, gm.GearParams(2.0, 13, alpha, 0.05), partner=line, finalize=True)
    check('gear meshed with rack', rg.ok, str(rg.errors + rg.warnings))
    check('rack line held still during the gear resize',
          line.startSketchPoint.geometry.distanceTo(ends[0]) < 1e-9 and line.endSketchPoint.geometry.distanceTo(ends[1]) < 1e-9)
    check('gear still tangent to the rack line', tangent_gap(rd, circle, line) < 1e-6, f'{tangent_gap(rd, circle, line):.2e}')
    err = live_error(rd, circle, line)
    check('gear aligned to rack (live geometry)', err < 1e-6, f'{err:.2e} teeth')
    check('contact ratio reported', any(i.startswith('Contact ratio') for i in rg.infos), str(rg.infos))
    check('gear record points at the rack', dr.read_gear(circle).mesh_with == rd.read_rack(line).rack_id)
    check('two separate regions (teeth don\'t touch)', sk.profiles.count == 2, f'{sk.profiles.count}')

    # Edits: shifting the rack's offset puts the gear out of phase; re-meshing the gear fixes it.
    r_edit = rd.make_rack(line, rack_p, gm.LEFT, 1.0, 6.0, finalize=True, edit=True)
    check('rack offset edit warns about the gear', r_edit.ok and any('13-tooth gear meshed with this rack' in w
                                                                     for w in r_edit.warnings), str(r_edit.warnings))
    g_edit = dr.make_gear(circle, gm.GearParams(2.0, 13, alpha, 0.05), partner=line, finalize=True, edit=True)
    check('re-meshing the gear lines it up again', g_edit.ok and live_error(rd, circle, line) < 1e-6
          and not any('rack meshed' in w for w in g_edit.warnings), str(g_edit.warnings))
    g_edit = dr.make_gear(circle, gm.GearParams(2.0, 13, alpha, 0.05), partner=line, rotation_offset=0.05,
                          finalize=True, edit=True)
    check('rotating the gear warns about the rack', any('rack meshed with this gear' in w for w in g_edit.warnings),
          str(g_edit.warnings))
    dr.make_gear(circle, gm.GearParams(2.0, 13, alpha, 0.05), partner=line, finalize=True, edit=True)

    # --- gear first, then a rack meshed with it: right side, on a 30° line -----------------
    s2 = root.sketches.add(root.xYConstructionPlane)
    g2 = s2.sketchCurves.sketchCircles.addByCenterRadius(P(0, 0), 1.7)
    rg2 = dr.make_gear(g2, gm.GearParams(2.0, 17, alpha, 0.05), rotation_offset=0.3, finalize=True)
    a = math.radians(30)
    # The line runs at 30° with the gear on its right: offset from the center by r along the line's left normal.
    nx, ny = -math.sin(a), math.cos(a)
    cx, cy = 17.0 * nx, 17.0 * ny
    l2 = s2.sketchCurves.sketchLines.addByTwoPoints(P(cx - 40 * math.cos(a), cy - 40 * math.sin(a)),
                                                     P(cx + 40 * math.cos(a), cy + 40 * math.sin(a)))
    s2.geometricConstraints.addTangent(g2, l2)
    rr2 = rd.make_rack(l2, rack_p, gm.RIGHT, 0.0, 6.0, finalize=True, partner=g2)
    check('rack meshed with gear (30°, right side)', rg2.ok and rr2.ok, str(rr2.errors + rr2.warnings))
    err = live_error(rd, g2, l2)
    check('rack aligned to gear (live geometry)', err < 1e-6, f'{err:.2e} teeth')
    check('rack record points at the gear', rd.read_rack(l2).mesh_with == dr.read_gear(g2).gear_id)
    check('two separate regions', s2.profiles.count == 2, f'{s2.profiles.count}')
    r_off = rd.make_rack(l2, rack_p, gm.RIGHT, 0.4, 6.0, finalize=True, edit=True, partner=g2)
    check('offset on a meshed rack is added after alignment',
          r_off.ok and abs(rd.read_rack(l2).tooth_phase - rd.read_rack(l2).offset - (r_off.rack.offset - 0.4)) < 1e-9
          and live_error(rd, g2, l2) > 0.01, f'{live_error(rd, g2, l2):.3f} teeth')
    rd.make_rack(l2, rack_p, gm.RIGHT, 0.0, 6.0, finalize=True, edit=True, partner=g2)

    # --- errors and warnings ----------------------------------------------------------------
    s3 = root.sketches.add(root.xZConstructionPlane)
    l3 = s3.sketchCurves.sketchLines.addByTwoPoints(P(-50, 0), P(50, 0))
    rd.make_rack(l3, rack_p, gm.LEFT, 0.0, 6.0, finalize=True)
    below = s3.sketchCurves.sketchCircles.addByCenterRadius(P(0, -12), 1.2)
    r = dr.make_gear(below, gm.GearParams(2.0, 12, alpha, 0.05), partner=l3, finalize=True)
    check('gear on the backing side: clear error', not r.ok and 'wrong side' in r.errors[0], str(r.errors))
    other = s3.sketchCurves.sketchCircles.addByCenterRadius(P(30, 12.5), 1.25)
    r = dr.make_gear(other, gm.GearParams(2.5, 10, alpha, 0.05), partner=l3, finalize=True)
    check('module mismatch: clear error', not r.ok and 'Module' in r.errors[0], str(r.errors))
    loose = s3.sketchCurves.sketchCircles.addByCenterRadius(P(-30, 14), 1.2)
    r = dr.make_gear(loose, gm.GearParams(2.0, 12, alpha, 0.05), partner=l3, finalize=True)
    check('not tangent: warning', r.ok and any("isn't tangent" in w for w in r.warnings), str(r.warnings))
    plain = s3.sketchCurves.sketchLines.addByTwoPoints(P(-50, 40), P(50, 40))
    lonely = s3.sketchCurves.sketchCircles.addByCenterRadius(P(0, 52), 1.2)
    r = dr.make_gear(lonely, gm.GearParams(2.0, 12, alpha, 0.05), partner=plain, finalize=True)
    check('a plain line as Mesh with: clear error', not r.ok and 'not a gear made by this tool' in r.errors[0],
          str(r.errors))
    ring_c = s3.sketchCurves.sketchCircles.addByCenterRadius(P(0, 100), 4.0)
    rl = s3.sketchCurves.sketchLines.addByTwoPoints(P(-30, 60), P(30, 60))
    rd.make_rack(rl, rack_p, gm.LEFT, 0.0, 6.0, finalize=True)
    r = dr.make_gear(ring_c, gm.GearParams(2.0, 40, alpha, 0.05, gm.INTERNAL), partner=rl, finalize=True)
    check('internal gear with a rack: clear error', not r.ok and 'external' in r.errors[0], str(r.errors))

    app.activeViewport.fit()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    summary = f'{sum(r.startswith("PASS") for r in results)}/{len(results)} passed'
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(results + [summary]) + '\n')
    print('\n'.join(results))
    print(summary)
