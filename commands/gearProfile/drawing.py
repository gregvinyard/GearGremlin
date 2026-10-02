"""Sketch-level operations for GearGremlin: plain functions taking a sketch and parameters.

Kept separate from the command and event code so dev_scripts can call them directly.
Everything crossing into the API is converted mm → cm here.
"""
import math
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

import adsk.core
import adsk.fusion

from ... import gearmath as gm

ATTR_GROUP = 'GearGremlin'
ATTR_NAME = 'gear'
PART_NAME = 'part'  # on each drawn curve; value is the owning gear's id
PLAN_NAME = 'plan'  # on circles drawn by the planetary helper

_PLAIN_NUMBER = re.compile(r'^\s*[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?\s*(mm|cm|m|in|ft|")?\s*$')


def to_cm(mm: float) -> float:
    return mm / 10.0


def to_mm(cm: float) -> float:
    return cm * 10.0


def p3(pt: gm.Point) -> adsk.core.Point3D:
    return adsk.core.Point3D.create(to_cm(pt[0]), to_cm(pt[1]), 0)


def circle_center_mm(circle: adsk.fusion.SketchCircle) -> gm.Point:
    g = circle.centerSketchPoint.geometry
    return (to_mm(g.x), to_mm(g.y))


def circle_radius_mm(circle: adsk.fusion.SketchCircle) -> float:
    return to_mm(circle.radius)


# ---------------------------------------------------------------------------
# Gear metadata
# ---------------------------------------------------------------------------

def read_gear(circle: adsk.fusion.SketchCircle) -> Optional[gm.GearRecord]:
    """The GearRecord stored on a circle by this tool, or None."""
    attr = circle.attributes.itemByName(ATTR_GROUP, ATTR_NAME)
    if not attr:
        return None
    return gm.from_attribute(attr.value)


def write_gear(circle: adsk.fusion.SketchCircle, record: gm.GearRecord) -> None:
    circle.attributes.add(ATTR_GROUP, ATTR_NAME, gm.to_attribute(record))


def read_plan(circle: adsk.fusion.SketchCircle) -> Optional[gm.PlanRecord]:
    """The planetary plan the helper stored on this circle, or None."""
    attr = circle.attributes.itemByName(ATTR_GROUP, PLAN_NAME)
    return gm.from_plan_attribute(attr.value) if attr else None


def write_plan(circle: adsk.fusion.SketchCircle, plan: gm.PlanRecord) -> None:
    circle.attributes.add(ATTR_GROUP, PLAN_NAME, gm.to_plan_attribute(plan))


def gear_circles(sketch: adsk.fusion.Sketch) -> list:
    """All circles in the sketch that carry a gear attribute."""
    circles = sketch.sketchCurves.sketchCircles
    result = []
    for i in range(circles.count):
        c = circles.item(i)
        if c.attributes.itemByName(ATTR_GROUP, ATTR_NAME):
            result.append(c)
    return result


def find_gear_circle(sketch: adsk.fusion.Sketch, gear_id: str) -> Optional[adsk.fusion.SketchCircle]:
    if not gear_id:
        return None
    for c in gear_circles(sketch):
        record = read_gear(c)
        if record and record.gear_id == gear_id:
            return c
    return None


def tag_part(entity, gear_id: str) -> None:
    """Mark a drawn curve as belonging to a gear, so an edit can find and replace it.

    Only one curve per tooth (the tip arc) and the reference circles are tagged: attribute adds
    are slow (several ms each, growing with the design), and the rest of a tooth loop is found
    by following connected endpoints (see gear_parts).
    """
    entity.attributes.add(ATTR_GROUP, PART_NAME, gear_id)


def _tagged(sketch: adsk.fusion.Sketch, gear_id: Optional[str] = None) -> list:
    """(curve, gear id) for tagged curves in the sketch, from one design-wide attribute search."""
    out = []
    for attr in sketch.parentComponent.parentDesign.findAttributes(ATTR_GROUP, PART_NAME):
        if gear_id is not None and attr.value != gear_id:
            continue
        curve = adsk.fusion.SketchCurve.cast(attr.parent)
        if curve is not None and curve.parentSketch == sketch:
            out.append((curve, attr.value))
    return out


