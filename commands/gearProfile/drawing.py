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
    """Mark a drawn curve as belonging to a gear, so an edit can find and replace it."""
    entity.attributes.add(ATTR_GROUP, PART_NAME, gear_id)


def gear_parts(sketch: adsk.fusion.Sketch, gear_id: str) -> list:
    """All curves drawn for the gear with this id (teeth and reference circles).

    Uses one design-wide attribute search; reading attributes curve by curve is much slower.
    """
    result = []
    for attr in sketch.parentComponent.parentDesign.findAttributes(ATTR_GROUP, PART_NAME):
        if attr.value != gear_id:
            continue
        curve = adsk.fusion.SketchCurve.cast(attr.parent)
        if curve is not None and curve.parentSketch == sketch:
            result.append(curve)
    return result


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
    return None


def delete_gear_parts(sketch: adsk.fusion.Sketch, gear_id: str) -> None:
    """Delete all of a gear's curves in one operation (deleting them one by one is ~100× slower).

    Each curve's tag is removed first: attributes outlive their deleted entities, and the
    leftovers pile up with every edit and make attribute adds over 10× slower.
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
    if abs(circle_radius_mm(circle) * 2 - diameter_mm) < 1e-9 and size_dimension(circle):
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
    profile: Optional[gm.Profile] = None
    theta0: float = 0.0
    entities: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


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

    profile = gm.build_profile(params, center, theta0)
    result.profile = profile
    result.theta0 = theta0
    # Tagging inside the deferred block matters: each attribute add otherwise triggers a
    # recompute of features built on the sketch (seconds per gear once it's extruded).
    if edit:
        # Only now, once resizing and alignment have succeeded, so a failed edit keeps the old gear.
        delete_gear_parts(sketch, gear_id)
    deferred = sketch.isComputeDeferred
    sketch.isComputeDeferred = True
    try:
        result.entities = draw_profile(sketch, profile)
        if reference_circles:
            result.entities += draw_reference_circles(sketch, circle, profile)
        for ent in result.entities:
            tag_part(ent, gear_id)
    finally:
        sketch.isComputeDeferred = deferred
    if edit:
        result.warnings += dependent_warnings(sketch, gear_id, params, center, theta0)
    if finalize:
        circle.isConstruction = True
        write_gear(circle, gm.GearRecord(
            params=params, theta0=theta0, gear_id=gear_id, rotation_offset=rotation_offset,
            resize=resize, reference_circles=reference_circles,
            mesh_with=partner_record.gear_id if partner_record else ''))
    return result


def dependent_warnings(sketch: adsk.fusion.Sketch, gear_id: str, params: gm.GearParams,
                       center: gm.Point, theta0: float) -> list:
    """Warnings for gears that were aligned to this one and no longer mesh with it."""
    warnings = []
    for c in gear_circles(sketch):
        record = read_gear(c)
        if record is None or record.mesh_with != gear_id:
            continue
        other_center = circle_center_mm(c)
        mesh = gm.check_mesh(record.params, other_center, params, center, circle_radius_mm(c))
        spacing = math.dist(center, other_center) - gm.expected_center_distance(params, record.params)
        aligned = gm.mesh_error(params, center, theta0, record.params, other_center, record.theta0) <= 1e-6
        if mesh.errors or abs(spacing) > gm.TANGENCY_TOL_MM or not aligned:
            warnings.append(f'The {record.params.teeth}-tooth gear meshed with this one no longer lines up. '
                            'Edit it to re-align.')
    return warnings
