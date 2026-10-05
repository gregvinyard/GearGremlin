"""Fusion-side test of commands/gearProfile/rack_drawing.py. Run through the Fusion MCP server.

Creates a NEW scratch design document (never touches existing ones), draws lines, makes racks
with rack_drawing.make_rack, and prints checks as PASS/FAIL lines. Results also go to
tests/out/test_rack.txt.
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
OUT = os.path.join(ROOT, 'tests', 'out', 'test_rack.txt')


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


def add_line(sketch, x, y, angle_deg, length):
    a = math.radians(angle_deg)
    return sketch.sketchCurves.sketchLines.addByTwoPoints(
        P(x, y), P(x + length * math.cos(a), y + length * math.sin(a)))


def curve_tokens(sketch):
    curves = sketch.sketchCurves
    return {curves.item(i).entityToken for i in range(curves.count)}


def profiles_using(sketch, tokens):
    out = []
    for i in range(sketch.profiles.count):
        p = sketch.profiles.item(i)
        loops = p.profileLoops
        found = set()
        for j in range(loops.count):
            pcs = loops.item(j).profileCurves
            for k in range(pcs.count):
                found.add(pcs.item(k).sketchEntity.entityToken)
        if found & tokens:
            out.append(p)
    return out


def area_mm2(profiles):
    return sum(p.areaProperties().area * 100 for p in profiles)


def volume_mm3(feature):
    return sum(feature.bodies.item(i).volume for i in range(feature.bodies.count)) * 1000


def extrude(root, profiles, height='5 mm'):
    if len(profiles) == 1:
        value = profiles[0]
    else:
        value = adsk.core.ObjectCollection.create()
        for p in profiles:
            value.add(p)
    return root.features.extrudeFeatures.addSimple(value, adsk.core.ValueInput.createByString(height),
                                                   adsk.fusion.FeatureOperations.NewBodyFeatureOperation)


def rack_heights(rd, sketch, line):
    """(max h, min h) over the rack's curve endpoints, measured from sketch geometry."""
    gm = rd.gm
    rec = rd.read_rack(line)
    hs = []
    for c in rd.rack_parts(sketch, rec.rack_id):
        for pt in (c.startSketchPoint, c.endSketchPoint):
            g = pt.geometry
            hs.append(gm.rack_coords(rec.origin, rec.direction, rec.side, (rd.to_mm(g.x), rd.to_mm(g.y)))[1])
    return max(hs), min(hs)


def tip_lines(rd, sketch, rack_id):
    return [c for c, _ in rd.drawing._tagged(sketch, rack_id)]


def teeth_side_ok(rd, sketch, line, side):
    """Every tagged top land lies on the chosen side of the line's start → end direction (from geometry)."""
    origin, direction, _ = rd.line_frame(line)
    u = (math.cos(direction), math.sin(direction))
    rec = rd.read_rack(line)
    signs = []
    for c in tip_lines(rd, sketch, rec.rack_id):
        a, b = c.startSketchPoint.geometry, c.endSketchPoint.geometry
        mx, my = rd.to_mm((a.x + b.x) / 2) - origin[0], rd.to_mm((a.y + b.y) / 2) - origin[1]
        signs.append(u[0] * my - u[1] * mx)
    return bool(signs) and all(s * side > 0 for s in signs), len(signs)