def _band(circle: adsk.fusion.SketchCircle, record: gm.GearRecord) -> tuple:
    """Center and the radius band a gear's own curves lie in."""
    p = record.params
    reach = p.dedendum + 0.5 * p.module
    return circle_center_mm(circle), p.pitch_radius - reach, p.pitch_radius + reach


def _in_band(curve, center: gm.Point, lo: float, hi: float) -> bool:
    for pt in (curve.startSketchPoint, curve.endSketchPoint):
        g = pt.geometry
        rho = math.hypot(to_mm(g.x) - center[0], to_mm(g.y) - center[1])
        if not lo <= rho <= hi:
            return False
    return True


def _walk(start, accept) -> list:
    """Curves connected to `start` end to end (not through circles), for which accept(curve) holds."""
    seen = {start.entityToken: start}
    stack = [start]
    while stack:
        curve = stack.pop()
        for pt in (curve.startSketchPoint, curve.endSketchPoint):
            ents = pt.connectedEntities
            for j in range(ents.count):
                other = adsk.fusion.SketchCurve.cast(ents.item(j))
                if other is None or adsk.fusion.SketchCircle.cast(other) is not None:
                    continue
                token = other.entityToken
                if token in seen or not accept(other):
                    continue
                seen[token] = other
                stack.append(other)
    return list(seen.values())


def _own_curve(gear_id: str, center: gm.Point, lo: float, hi: float):
    """A curve that can belong to this gear's tooth loop: an arc or fitted spline in its radius
    band, or (for gears made by older versions) any curve tagged with its id."""
    def accept(curve) -> bool:
        attr = curve.attributes.itemByName(ATTR_GROUP, PART_NAME)
        if attr is not None:
            return attr.value == gear_id
        is_profile_type = (adsk.fusion.SketchArc.cast(curve) is not None
                           or adsk.fusion.SketchFittedSpline.cast(curve) is not None)
        return is_profile_type and _in_band(curve, center, lo, hi)
    return accept


def gear_parts(sketch: adsk.fusion.Sketch, gear_id: str) -> list:
    """All curves drawn for the gear with this id: its tooth loop and reference circles."""
    tagged = [c for c, _ in _tagged(sketch, gear_id)]
    circle = find_gear_circle(sketch, gear_id)
    if circle is None:
        return tagged
    center, lo, hi = _band(circle, read_gear(circle))
    parts = {c.entityToken: c for c in tagged}
    walked = set()
    for c in tagged:
        if adsk.fusion.SketchCircle.cast(c) is not None or c.entityToken in walked:
            continue
        # Measure against where the gear was when it was drawn: an edit may already have moved
        # the pitch circle. A tagged tip arc's center is that point.
        arc = adsk.fusion.SketchArc.cast(c)
        if arc is not None:
            g = arc.centerSketchPoint.geometry
            center = (to_mm(g.x), to_mm(g.y))
        for found in _walk(c, _own_curve(gear_id, center, lo, hi)):
            parts[found.entityToken] = found
            walked.add(found.entityToken)
    return list(parts.values())


def gear_for_entity(entity) -> Optional[adsk.fusion.SketchCircle]:
    """The gear's pitch circle, given the circle itself or any curve drawn for it."""
    curve = adsk.fusion.SketchCurve.cast(entity)
    if curve is None:
        return None
    circle = adsk.fusion.SketchCircle.cast(curve)
    if circle is not None and read_gear(circle) is not None:
        return circle
    attr = curve.attributes.itemByName(ATTR_GROUP, PART_NAME)
    if attr:
        return find_gear_circle(curve.parentSketch, attr.value)
    if circle is not None or (adsk.fusion.SketchArc.cast(curve) is None
                              and adsk.fusion.SketchFittedSpline.cast(curve) is None):
        return None
    # An untagged tooth curve: walk along the loop to the nearest tagged one (a few steps).
    sketch = curve.parentSketch
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
                        found = find_gear_circle(sketch, tag.value)
                        if found is not None:
                            center, lo, hi = _band(found, read_gear(found))
                            if _in_band(curve, center, lo, hi):
                                return found
                    nxt.append(other)
        frontier = nxt
        if not frontier:
            break
    return None


