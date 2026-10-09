"""Sketch-level operations for racks: plain functions taking a sketch and parameters, like drawing.py.

A rack is drawn along a sketch line (its pitch line). The geometry, frame and tooth phase
conventions are in gearmath (see "Racks" there and docs/rack-spec.md).
"""
import math
import uuid
from dataclasses import dataclass
from typing import Optional

import adsk.core
import adsk.fusion

from ... import gearmath as gm
from . import drawing
from .drawing import ATTR_GROUP, PART_NAME, to_cm, to_mm

RACK_NAME = 'rack'  # on a rack's pitch line; value is the rack record
BAND_TOL_MM = 1e-3


# ---------------------------------------------------------------------------
# Rack metadata
# ---------------------------------------------------------------------------

def read_rack(line) -> Optional[gm.RackRecord]:
    """The RackRecord stored on a line by this tool, or None."""
    line = adsk.fusion.SketchLine.cast(line)
    if line is None:
        return None
    attr = line.attributes.itemByName(ATTR_GROUP, RACK_NAME)
    return gm.from_rack_attribute(attr.value) if attr else None


def write_rack(line: adsk.fusion.SketchLine, record: gm.RackRecord) -> None:
    line.attributes.add(ATTR_GROUP, RACK_NAME, gm.to_rack_attribute(record))


def rack_lines(sketch: adsk.fusion.Sketch) -> list:
    """All lines in the sketch that carry a rack record (one design-wide attribute search)."""
    out = []
    for attr in sketch.parentComponent.parentDesign.findAttributes(ATTR_GROUP, RACK_NAME):
        line = adsk.fusion.SketchLine.cast(attr.parent)
        if line is not None and line.isValid and line.parentSketch == sketch:
            out.append(line)
    return out


def find_rack_line(sketch: adsk.fusion.Sketch, rack_id: str) -> Optional[adsk.fusion.SketchLine]:
    if not rack_id:
        return None
    for line in rack_lines(sketch):
        record = read_rack(line)
        if record and record.rack_id == rack_id:
            return line
    return None


def line_frame(line: adsk.fusion.SketchLine) -> tuple:
    """(start point (mm), direction (rad), length (mm)) of a sketch line, in sketch coordinates."""
    s, e = line.startSketchPoint.geometry, line.endSketchPoint.geometry
    dx, dy = to_mm(e.x - s.x), to_mm(e.y - s.y)
    return (to_mm(s.x), to_mm(s.y)), math.atan2(dy, dx), math.hypot(dx, dy)


def rack_pose(line: adsk.fusion.SketchLine, record: gm.RackRecord) -> gm.RackPose:
    """A rack as it is now, for meshing: the live pitch line, with the phase its teeth were drawn with."""
    origin, direction, length = line_frame(line)
    return gm.RackPose(record.params, origin, direction, record.side, record.tooth_phase, length)


def _meshes(gear: gm.GearParams, center: gm.Point, theta0: float, pose: gm.RackPose) -> tuple:
    """(touching, lined up) for a gear and a rack: tangent on the teeth side within the line, and in
    the same system and phase."""
    s, h = gm.rack_contact(pose, center)
    touching = (abs(h - gear.pitch_radius) <= gm.TANGENCY_TOL_MM
                and -gm.TANGENCY_TOL_MM <= s <= pose.length + gm.TANGENCY_TOL_MM)
    lined_up = (not gm._match_errors(gear, pose.params) and touching
                and gm.rack_mesh_error(gear, center, theta0, pose) <= 1e-6)
    return touching, lined_up


def gear_rack_warnings(sketch: adsk.fusion.Sketch, gear_id: str, params: gm.GearParams, center: gm.Point,
                       theta0: float) -> list:
    """Out-of-phase check for a gear against racks: every rack it touches, plus racks aligned to it."""
    if params.internal:
        return []
    warnings = []
    for line in rack_lines(sketch):
        record = read_rack(line)
        if record is None:
            continue
        touching, lined_up = _meshes(params, center, theta0, rack_pose(line, record))
        dependent = bool(gear_id) and record.mesh_with == gear_id
        if (touching or dependent) and not lined_up:
            warnings.append('The rack meshed with this gear no longer lines up. Edit it to re-align.')
    return list(dict.fromkeys(warnings))