def run(context):
    app = adsk.core.Application.get()
    rd = load_rack_drawing()
    gm, dr = rd.gm, rd.drawing

    app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    design = adsk.fusion.Design.cast(app.activeProduct)
    root = design.rootComponent

    combos = [gm.RackParams(2.0, math.radians(20), 0.05, 1.0),
              gm.RackParams(1.5, math.radians(14.5), 0.1, 0.8),
              gm.RackParams(3.0, math.radians(25), 0.0, 1.0),      # full-round root
              gm.RackParams(1.0, math.radians(20), 0.2, 1.25)]

    # --- directions × sides × parameter combos, resize on -------------------------------
    sk = root.sketches.add(root.xYConstructionPlane)
    n = 0
    for angle in (0, 90, 30, 137):
        for side in (gm.LEFT, gm.RIGHT):
            params = combos[n % len(combos)]
            body = 3 * params.module
            x, y = (n % 4) * 120.0, (n // 4) * 120.0
            line = add_line(sk, x, y, angle, 47.3)
            origin0, dir0, len0 = rd.line_frame(line)
            before = curve_tokens(sk)
            label = f'{angle}deg {"L" if side == 1 else "R"} m{params.module:g}'
            r = rd.make_rack(line, params, side, 0.0, body, resize=True, finalize=True)
            check(f'{label}: ok', r.ok, str(r.errors + r.warnings))
            if not r.ok:
                n += 1
                continue
            origin, direction, length = rd.line_frame(line)
            teeth = gm.rack_teeth_for_length(len0, params)
            check(f'{label}: length N*p', abs(length - teeth * params.pitch) < 1e-4, f'{length:.5f} vs {teeth}*p')
            check(f'{label}: start unmoved, direction kept',
                  math.dist(origin, origin0) < 1e-6 and abs(gm._wrap(direction - dir0)) < 1e-9)
            ok, count = teeth_side_ok(rd, sk, line, side)
            check(f'{label}: teeth on chosen side', ok and count == teeth, f'{count} tips')
            hi, lo = rack_heights(rd, sk, line)
            check(f'{label}: tip and back heights', abs(hi - params.addendum) < 1e-6
                  and abs(lo + params.dedendum + body) < 1e-6, f'{hi:.4f}, {lo:.4f}')
            new = curve_tokens(sk) - before
            profs = profiles_using(sk, new)
            expected = abs(gm.profile_area(r.rack.segments))
            check(f'{label}: one region, area matches', len(profs) == 1 and abs(area_mm2(profs) - expected) < 1e-3,
                  f'{len(profs)} regions, {area_mm2(profs):.4f} vs {expected:.4f} mm2')
            check(f'{label}: pitch line construction', line.isConstruction)
            n += 1

    # --- extrude one ---------------------------------------------------------------------
    first = rd.rack_lines(sk)[0]
    rec = rd.read_rack(first)
    profs = profiles_using(sk, {c.entityToken for c in rd.rack_parts(sk, rec.rack_id)})
    ext = extrude(root, profs)
    check('rack extrudes to one body', ext.bodies.count == 1 and
          abs(volume_mm3(ext) - area_mm2(profs) * 5) < 1e-2, f'{volume_mm3(ext):.2f} mm3')

    # --- resize off, open outline, offset ---------------------------------------------------
    s2 = root.sketches.add(root.xZConstructionPlane)
    p20 = combos[0]
    line = add_line(s2, 0, 0, 0, 47.0)
    before = curve_tokens(s2)
    r = rd.make_rack(line, p20, gm.LEFT, 0.0, 0.0, resize=False, finalize=True)
    _, _, length = rd.line_frame(line)
    teeth = len(gm.rack_tooth_centers(47.0, p20))
    check('resize off: line untouched', abs(length - 47.0) < 1e-9, f'{length:.6f}')
    check('resize off: leftover warned', any('has no teeth' in w for w in r.warnings), str(r.warnings))
    check('resize off: whole teeth only', len(tip_lines(rd, s2, rd.read_rack(line).rack_id)) == teeth == 7)
    check('open outline: no new region', not profiles_using(s2, curve_tokens(s2) - before),
          f'{len(profiles_using(s2, curve_tokens(s2) - before))}')
    check('open outline: info note', any("won't extrude" in i for i in r.infos))
    line = add_line(s2, 0, 60, 0, 47.0)
    r = rd.make_rack(line, p20, gm.LEFT, 1.0, 5.0, resize=True, finalize=True)
    check('offset on N*p line drops one tooth', r.ok and len(r.rack.tooth_centers) == 6,
          f'{len(r.rack.tooth_centers) if r.rack else None}')

    # --- constraints -----------------------------------------------------------------------------
    s3 = root.sketches.add(root.xYConstructionPlane)
    dims = s3.sketchDimensions
    aligned = adsk.fusion.DimensionOrientations.AlignedDimensionOrientation
    horizontal = adsk.fusion.DimensionOrientations.HorizontalDimensionOrientation

    line = add_line(s3, 0, 0, 20, 40.0)
    dims.addDistanceDimension(line.startSketchPoint, line.endSketchPoint, aligned, P(10, 15), True)
    count = dims.count
    r = rd.make_rack(line, p20, gm.LEFT, 0.0, 5.0, finalize=True)
    _, _, length = rd.line_frame(line)
    check('existing aligned dimension reused', r.ok and dims.count == count and abs(length - 6 * p20.pitch) < 1e-4,
          f'{dims.count} dims, {length:.4f}')

    line = add_line(s3, 0, 60, 0, 40.0)
    s3.geometricConstraints.addHorizontal(line)
    r = rd.make_rack(line, p20, gm.RIGHT, 0.0, 5.0, finalize=True)
    _, direction, length = rd.line_frame(line)
    check('horizontal-constrained line resizes', r.ok and abs(length - 6 * p20.pitch) < 1e-4 and abs(direction) < 1e-9,
          str(r.errors))

    line = add_line(s3, 0, 120, 0, 40.0)
    line.endSketchPoint.isFixed = True
    before, dcount = curve_tokens(s3), dims.count
    r = rd.make_rack(line, p20, gm.LEFT, 0.0, 5.0, finalize=True)
    check('fixed end: clear error, sketch unchanged', not r.ok and 'fixed' in r.errors[0]
          and curve_tokens(s3) == before and dims.count == dcount, str(r.errors))

    line = add_line(s3, 0, 180, 30, 40.0)
    dims.addDistanceDimension(line.startSketchPoint, line.endSketchPoint, horizontal, P(10, 200), True)
    before, dcount = curve_tokens(s3), dims.count
    _, dir_before, len_before = rd.line_frame(line)
    r = rd.make_rack(line, p20, gm.LEFT, 0.0, 5.0, finalize=True)
    _, dir_after, len_after = rd.line_frame(line)
    check('diagonal line with horizontal dimension: clear error, sketch unchanged',
          not r.ok and 'turn this line' in r.errors[0] and curve_tokens(s3) == before and dims.count == dcount
          and abs(len_after - len_before) < 1e-6 and abs(dir_after - dir_before) < 1e-9,
          f'{r.errors} dims {dcount}->{dims.count}, length {len_before:.4f}->{len_after:.4f}')
    check('no part attributes left by the failed rack', rd.read_rack(line) is None)

    line = add_line(s3, 0, 240, 0, 40.0)
    other = s3.sketchCurves.sketchLines.addByTwoPoints(line.endSketchPoint, P(40, 270))
    r = rd.make_rack(line, p20, gm.LEFT, 0.0, 5.0, finalize=True)
    _, _, length = rd.line_frame(line)
    check('end shared with another line: resizes, other line follows',
          r.ok and abs(length - 6 * p20.pitch) < 1e-4 and other.startSketchPoint == line.endSketchPoint, str(r.errors))

    # Midpoint held by alignment to the sketch origin (reported by the user): the line can only
    # grow about its midpoint, so the start has to move.
    cons = s3.geometricConstraints
    for label, start, end, align in (('horizontal', (-31.57, 300), (31.57, 300), cons.addVerticalPoints),
                                     ('vertical', (300, -31.57), (300, 31.57), cons.addHorizontalPoints)):
        line = s3.sketchCurves.sketchLines.addByTwoPoints(P(*start), P(*end))
        (cons.addHorizontal if label == 'horizontal' else cons.addVertical)(line)
        mid = s3.sketchPoints.add(P((start[0] + end[0]) / 2, (start[1] + end[1]) / 2))
        cons.addMidPoint(mid, line)
        align(mid, s3.originPoint)
        mid_before = mid.geometry
        _, dir_before, _ = rd.line_frame(line)
        r = rd.make_rack(line, p20, gm.LEFT, 0.0, 5.0, finalize=True)
        _, direction, length = rd.line_frame(line)
        check(f'midpoint aligned to origin ({label}): resizes about the midpoint',
              r.ok and abs(length - 10 * p20.pitch) < 1e-4 and mid.geometry.distanceTo(mid_before) < 1e-7
              and abs(direction - dir_before) < 1e-9, f'{r.errors} {length:.4f} mm')
    line = s3.sketchCurves.sketchLines.addByTwoPoints(P(-31.57, 360), P(31.57, 360))
    cons.addHorizontal(line)
    mid = s3.sketchPoints.add(P(0, 360))
    cons.addMidPoint(mid, line)
    cons.addVerticalPoints(mid, s3.originPoint)
    line.startSketchPoint.isFixed = True
    before, dcount = curve_tokens(s3), dims.count
    start_before, end_before = line.startSketchPoint.geometry, line.endSketchPoint.geometry
    r = rd.make_rack(line, p20, gm.LEFT, 0.0, 5.0, finalize=True)
    check('midpoint aligned and start fixed: clear error, sketch unchanged',
          not r.ok and 'constraints hold' in r.errors[0] and curve_tokens(s3) == before and dims.count == dcount
          and line.startSketchPoint.geometry.distanceTo(start_before) < 1e-9
          and line.endSketchPoint.geometry.distanceTo(end_before) < 1e-9, str(r.errors))

    # --- short, long ----------------------------------------------------------------------------
    s4 = root.sketches.add(root.yZConstructionPlane)
    short = add_line(s4, 0, 0, 0, 5.0)
    r = rd.make_rack(short, p20, gm.LEFT, 0.0, 5.0, resize=False, finalize=True)
    check('short line: clear error', not r.ok and 'shorter than one tooth' in r.errors[0], str(r.errors))
    r = rd.make_rack(short, p20, gm.LEFT, 0.0, 5.0, resize=True, finalize=True)
    check('short line, resize on: one tooth', r.ok and len(r.rack.tooth_centers) == 1, str(r.errors))
    too_long = add_line(s4, 0, 40, 0, 401 * math.pi)
    r = rd.make_rack(too_long, gm.RackParams(1.0, math.radians(20), 0.05), finalize=True)
    check('more than 400 teeth: clear error', not r.ok and 'at most' in r.errors[0], str(r.errors))

    s5 = root.sketches.add(root.xYConstructionPlane)
    long_params = gm.RackParams(1.0, math.radians(20), 0.05)
    preview_line = add_line(s5, 0, 0, 0, 400 * math.pi)
    t = time.time()
    rp = rd.make_rack(preview_line, long_params, gm.LEFT, 0.0, 3.0, finalize=False)
    t_preview = time.time() - t
    long_line = add_line(s5, 0, 100, 0, 400 * math.pi)
    t = time.time()
    rl = rd.make_rack(long_line, long_params, gm.LEFT, 0.0, 3.0, finalize=True)
    t_execute = time.time() - t
    check('400-tooth rack', rp.ok and rl.ok and len(rl.rack.tooth_centers) == 400,
          f'preview-style {t_preview:.1f} s, execute {t_execute:.1f} s')

    # --- guards -------------------------------------------------------------------------------------
    rack_line = rd.rack_lines(s2)[0]
    r = rd.make_rack(rack_line, p20, finalize=True)
    check('second rack on the same line blocked', not r.ok and 'already a rack' in r.errors[0], str(r.errors))
    rec = rd.read_rack(rack_line)
    tip = tip_lines(rd, s2, rec.rack_id)[0]
    r = rd.make_rack(adsk.fusion.SketchLine.cast(tip), p20, finalize=True)
    check('a top land is not a pitch line', not r.ok and 'part of a gear' in r.errors[0], str(r.errors))
    flank = next(c for c in rd.rack_parts(s2, rec.rack_id)
                 if adsk.fusion.SketchLine.cast(c) is not None and c.attributes.itemByName('GearGremlin', 'part') is None)
    owner = rd.owner_for_entity(flank)
    check('untagged rack line resolves to its rack', owner is not None and owner[0] == 'rack' and owner[1] == rack_line)
    r = rd.make_rack(adsk.fusion.SketchLine.cast(flank), p20, finalize=True)
    check('an untagged rack line is not a pitch line', not r.ok and 'part of a gear' in r.errors[0], str(r.errors))
    arc = next(c for c in rd.rack_parts(s2, rec.rack_id) if adsk.fusion.SketchArc.cast(c) is not None)
    owner = rd.owner_for_entity(arc)
    check('rack fillet arc resolves to its rack', owner is not None and owner[1] == rack_line)

    # --- edit -----------------------------------------------------------------------------------------
    s6 = root.sketches.add(root.xYConstructionPlane)
    line = add_line(s6, 0, 0, 15, 50.0)
    r = rd.make_rack(line, p20, gm.LEFT, 0.0, 6.0, finalize=True)
    rec = rd.read_rack(line)
    rack_id = rec.rack_id
    # A user rectangle around the rack: regions are the rack and the frame around it.
    lines = s6.sketchCurves.sketchLines
    rect = lines.addTwoPointRectangle(P(-20, -30), P(70, 40))
    rect_tokens = {rect.item(i).entityToken for i in range(rect.count)}
    rack_tokens = {c.entityToken for c in rd.rack_parts(s6, rack_id)}
    rack_only = []
    frame = []
    for p in profiles_using(s6, rack_tokens):
        loops = p.profileLoops
        toks = set()
        for j in range(loops.count):
            pcs = loops.item(j).profileCurves
            for k in range(pcs.count):
                toks.add(pcs.item(k).sketchEntity.entityToken)
        (frame if toks & rect_tokens else rack_only).append(p)
    check('edit setup: rack region and frame region', len(rack_only) == 1 and len(frame) == 1,
          f'{len(rack_only)}, {len(frame)}')
    ext_rack = extrude(root, rack_only, '4 mm')
    ext_frame = extrude(root, frame, '2 mm')
    rect_area = 90 * 70
    for step, (params, side, offset, body) in enumerate([
            (gm.RackParams(2.5, math.radians(20), 0.05), gm.LEFT, 0.0, 6.0),
            (gm.RackParams(2.5, math.radians(20), 0.05), gm.RIGHT, 0.0, 6.0),
            (gm.RackParams(2.5, math.radians(20), 0.05), gm.RIGHT, 1.7, 4.0)]):
        before = curve_tokens(s6)
        old_parts = {c.entityToken for c in rd.rack_parts(s6, rack_id)}
        r = rd.make_rack(line, params, side, offset, body, resize=True, finalize=True, edit=True)
        label = f'edit {step + 1} (m{params.module:g}, {"L" if side == 1 else "R"}, offset {offset:g}, T {body:g})'
        check(f'{label}: ok', r.ok, str(r.errors + r.warnings))
        rec = rd.read_rack(line)
        check(f'{label}: id unchanged', rec.rack_id == rack_id)
        now = curve_tokens(s6)
        new_parts = {c.entityToken for c in rd.rack_parts(s6, rack_id)}
        check(f'{label}: old curves gone, no orphans',
              not (old_parts & now) and now == (before - old_parts) | {e.entityToken for e in r.entities}
              and new_parts == {e.entityToken for e in r.entities}, f'{len(now)} curves')
        tags = [a for a in design.findAttributes('GearGremlin', 'part') if a.value == rack_id]
        check(f'{label}: one tag per tooth', len(tags) == len(r.rack.tooth_centers), f'{len(tags)}')
        ok, _ = teeth_side_ok(rd, s6, line, side)
        check(f'{label}: teeth on chosen side', ok)
        area = abs(gm.profile_area(r.rack.segments))
        check(f'{label}: features re-pointed', r.repointed == [ext_rack.name, ext_frame.name] and not r.not_repointed,
              f'{r.repointed} / {r.not_repointed}')
        check(f'{label}: rack extrude healthy, volume follows',
              ext_rack.healthState == adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState
              and abs(volume_mm3(ext_rack) - area * 4) < 0.05, f'{volume_mm3(ext_rack):.2f} vs {area * 4:.2f}')
        check(f'{label}: frame extrude healthy, volume follows',
              ext_frame.healthState == adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState
              and abs(volume_mm3(ext_frame) - (rect_area - area) * 2) < 0.05,
              f'{volume_mm3(ext_frame):.2f} vs {(rect_area - area) * 2:.2f}')
    if dr.repoint_errors:
        results.append('INFO  re-point errors: ' + '; '.join(dr.repoint_errors))

    # --- gears and racks in one sketch --------------------------------------------------------------
    s7 = root.sketches.add(root.xZConstructionPlane)
    circle = s7.sketchCurves.sketchCircles.addByCenterRadius(P(0, 30), 2.0)
    rg = dr.make_gear(circle, gm.GearParams(2.0, 20, math.radians(20), 0.05), finalize=True)
    line = add_line(s7, -30, 0, 0, 60.0)
    rr = rd.make_rack(line, p20, gm.LEFT, 0.0, 5.0, finalize=True)
    check('gear and rack both made', rg.ok and rr.ok, str(rg.errors + rr.errors))
    gear_id = dr.read_gear(circle).gear_id
    rack_id = rd.read_rack(line).rack_id
    check('gear_circles: only the circle', dr.gear_circles(s7) == [circle])
    check('rack_lines: only the line', rd.rack_lines(s7) == [line])
    check('find_gear_circle ignores rack ids', dr.find_gear_circle(s7, rack_id) is None)
    rtip = tip_lines(rd, s7, rack_id)[0]
    check('gear_for_entity(rack tip) is None', dr.gear_for_entity(rtip) is None)
    gtip = rg.entities[2]
    check('owner_for_entity: gear tooth -> gear, rack tip -> rack',
          rd.owner_for_entity(gtip) == ('gear', circle) and rd.owner_for_entity(rtip) == ('rack', line))
    gear_parts = {c.entityToken for c in dr.gear_parts(s7, gear_id)}
    rack_parts = {c.entityToken for c in rd.rack_parts(s7, rack_id)}
    check('gear and rack parts disjoint', gear_parts and rack_parts and not gear_parts & rack_parts)
    rg2 = dr.make_gear(circle, gm.GearParams(2.0, 20, math.radians(20), 0.05, height_factor=0.8), finalize=True,
                       edit=True)
    check('editing the gear leaves the rack', rg2.ok and {c.entityToken for c in rd.rack_parts(s7, rack_id)} == rack_parts)
    gear_parts = {c.entityToken for c in dr.gear_parts(s7, gear_id)}
    rr2 = rd.make_rack(line, gm.RackParams(2.0, math.radians(20), 0.05, 0.8), gm.LEFT, 0.0, 5.0, finalize=True,
                       edit=True)
    check('editing the rack leaves the gear', rr2.ok and {c.entityToken for c in dr.gear_parts(s7, gear_id)} == gear_parts)

    app.activeViewport.fit()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    summary = f'{sum(r.startswith("PASS") for r in results)}/{len(results)} passed'
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(results + [summary]) + '\n')
    print('\n'.join(results))
    print(summary)