def delete_gear_parts(sketch: adsk.fusion.Sketch, gear_id: str) -> None:
    """Delete all of a gear's curves in one operation (deleting them one by one is ~100× slower).

    Tags are removed first: attributes outlive their deleted entities, and the leftovers pile
    up with every edit and make attribute adds over 10× slower.
    """
    parts = adsk.core.ObjectCollection.create()
    for c in gear_parts(sketch, gear_id):
        attr = c.attributes.itemByName(ATTR_GROUP, PART_NAME)
        if attr:
            attr.deleteMe()
        parts.add(c)
    if parts.count:
        sketch.parentComponent.parentDesign.deleteEntities(parts)


# ---------------------------------------------------------------------------
# Resizing
# ---------------------------------------------------------------------------

@dataclass
class ResizeResult:
    error: str = ''
    warnings: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error


def size_dimension(circle: adsk.fusion.SketchCircle):
    """The circle's driving diameter or radius dimension, or None."""
    dims = circle.sketchDimensions
    for i in range(dims.count):
        d = dims.item(i)
        if not d.isDriving:
            continue
        if adsk.fusion.SketchDiameterDimension.cast(d) or adsk.fusion.SketchRadialDimension.cast(d):
            return d
    return None


def dimension_expression_warning(circle: adsk.fusion.SketchCircle) -> str:
    """A warning if the circle's size comes from an expression that Resize would overwrite."""
    dim = size_dimension(circle)
    if dim and not _PLAIN_NUMBER.match(dim.parameter.expression):
        return (f'Circle size is set by the expression "{dim.parameter.expression}". '
                'Resize will replace it with a number.')
    return ''


def resize_check(circle: adsk.fusion.SketchCircle) -> str:
    """A blocking error that's knowable before trying to resize, or ''."""
    if circle.isFixed:
        return 'The circle is fixed, so it can\'t be resized. Unfix it or turn off Resize.'
    return ''


def resize_circle(sketch: adsk.fusion.Sketch, circle: adsk.fusion.SketchCircle, diameter_mm: float,
                  hold: Optional[list] = None) -> ResizeResult:
    """Set the circle's diameter to diameter_mm through its dimension (adding one if needed).

    Circles in `hold` (typically existing gears) have their centers fixed during the
    resize so the solver moves the new circle rather than gears whose teeth are drawn.
    """
    result = ResizeResult()
    error = resize_check(circle)
    if error:
        result.error = error
        return result
    if abs(circle_radius_mm(circle) * 2 - diameter_mm) < 1e-6 and (size_dimension(circle)
                                                                   or circle.isFullyConstrained):
        # Already right, and held there (by a dimension, or by constraints as in a planetary set).
        # Skipping matters: any dimension change re-solves the whole sketch.
        return result

    temporarily_fixed = []
    failure = None
    try:
        for c in hold or []:
            pt = c.centerSketchPoint
            if not pt.isFixed:
                pt.isFixed = True
                temporarily_fixed.append(pt)
        dim = size_dimension(circle)
        if dim is None:
            g = circle.centerSketchPoint.geometry
            r = circle.radius
            text = adsk.core.Point3D.create(g.x + r * 0.8, g.y + r * 0.8, 0)
            dim = sketch.sketchDimensions.addDiameterDimension(circle, text, True)
        is_radius = adsk.fusion.SketchRadialDimension.cast(dim) is not None
        target_cm = to_cm(diameter_mm / 2 if is_radius else diameter_mm)
        dim.parameter.value = target_cm
    except Exception as e:
        failure = str(e)
    finally:
        for pt in temporarily_fixed:
            pt.isFixed = False

    current = circle_radius_mm(circle) * 2
    if abs(current - diameter_mm) <= 1e-4:
        # Done, or the dimension was refused because constraints already fix the size at the
        # right value (e.g. the ring and later planets of a fully constrained planetary set).
        return result
    upper = (failure or 'CONSTRAIN').upper()
    if 'SOLV' in upper or 'CONSTRAIN' in upper:
        result.error = (f'The sketch constraints hold this circle at ⌀{current:.2f} mm, but the gear needs '
                        f'⌀{diameter_mm:.2f} mm. Change the module or teeth, or remove a constraint.')
    else:
        result.error = f'Couldn\'t resize the circle: {failure}'
    return result


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def _closer(pt_a: adsk.fusion.SketchPoint, pt_b: adsk.fusion.SketchPoint, target: gm.Point):
    def dist(p):
        g = p.geometry
        return math.hypot(to_mm(g.x) - target[0], to_mm(g.y) - target[1])
    return pt_a if dist(pt_a) <= dist(pt_b) else pt_b