def rack_gear_warnings(sketch: adsk.fusion.Sketch, rack_id: str, pose: gm.RackPose) -> list:
    """Out-of-phase check for a rack against gears: every gear it touches, plus gears aligned to it."""
    warnings = []
    for circle in drawing.gear_circles(sketch):
        record = drawing.read_gear(circle)
        if record is None or record.params.internal:
            continue
        touching, lined_up = _meshes(record.params, drawing.circle_center_mm(circle), record.theta0, pose)
        dependent = bool(rack_id) and record.mesh_with == rack_id
        if (touching or dependent) and not lined_up:
            warnings.append(f'The {record.params.teeth}-tooth gear meshed with this rack no longer lines up. '
                            'Edit it to re-align.')
    return list(dict.fromkeys(warnings))


def in_plane_error(line: adsk.fusion.SketchLine) -> str:
    for pt in (line.startSketchPoint, line.endSketchPoint):
        if abs(pt.geometry.z) > 1e-6:
            return 'The line must lie in the sketch plane.'
    return ''


# ---------------------------------------------------------------------------
# Finding a rack's curves
# ---------------------------------------------------------------------------

def _in_rack_band(curve, record: gm.RackRecord) -> bool:
    """Both endpoints inside the region the rack was drawn in."""
    p = record.params
    lo, hi = -(p.dedendum + record.body) - BAND_TOL_MM, p.addendum + BAND_TOL_MM
    for pt in (curve.startSketchPoint, curve.endSketchPoint):
        g = pt.geometry
        s, h = gm.rack_coords(record.origin, record.direction, record.side, (to_mm(g.x), to_mm(g.y)))
        if not (-BAND_TOL_MM <= s <= record.length + BAND_TOL_MM and lo <= h <= hi):
            return False
    return True


def _own_rack_curve(record: gm.RackRecord, pitch_token: str):
    """A curve that can belong to this rack's outline: an untagged line or arc in its band, or a curve
    tagged with its id. Never the pitch line itself."""
    def accept(curve) -> bool:
        if curve.entityToken == pitch_token:
            return False
        attr = curve.attributes.itemByName(ATTR_GROUP, PART_NAME)
        if attr is not None:
            return attr.value == record.rack_id
        is_rack_type = (adsk.fusion.SketchLine.cast(curve) is not None
                        or adsk.fusion.SketchArc.cast(curve) is not None)
        return is_rack_type and _in_rack_band(curve, record)
    return accept


def rack_parts(sketch: adsk.fusion.Sketch, rack_id: str) -> list:
    """All curves drawn for the rack with this id."""
    tagged = [c for c, _ in drawing._tagged(sketch, rack_id)]
    line = find_rack_line(sketch, rack_id)
    if line is None:
        return tagged
    accept = _own_rack_curve(read_rack(line), line.entityToken)
    parts = {c.entityToken: c for c in tagged}
    walked = set()
    for c in tagged:
        if c.entityToken in walked:
            continue  # reached by an earlier walk: the outline is one connected chain
        for found in drawing._walk(c, accept):
            parts[found.entityToken] = found
            walked.add(found.entityToken)
    return list(parts.values())


def delete_rack_parts(sketch: adsk.fusion.Sketch, rack_id: str) -> None:
    """Delete all of a rack's curves in one operation, tags first (see drawing.delete_gear_parts)."""
    parts = adsk.core.ObjectCollection.create()
    for c in rack_parts(sketch, rack_id):
        attr = c.attributes.itemByName(ATTR_GROUP, PART_NAME)
        if attr:
            attr.deleteMe()
        parts.add(c)
    if parts.count:
        sketch.parentComponent.parentDesign.deleteEntities(parts)


def rack_for_entity(entity) -> Optional[adsk.fusion.SketchLine]:
    """The rack's pitch line, given the line itself or any curve drawn for the rack."""
    curve = adsk.fusion.SketchCurve.cast(entity)
    if curve is None:
        return None
    if read_rack(curve) is not None:
        return adsk.fusion.SketchLine.cast(curve)
    sketch = curve.parentSketch
    attr = curve.attributes.itemByName(ATTR_GROUP, PART_NAME)
    if attr:
        return find_rack_line(sketch, attr.value)
    if adsk.fusion.SketchLine.cast(curve) is None and adsk.fusion.SketchArc.cast(curve) is None:
        return None
    # An untagged rack curve: walk along the outline to the nearest tagged one (a few steps).
    seen = {curve.entityToken}
    frontier = [curve]
    for _ in range(12):
        nxt = []
        for c in frontier:
            for pt in (c.startSketchPoint, c.endSketchPoint):
                ents = pt.connectedEntities
                for j in range(ents.count):
                    other = adsk.fusion.SketchCurve.cast(ents.item(j))
                    if other is None or adsk.fusion.SketchCircle.cast(other) is not None:
                        continue
                    if other.entityToken in seen:
                        continue
                    seen.add(other.entityToken)
                    tag = other.attributes.itemByName(ATTR_GROUP, PART_NAME)
                    if tag:
                        found = find_rack_line(sketch, tag.value)
                        if found is not None and found != curve and _in_rack_band(curve, read_rack(found)):
                            return found
                    nxt.append(other)
        frontier = nxt
        if not frontier:
            break
    return None