def draw_profile(sketch: adsk.fusion.Sketch, profile: gm.Profile) -> list:
    """Draw the profile as one closed loop, chaining shared SketchPoints. Returns the new curves."""
    curves = sketch.sketchCurves
    segments = profile.segments
    entities = []
    first_point = None
    prev_point = None
    deferred = sketch.isComputeDeferred
    sketch.isComputeDeferred = True
    try:
        last = len(segments) - 1
        for i, seg in enumerate(segments):
            start = prev_point if prev_point is not None else p3(seg.start)
            end = first_point if i == last else p3(seg.end)
            if isinstance(seg, gm.Line):
                ent = curves.sketchLines.addByTwoPoints(start, end)
            elif isinstance(seg, gm.Arc):
                ent = curves.sketchArcs.addByThreePoints(start, p3(seg.mid), end)
            else:
                pts = adsk.core.ObjectCollection.create()
                pts.add(start)
                for p in seg.points[1:-1]:
                    pts.add(p3(p))
                pts.add(end)
                ent = curves.sketchFittedSplines.add(pts)
            entities.append(ent)
            if first_point is None:
                first_point = _closer(ent.startSketchPoint, ent.endSketchPoint, seg.start)
            prev_point = _closer(ent.startSketchPoint, ent.endSketchPoint, seg.end)
    finally:
        sketch.isComputeDeferred = deferred
    return entities


def draw_reference_circles(sketch: adsk.fusion.Sketch, circle: adsk.fusion.SketchCircle,
                           profile: gm.Profile) -> list:
    """Tip and root circles as construction geometry, concentric with the pitch circle."""
    result = []
    for r in (profile.tip_radius, profile.root_radius):
        c = sketch.sketchCurves.sketchCircles.addByCenterRadius(circle.centerSketchPoint, to_cm(r))
        c.isConstruction = True
        result.append(c)
    return result


# ---------------------------------------------------------------------------
# Pipeline (shared by executePreview and execute)
# ---------------------------------------------------------------------------

@dataclass
class GearResult:
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    infos: list = field(default_factory=list)
    profile: Optional[gm.Profile] = None
    theta0: float = 0.0
    entities: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _tangent(a: gm.GearParams, ca: gm.Point, b: gm.GearParams, cb: gm.Point) -> bool:
    if a.internal and b.internal:
        return False
    return abs(math.dist(ca, cb) - gm.expected_center_distance(a, b)) <= gm.TANGENCY_TOL_MM