def owner_for_entity(entity) -> Optional[tuple]:
    """('gear', pitch circle) or ('rack', pitch line) for a gear or rack made by this tool, given its
    pitch curve or any curve drawn for it; else None."""
    circle = drawing.gear_for_entity(entity)
    if circle is not None:
        return 'gear', circle
    line = rack_for_entity(entity)
    if line is not None:
        return 'rack', line
    return None


# ---------------------------------------------------------------------------
# Resizing the line
# ---------------------------------------------------------------------------

def length_dimension(line: adsk.fusion.SketchLine):
    """The driving dimension that sets this line's length, or None: a linear dimension between its two
    endpoints (or on the line) that is aligned, or horizontal/vertical along a horizontal/vertical line."""
    _, direction, _ = line_frame(line)
    ends = (line.startSketchPoint, line.endSketchPoint)
    candidates = []
    for owner in (line.startSketchPoint, line):
        dims = owner.sketchDimensions
        for i in range(dims.count):
            candidates.append(dims.item(i))
    for d in candidates:
        lin = adsk.fusion.SketchLinearDimension.cast(d)
        if lin is None or not d.isDriving:
            continue
        try:
            one, two = lin.entityOne, lin.entityTwo
        except Exception:
            continue
        on_ends = (one == ends[0] and two == ends[1]) or (one == ends[1] and two == ends[0])
        on_line = one == line and (two is None or two == line)
        if not (on_ends or on_line):
            continue
        orient = lin.orientation
        if orient == adsk.fusion.DimensionOrientations.AlignedDimensionOrientation:
            return d
        if orient == adsk.fusion.DimensionOrientations.HorizontalDimensionOrientation and \
                abs(math.sin(direction)) < 1e-9:
            return d
        if orient == adsk.fusion.DimensionOrientations.VerticalDimensionOrientation and \
                abs(math.cos(direction)) < 1e-9:
            return d
    return None


def line_resize_check(line: adsk.fusion.SketchLine) -> str:
    """A blocking error that's knowable before trying to resize, or ''."""
    if line.isFixed or line.endSketchPoint.isFixed:
        return "The line's end is fixed, so it can't be resized. Unfix it or turn off Resize."
    return ''


def length_expression_warning(line: adsk.fusion.SketchLine) -> str:
    dim = length_dimension(line)
    if dim and not drawing._PLAIN_NUMBER.match(dim.parameter.expression):
        return (f'Line length is set by the expression "{dim.parameter.expression}". '
                'Resize will replace it with a number.')
    return ''


def hold_points(sketch: adsk.fusion.Sketch, line: adsk.fusion.SketchLine) -> list:
    """Points to keep still while this line is resized: gear centers and other racks' pitch lines."""
    points = [c.centerSketchPoint for c in drawing.gear_circles(sketch)]
    for other in rack_lines(sketch):
        if other != line:
            points += [other.startSketchPoint, other.endSketchPoint]
    return points


def resize_line(sketch: adsk.fusion.Sketch, line: adsk.fusion.SketchLine, length_mm: float,
                hold: Optional[list] = None, side: int = gm.LEFT) -> drawing.ResizeResult:
    """Set the line's length to length_mm by moving its end point along the line.

    The points in `hold` are fixed throughout. The start point is held too, unless the sketch
    keeps another point of the line in place (e.g. its midpoint), in which case the start may
    slide along the line. Succeeds only if the line ends up at the length on its original line
    and direction; on failure, the dimension and endpoints are put back.
    """
    result = drawing.ResizeResult()
    error = line_resize_check(line)
    if error:
        result.error = error
        return result
    start0, dir0, len0 = line_frame(line)
    if abs(len0 - length_mm) < 1e-6 and (length_dimension(line) or line.isFullyConstrained):
        return result   # already right and held there; any dimension change re-solves the sketch

    # First with the start held, so only the end moves. If the sketch keeps some other point of
    # the line in place (e.g. its midpoint, aligned to the origin), that can't work: try again
    # letting the start slide along the line too.
    attempts = []
    for hold_start in (True, False):
        attempt = _try_resize(sketch, line, length_mm, hold, side, hold_start)
        attempts.append(attempt)
        if attempt.ok:
            return result
        _undo_resize(line, attempt)

    if any(a.failure is None and a.turned for a in attempts):
        # E.g. a horizontal dimension on a diagonal line: the solver keeps it by turning the line.
        result.error = ("The sketch constraints would turn this line to change its length. Add a constraint "
                        "that sets its angle, or turn off Resize.")
        return result
    length = line_frame(line)[2]
    failure = next((a.failure for a in attempts if a.failure), None)
    upper = (failure or 'CONSTRAIN').upper()
    if 'SOLV' in upper or 'CONSTRAIN' in upper:
        result.error = (f'The sketch constraints hold this line at {length:.2f} mm, but the rack needs '
                        f'{length_mm:.2f} mm. Change the module, or remove a constraint.')
    else:
        result.error = f"Couldn't resize the line: {failure}"
    return result