def ring_pinions(circle: adsk.fusion.SketchCircle, params: gm.GearParams, center: gm.Point,
                 partner: Optional[adsk.fusion.SketchCircle]) -> tuple:
    """External gears a ring must be trimmed against: its Mesh with partner, gears already
    tangent inside it, and its planetary plan's planets."""
    pinions = []
    for c in gear_circles(circle.parentSketch):
        if c == circle:
            continue
        record = read_gear(c)
        if record is None or record.params.internal:
            continue
        if c == partner or _tangent(params, center, record.params, circle_center_mm(c)):
            pinions.append(record.params)
    plan = read_plan(circle)
    if plan is not None and plan.role == 'ring':
        pinions.append(plan.planet_params(params.backlash))
    # Only pinions of the same system can mesh; identical parameters give identical trims.
    return tuple(dict.fromkeys(p for p in pinions if gm.same_system(p, params)))


def make_gear(circle: adsk.fusion.SketchCircle, params: gm.GearParams, resize: bool = True,
              partner: Optional[adsk.fusion.SketchCircle] = None, rotation_offset: float = 0.0,
              reference_circles: bool = False, finalize: bool = False, edit: bool = False) -> GearResult:
    """Resize, align, and draw one gear on `circle`.

    With edit, `circle` must already be a gear: its old curves are deleted and redrawn under
    the same id. With finalize (execute only), the circle is made construction geometry and
    the gear record is written to it.
    """
    result = GearResult()
    sketch = circle.parentSketch
    check = gm.check_params(params)
    result.errors += check.errors
    result.warnings += check.warnings

    existing = read_gear(circle)
    if edit:
        if existing is None:
            result.errors.append("This circle isn't a gear made by this tool.")
        elif not existing.editable:
            result.errors.append("This gear was made by an older GearGremlin and can't be edited. "
                                 'Delete it and make it again.')
    elif existing is not None:
        result.errors.append('This circle is already a gear. Pick a different circle.')

    partner_record = None
    if partner is not None:
        partner_record = read_gear(partner)
        if partner_record is None:
            result.errors.append('Mesh with: not a gear made by this tool.')
        elif partner == circle:
            result.errors.append('Mesh with must be a different circle.')
        elif partner.parentSketch != sketch:
            result.errors.append('Mesh with must be in the same sketch.')
    if result.errors:
        return result

    gear_id = existing.gear_id if edit else uuid.uuid4().hex

    if resize:
        hold = [c for c in gear_circles(sketch) if c != circle]
        resized = resize_circle(sketch, circle, 2 * params.pitch_radius, hold)
        if not resized.ok:
            result.errors.append(resized.error)
            return result
        result.warnings += resized.warnings
    else:
        result.warnings += gm.check_sizing(2 * circle_radius_mm(circle), params, resize).warnings

    # Re-read after the resize: the solver may have moved either circle.
    center = circle_center_mm(circle)
    theta0 = 0.0
    if partner_record is not None:
        partner_center = circle_center_mm(partner)
        mesh = gm.check_mesh(params, center, partner_record.params, partner_center, circle_radius_mm(partner))
        result.errors += mesh.errors
        result.warnings += mesh.warnings
        if result.errors:
            return result
        theta0 = gm.align_theta0(params, center, partner_record.params, partner_center, partner_record.theta0)
    theta0 += rotation_offset

    # Drawn radii. A ring is trimmed (from scratch, also on edit) against every pinion it meets.
    if params.internal:
        pinions = ring_pinions(circle, params, center, partner)
        radii = gm.gear_radii(params, pinions)
        result.infos += ring_trim_notes(radii, pinions)
        result.warnings += [gm.trim_failure_message(params, t) for t in radii.failed]
    else:
        radii = gm.gear_radii(params)

    planned = read_plan(circle) is not None
    if partner_record is not None:
        report = gm.pair_report(params, radii, partner_record.params, partner_record.drawn_tip, planned)
        result.infos += report.infos
        result.warnings += [w for w in report.warnings if w not in result.warnings]
    if not params.internal:
        # Rings this gear sits in, other than the partner: are their tips trimmed enough for it?
        for c, record in _tangent_gears(sketch, circle, params, center):
            if c != partner and record.params.internal:
                report = gm.pair_report(params, radii, record.params, record.drawn_tip, planned)
                result.warnings += [w for w in report.warnings if 'ring' in w and w not in result.warnings]

    profile = gm.build_profile(params, center, theta0, radii=radii)
    result.profile = profile
    result.theta0 = theta0
    if edit:
        # Only now, once resizing and alignment have succeeded, so a failed edit keeps the old gear.
        delete_gear_parts(sketch, gear_id)
    # Tagging inside the deferred block matters: each attribute add otherwise triggers a
    # recompute of features built on the sketch.
    deferred = sketch.isComputeDeferred
    sketch.isComputeDeferred = True
    try:
        result.entities = draw_profile(sketch, profile)
        # Tag each tooth's tip arc; the rest of the loop is found from these (see tag_part).
        # Tags only matter once the gear exists, so the preview skips them (they cost ~6 ms each).
        if finalize:
            for seg, ent in zip(profile.segments, result.entities):
                if isinstance(seg, gm.Arc) and abs(seg.radius - radii.tip) < 1e-9:
                    tag_part(ent, gear_id)
        if reference_circles:
            refs = draw_reference_circles(sketch, circle, profile)
            if finalize:
                for ent in refs:
                    tag_part(ent, gear_id)
            result.entities += refs
    finally:
        sketch.isComputeDeferred = deferred
    result.warnings += neighbor_warnings(sketch, circle, gear_id, params, center, theta0)
    if finalize:
        circle.isConstruction = True
        write_gear(circle, gm.GearRecord(
            params=params, theta0=theta0, gear_id=gear_id, rotation_offset=rotation_offset,
            resize=resize, reference_circles=reference_circles,
            mesh_with=partner_record.gear_id if partner_record else '', tip_radius=radii.tip))
    return result