@dataclass
class _Attempt:
    start0: adsk.core.Point3D      # endpoints before the attempt, to put back if it fails
    end0: adsk.core.Point3D
    added: object = None           # the dimension it added, if any
    changed: Optional[tuple] = None   # (existing dimension, its expression before), if it changed one
    failure: Optional[str] = None  # the API's error, if it raised
    ok: bool = False
    turned: bool = False           # the line changed direction or moved off its own line


def _try_resize(sketch: adsk.fusion.Sketch, line: adsk.fusion.SketchLine, length_mm: float, hold: Optional[list],
                side: int, hold_start: bool) -> _Attempt:
    """Set the line's length through its dimension (adding one if needed). Succeeds if the length is
    reached with the direction unchanged, the start unmoved (hold_start) or still on the line."""
    start0, dir0, len0 = line_frame(line)
    attempt = _Attempt(line.startSketchPoint.geometry, line.endSketchPoint.geometry)
    temporarily_fixed = []
    try:
        for pt in ([line.startSketchPoint] if hold_start else []) + list(hold or []):
            if not pt.isFixed:
                pt.isFixed = True
                temporarily_fixed.append(pt)
        dim = length_dimension(line)
        if dim is not None:
            attempt.changed = (dim, dim.parameter.expression)
        else:
            # Text on the side away from the teeth, halfway along.
            mid = gm.rack_point(start0, dir0, side, len0 / 2.0, -0.15 * max(len0, 10.0))
            text = adsk.core.Point3D.create(to_cm(mid[0]), to_cm(mid[1]), 0)
            attempt.added = sketch.sketchDimensions.addDistanceDimension(
                line.startSketchPoint, line.endSketchPoint,
                adsk.fusion.DimensionOrientations.AlignedDimensionOrientation, text, True)
            dim = attempt.added
        dim.parameter.value = to_cm(length_mm)
    except Exception as e:
        attempt.failure = str(e)
    finally:
        for pt in temporarily_fixed:
            pt.isFixed = False

    start, direction, length = line_frame(line)
    s, h = gm.rack_coords(start0, dir0, gm.LEFT, start)
    attempt.turned = abs(gm._wrap(direction - dir0)) > 1e-9 or abs(h) > 1e-6
    moved_ok = abs(s) <= 1e-6 if hold_start else True
    attempt.ok = abs(length - length_mm) <= 1e-4 and not attempt.turned and moved_ok
    return attempt


def _undo_resize(line: adsk.fusion.SketchLine, attempt: _Attempt) -> None:
    """Put a failed resize back: drop the dimension it added (or restore the one it changed), then
    move the endpoints back to where they were."""
    try:
        if attempt.added is not None and attempt.added.isValid:
            attempt.added.deleteMe()
        changed = attempt.changed
        if changed is not None and changed[0].isValid and changed[0].parameter.expression != changed[1]:
            changed[0].parameter.expression = changed[1]
        for pt, target in ((line.startSketchPoint, attempt.start0), (line.endSketchPoint, attempt.end0)):
            g = pt.geometry
            if g.distanceTo(target) > 1e-9:
                pt.move(adsk.core.Vector3D.create(target.x - g.x, target.y - g.y, 0))
    except Exception:
        pass   # best effort; the caller reports the failure either way


# ---------------------------------------------------------------------------
# Pipeline (shared by executePreview and execute)
# ---------------------------------------------------------------------------