def ring_trim_notes(radii: gm.Radii, pinions: tuple) -> list:
    if not pinions:
        return ['Tip trim depends on the pinion. Set Mesh with, or edit the ring after making its pinions.']
    if radii.trim is None:
        return []
    return [f'Ring tips trimmed {radii.trimmed_by:.2f} mm (tip ⌀ {2 * radii.tip:.2f} mm) to clear the '
            f'{radii.trim.pinion.teeth}-tooth pinion.']


def _tangent_gears(sketch: adsk.fusion.Sketch, circle: adsk.fusion.SketchCircle, params: gm.GearParams,
                   center: gm.Point) -> list:
    out = []
    for c in gear_circles(sketch):
        if c == circle:
            continue
        record = read_gear(c)
        if record is not None and _tangent(params, center, record.params, circle_center_mm(c)):
            out.append((c, record))
    return out


def neighbor_warnings(sketch: adsk.fusion.Sketch, circle: adsk.fusion.SketchCircle, gear_id: str,
                      params: gm.GearParams, center: gm.Point, theta0: float) -> list:
    """Out-of-phase check: every tangent gear, plus gears aligned to this one (even if an edit made
    them no longer tangent), must still mesh with it."""
    warnings = []
    for c in gear_circles(sketch):
        if c == circle:
            continue
        record = read_gear(c)
        if record is None or (params.internal and record.params.internal):
            continue
        other_center = circle_center_mm(c)
        dependent = bool(gear_id) and record.mesh_with == gear_id
        if not dependent and not _tangent(params, center, record.params, other_center):
            continue
        compatible = gm.same_system(params, record.params)
        spacing = math.dist(center, other_center) - gm.expected_center_distance(params, record.params)
        aligned = gm.mesh_error(params, center, theta0, record.params, other_center, record.theta0) <= 1e-6
        if not compatible or abs(spacing) > gm.TANGENCY_TOL_MM or not aligned:
            warnings.append(f'The {record.params.teeth}-tooth gear meshed with this one no longer lines up. '
                            'Edit it to re-align.')
    return list(dict.fromkeys(warnings))