def make_rack(line: adsk.fusion.SketchLine, params: gm.RackParams, side: int = gm.LEFT, offset: float = 0.0,
              body: float = 0.0, resize: bool = True, finalize: bool = False, edit: bool = False,
              partner: Optional[adsk.fusion.SketchCircle] = None) -> drawing.GearResult:
    """Resize the line and draw a rack along it.

    With `partner` (a gear's pitch circle), the teeth are phased to mesh with that gear, and
    `offset` is added after the alignment, as a gear's rotation offset is. With edit, `line` must already be a rack: its old curves are deleted and redrawn under the
    same id. With finalize (execute only), the line is made construction geometry and the rack
    record is written to it.
    """
    result = drawing.GearResult()
    sketch = line.parentSketch
    error = in_plane_error(line)
    if error:
        result.errors.append(error)
        return result
    _, _, length0 = line_frame(line)
    check = gm.check_rack(params, length0, resize, offset, body)
    result.errors += check.errors
    result.warnings += check.warnings

    existing = read_rack(line)
    if edit:
        if existing is None:
            result.errors.append("This line isn't a rack made by this tool.")
    elif existing is not None:
        result.errors.append('This line is already a rack. Pick a different line.')
    elif drawing.read_gear(line) is not None or rack_for_entity(line) is not None:
        result.errors.append('That line is part of a gear made by this tool.')
    partner_record = None
    if partner is not None:
        partner_record = drawing.read_gear(partner)
        if partner_record is None:
            result.errors.append('Mesh with: not a gear made by this tool.')
        elif partner.parentSketch != sketch:
            result.errors.append('Mesh with must be in the same sketch.')
    if result.errors:
        return result

    rack_id = existing.rack_id if edit else uuid.uuid4().hex
    target = None
    if resize:
        target = gm.rack_teeth_for_length(length0, params) * params.pitch
        resized = resize_line(sketch, line, target, hold_points(sketch, line), side)
        if not resized.ok:
            result.errors.append(resized.error)
            return result
        result.warnings += resized.warnings

    # Re-read after the resize. The solver lands within ~1e-7 mm of the target; build on the exact
    # target so the last tooth's cell isn't dropped for being a hair too long.
    origin, direction, length = line_frame(line)
    if target is not None:
        length = target
    phase = offset
    pose = gm.RackPose(params, origin, direction, side, phase, length)
    if partner_record is not None:
        gear_center = drawing.circle_center_mm(partner)
        mesh = gm.check_rack_mesh(partner_record.params, gear_center, pose)
        result.errors += mesh.errors
        result.warnings += mesh.warnings
        if result.errors:
            return result
        phase = gm.align_rack_phase(pose, partner_record.params, gear_center, partner_record.theta0) + offset
        pose = gm.RackPose(params, origin, direction, side, phase, length)
        report = gm.rack_pair_report(partner_record.params, gm.gear_radii(partner_record.params), params)
        result.infos += report.infos
        result.warnings += [w for w in report.warnings if w not in result.warnings]
    profile = gm.build_rack(params, origin, direction, side, length, phase, body)
    if not profile.tooth_centers:
        result.errors.append('No whole tooth fits on the line at this offset.')
        return result
    result.rack = profile
    if not profile.closed:
        result.infos.append("Outline is open, so it won't extrude.")

    repoint = None
    if edit:
        if finalize:
            captured = drawing.capture_features(sketch, {c.entityToken for c in rack_parts(sketch, rack_id)})
            if captured:
                repoint = drawing._Repoint(sketch, captured)
                repoint.park()
        # Only now, once resizing has succeeded, so a failed edit keeps the old rack.
        delete_rack_parts(sketch, rack_id)

    deferred = sketch.isComputeDeferred
    sketch.isComputeDeferred = True
    try:
        result.entities = drawing.draw_profile(sketch, profile, closed=profile.closed)
        if finalize:
            # Tag each tooth's top land; the rest of the outline is found from these (see rack_parts).
            tagged = 0
            a = params.addendum
            for seg, ent in zip(profile.segments, result.entities):
                if not isinstance(seg, gm.Line):
                    continue
                h0 = gm.rack_coords(origin, direction, side, seg.start)[1]
                h1 = gm.rack_coords(origin, direction, side, seg.end)[1]
                if abs(h0 - a) < 1e-9 and abs(h1 - a) < 1e-9:
                    drawing.tag_part(ent, rack_id)
                    tagged += 1
            if not tagged and result.entities:
                drawing.tag_part(result.entities[-1], rack_id)
    finally:
        sketch.isComputeDeferred = deferred
    result.warnings += rack_gear_warnings(sketch, rack_id, pose)
    if finalize:
        line.isConstruction = True
        write_rack(line, gm.RackRecord(
            params=params, rack_id=rack_id, side=side, offset=offset, resize=resize, body=body,
            teeth=len(profile.tooth_centers), origin=origin, direction=direction, length=length,
            mesh_with=partner_record.gear_id if partner_record else '', phase=phase))
        if repoint is not None:
            result.repointed, result.not_repointed = repoint.finish({e.entityToken for e in result.entities})
    return result
