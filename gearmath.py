"""Pure-Python involute spur gear math for GearGremlin.

No `adsk` imports. All lengths are millimetres, all angles radians.
See SPEC.md for the formulas and conventions used here.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Optional, Union

Point = tuple[float, float]

STANDARD_MODULES_MM: list[float] = [0.5, 0.8, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0]
PRESSURE_ANGLES_DEG: list[float] = [14.5, 20.0, 25.0]
MIN_TEETH = 6
MAX_TEETH = 400
TANGENCY_TOL_MM = 0.001
ATTR_VERSION = 2

EXTERNAL = 'external'
INTERNAL = 'internal'


# ---------------------------------------------------------------------------
# Parameters and basic radii
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GearParams:
    module: float
    teeth: int
    pressure_angle: float  # radians
    backlash: float = 0.0  # total per mesh, mm
    gear_type: str = EXTERNAL

    @property
    def internal(self) -> bool:
        return self.gear_type == INTERNAL

    @property
    def pitch_radius(self) -> float:
        return self.module * self.teeth / 2.0

    @property
    def base_radius(self) -> float:
        return self.pitch_radius * math.cos(self.pressure_angle)

    @property
    def angular_pitch(self) -> float:
        return 2.0 * math.pi / self.teeth

    @property
    def tip_radius(self) -> float:
        """Nominal tip radius (before any clamping)."""
        if self.internal:
            return self.pitch_radius - self.module
        return self.pitch_radius + self.module

    @property
    def root_radius(self) -> float:
        """Nominal root radius (before any clamping)."""
        if self.internal:
            return self.pitch_radius + 1.25 * self.module
        return self.pitch_radius - 1.25 * self.module

    @property
    def half_angle(self) -> float:
        """ψ (external: half tooth thickness) or ψs (internal: half space width), at the pitch circle."""
        base = math.pi / (2.0 * self.teeth)
        correction = self.backlash / (4.0 * self.pitch_radius)
        return base + correction if self.internal else base - correction


def involute(phi: float) -> float:
    """inv(φ) = tan φ − φ."""
    return math.tan(phi) - phi


def inverse_involute(value: float) -> float:
    """Return φ such that inv(φ) = value (value ≥ 0), by Newton's method."""
    if value <= 0.0:
        return 0.0
    phi = (3.0 * value) ** (1.0 / 3.0)
    for _ in range(50):
        t = math.tan(phi)
        step = (t - phi - value) / (t * t)
        phi -= step
        if abs(step) < 1e-15:
            break
    return phi


def pressure_angle_at(base_radius: float, rho: float) -> float:
    """φ(ρ) = arccos(rb/ρ), for ρ ≥ rb."""
    return math.acos(min(1.0, base_radius / rho))


def flank_angle(params: GearParams, rho: float) -> float:
    """Polar half-angle of the involute shape (tooth or space) at radius ρ: ψ + inv(α) − inv(φ(ρ))."""
    rho = max(rho, params.base_radius)
    return (params.half_angle + involute(params.pressure_angle)
            - involute(pressure_angle_at(params.base_radius, rho)))


def radius_where_pointed(params: GearParams) -> float:
    """Radius at which the involute shape's half-angle reaches zero."""
    phi = inverse_involute(params.half_angle + involute(params.pressure_angle))
    return params.base_radius / math.cos(phi)


def suggest_teeth(circle_diameter: float, module: float) -> int:
    """Hybrid sizing: tooth count that best fits the drawn circle, round(d/m)."""
    if module <= 0:
        return MIN_TEETH
    return int(round(circle_diameter / module))


def clamp_teeth(teeth: int) -> int:
    return max(MIN_TEETH, min(MAX_TEETH, teeth))


def undercut_min_teeth(pressure_angle: float) -> int:
    """Rule-of-thumb minimum tooth count without undercut, floor(2 / sin²α) (17 at 20°)."""
    return int(2.0 / math.sin(pressure_angle) ** 2)


# ---------------------------------------------------------------------------
# Profile segments
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Line:
    start: Point
    end: Point


@dataclass(frozen=True)
class Spline:
    points: list[Point]

    @property
    def start(self) -> Point:
        return self.points[0]

    @property
    def end(self) -> Point:
        return self.points[-1]


@dataclass(frozen=True)
class Arc:
    """Counter-clockwise arc from start_angle sweeping `sweep` (> 0) radians."""
    center: Point
    radius: float
    start_angle: float
    sweep: float

    def point_at(self, angle: float) -> Point:
        return (self.center[0] + self.radius * math.cos(angle),
                self.center[1] + self.radius * math.sin(angle))

    @property
    def start(self) -> Point:
        return self.point_at(self.start_angle)

    @property
    def mid(self) -> Point:
        return self.point_at(self.start_angle + self.sweep / 2.0)

    @property
    def end(self) -> Point:
        return self.point_at(self.start_angle + self.sweep)


Segment = Union[Line, Spline, Arc]


@dataclass
class Profile:
    """A closed loop of segments. Each segment's end equals the next one's start."""
    params: GearParams
    center: Point
    theta0: float
    segments: list[Segment]
    tip_radius: float   # as drawn (after clamping)
    root_radius: float  # as drawn (after clamping)
    warnings: list[str] = field(default_factory=list)


def _polar(center: Point, rho: float, angle: float) -> Point:
    return (center[0] + rho * math.cos(angle), center[1] + rho * math.sin(angle))


def flank_radii(r_start: float, r_end: float, count: int) -> list[float]:
    """`count` radii from r_start to r_end, denser near r_start (where involute curvature is highest)."""
    count = max(count, 2)
    return [r_start + (r_end - r_start) * (i / (count - 1)) ** 2 for i in range(count)]


def drawn_radii(params: GearParams) -> tuple[float, float, list[str]]:
    """Tip and root radii as they'll be drawn, after clamping, plus any clamp warnings."""
    warnings: list[str] = []
    ra, rf = params.tip_radius, params.root_radius
    if params.internal:
        if ra < params.base_radius:
            ra = params.base_radius
            warnings.append('Internal tips clamped to the base circle (teeth slightly short).')
        pointed = radius_where_pointed(params)
        if pointed < rf:
            rf = pointed
            warnings.append('Tooth spaces come to a point; root clamped.')
    else:
        pointed = radius_where_pointed(params)
        if pointed < ra:
            ra = pointed
            warnings.append('Teeth come to a point; tip clamped.')
    return ra, rf, warnings


def build_profile(params: GearParams, center: Point = (0.0, 0.0), theta0: float = 0.0,
                  points_per_flank: int = 10) -> Profile:
    """Build the closed tooth profile.

    Both gear types use the same loop. Each repeated "shape" is the involute-bounded
    region of half-angle flank_angle(ρ): an external gear's tooth, or an internal
    gear's tooth space. Shapes run from an inner radius to an outer radius, with an
    outer cap arc on each shape and an inner arc between neighbouring shapes.
    """
    ra, rf, warnings = drawn_radii(params)
    rb = params.base_radius
    tau = params.angular_pitch
    if params.internal:
        inner, outer = ra, rf
        first_center = theta0 + tau / 2.0
    else:
        inner, outer = rf, ra
        first_center = theta0

    involute_start = max(rb, inner)
    radial_foot = inner < involute_start  # external gear with root below the base circle
    half_inner = flank_angle(params, involute_start)
    half_outer = flank_angle(params, outer)
    radii = flank_radii(involute_start, outer, points_per_flank)

    segments: list[Segment] = []
    for k in range(params.teeth):
        c = first_center + k * tau
        lead = c - half_inner
        trail = c + half_inner
        if radial_foot:
            segments.append(Line(_polar(center, inner, lead), _polar(center, involute_start, lead)))
        segments.append(Spline([_polar(center, rho, c - flank_angle(params, rho)) for rho in radii]))
        segments.append(Arc(center, outer, c - half_outer, 2.0 * half_outer))
        segments.append(Spline([_polar(center, rho, c + flank_angle(params, rho)) for rho in reversed(radii)]))
        if radial_foot:
            segments.append(Line(_polar(center, involute_start, trail), _polar(center, inner, trail)))
        segments.append(Arc(center, inner, trail, tau - 2.0 * half_inner))

    return Profile(params, center, theta0, segments, ra, rf, warnings)


# ---------------------------------------------------------------------------
# Mesh alignment
# ---------------------------------------------------------------------------

def _frac(x: float) -> float:
    return x - math.floor(x)


def phase(direction: float, theta0: float, teeth: int) -> float:
    """Phase in teeth, p = frac((d − θ₀)/τ). 0 means a tooth centerline points along d."""
    return _frac((direction - theta0) / (2.0 * math.pi / teeth))


def theta0_for_phase(direction: float, p: float, teeth: int) -> float:
    """θ₀ in [0, τ) that gives phase p along direction d."""
    tau = 2.0 * math.pi / teeth
    return (direction - p * tau) % tau


def contact_directions(a_type: str, a_center: Point, b_type: str, b_center: Point) -> tuple[float, float]:
    """Direction from each gear's center to the contact point (d_A, d_B)."""
    if a_type == EXTERNAL and b_type == EXTERNAL:
        d_a = math.atan2(b_center[1] - a_center[1], b_center[0] - a_center[0])
        return d_a, d_a + math.pi
    if a_type == INTERNAL:
        ring, pinion = a_center, b_center
    else:
        ring, pinion = b_center, a_center
    d = math.atan2(pinion[1] - ring[1], pinion[0] - ring[0])
    return d, d


def align_theta0(new: GearParams, new_center: Point,
                 partner: GearParams, partner_center: Point, partner_theta0: float) -> float:
    """θ₀ for the new gear so a tooth on one faces a gap on the other at the contact point."""
    d_new, d_partner = contact_directions(new.gear_type, new_center, partner.gear_type, partner_center)
    p_partner = phase(d_partner, partner_theta0, partner.teeth)
    if new.internal == partner.internal:
        p_new = 0.5 - p_partner           # external pair: p_A + p_B ≡ ½
    elif new.internal:
        p_new = 0.5 + p_partner           # new is ring: p_ring − p_pinion ≡ ½
    else:
        p_new = p_partner - 0.5           # new is pinion
    return theta0_for_phase(d_new, _frac(p_new), new.teeth)


def expected_center_distance(a: GearParams, b: GearParams) -> float:
    if a.internal or b.internal:
        return abs(a.pitch_radius - b.pitch_radius)
    return a.pitch_radius + b.pitch_radius


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

@dataclass
class Check:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def extend(self, other: 'Check') -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)

    @property
    def ok(self) -> bool:
        return not self.errors


def check_params(params: GearParams) -> Check:
    check = Check()
    if params.module <= 0:
        check.errors.append('Module must be greater than 0.')
    if params.teeth < MIN_TEETH:
        check.errors.append(f'Need at least {MIN_TEETH} teeth.')
    if params.backlash < 0:
        check.errors.append('Backlash cannot be negative.')
    if check.errors:
        return check
    if params.backlash >= math.pi * params.module / 2.0:
        check.errors.append('Backlash is larger than the tooth itself.')
        return check
    if not params.internal and params.teeth < undercut_min_teeth(params.pressure_angle):
        check.warnings.append(f'Under {undercut_min_teeth(params.pressure_angle)} teeth: real gears would be '
                              'undercut. This profile isn\'t, so it may bind slightly.')
    check.warnings.extend(drawn_radii(params)[2])
    return check


def check_sizing(circle_diameter: float, params: GearParams, resize: bool) -> Check:
    check = Check()
    pitch_d = 2.0 * params.pitch_radius
    if not resize and abs(pitch_d - circle_diameter) > TANGENCY_TOL_MM:
        check.warnings.append('Resize is off: the gear won\'t match the drawn circle.')
    return check


def check_mesh(new: GearParams, new_center: Point,
               partner: GearParams, partner_center: Point, partner_radius: float) -> Check:
    """Validate a mesh pair. partner_radius is the partner circle's current radius."""
    check = Check()
    if abs(new.module - partner.module) > 1e-9:
        check.errors.append('Module doesn\'t match the mesh partner.')
    if abs(new.pressure_angle - partner.pressure_angle) > 1e-9:
        check.errors.append('Pressure angle doesn\'t match the mesh partner.')
    if new.internal and partner.internal:
        check.errors.append('Two internal gears can\'t mesh.')
    if check.errors:
        return check
    if new.internal or partner.internal:
        ring, pinion = (new, partner) if new.internal else (partner, new)
        if ring.teeth <= pinion.teeth:
            check.errors.append('The ring gear needs more teeth than the pinion.')
            return check
        if ring.teeth - pinion.teeth < 12:
            check.warnings.append('Ring and pinion differ by under 12 teeth: possible tip interference.')
    dist = math.hypot(new_center[0] - partner_center[0], new_center[1] - partner_center[1])
    if dist < TANGENCY_TOL_MM:
        check.errors.append('Gears share a center, so there\'s no contact point to align to.')
        return check
    if abs(dist - expected_center_distance(new, partner)) > TANGENCY_TOL_MM:
        check.warnings.append('Circles aren\'t tangent: the gears won\'t mesh at this spacing.')
    if abs(partner_radius - partner.pitch_radius) > TANGENCY_TOL_MM:
        check.warnings.append('Mesh partner was resized after it was made.')
    return check


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

@dataclass
class GearRecord:
    """Everything stored on a gear's pitch circle: the geometry plus the settings needed to edit it."""
    params: GearParams
    theta0: float                  # total, including rotation_offset
    gear_id: str = ''              # empty for version-1 gears, which can't be edited
    rotation_offset: float = 0.0
    resize: bool = True
    reference_circles: bool = False
    mesh_with: str = ''            # gear_id of the partner it was aligned to, if any

    @property
    def editable(self) -> bool:
        return bool(self.gear_id)


def to_attribute(record: GearRecord) -> str:
    params = record.params
    return json.dumps({
        'version': ATTR_VERSION,
        'id': record.gear_id,
        'type': params.gear_type,
        'module_mm': params.module,
        'pressure_angle_deg': math.degrees(params.pressure_angle),
        'teeth': params.teeth,
        'theta0_rad': record.theta0,
        'backlash_mm': params.backlash,
        'rotation_offset_rad': record.rotation_offset,
        'resize': record.resize,
        'reference_circles': record.reference_circles,
        'mesh_with': record.mesh_with,
    })


def from_attribute(value: str) -> Optional[GearRecord]:
    """Parse a stored gear attribute (version 1 or 2), or None if it isn't valid."""
    try:
        data = json.loads(value)
        params = GearParams(
            module=float(data['module_mm']),
            teeth=int(data['teeth']),
            pressure_angle=math.radians(float(data['pressure_angle_deg'])),
            backlash=float(data.get('backlash_mm', 0.0)),
            gear_type=INTERNAL if data['type'] == INTERNAL else EXTERNAL,
        )
        return GearRecord(
            params=params,
            theta0=float(data['theta0_rad']),
            gear_id=str(data.get('id', '')),
            rotation_offset=float(data.get('rotation_offset_rad', 0.0)),
            resize=bool(data.get('resize', True)),
            reference_circles=bool(data.get('reference_circles', False)),
            mesh_with=str(data.get('mesh_with', '')),
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def mesh_error(a: GearParams, a_center: Point, a_theta0: float,
               b: GearParams, b_center: Point, b_theta0: float) -> float:
    """How far a pair is from the mesh condition, in teeth (0 = perfectly aligned, 0.5 = worst)."""
    d_a, d_b = contact_directions(a.gear_type, a_center, b.gear_type, b_center)
    p_a = phase(d_a, a_theta0, a.teeth)
    p_b = phase(d_b, b_theta0, b.teeth)
    if a.internal == b.internal:
        off = p_a + p_b - 0.5
    elif a.internal:
        off = p_a - p_b - 0.5
    else:
        off = p_b - p_a - 0.5
    off = _frac(off)
    return min(off, 1.0 - off)


# ---------------------------------------------------------------------------
# SVG debug output
# ---------------------------------------------------------------------------

def _svg_path(profile: Profile) -> str:
    def pt(p: Point) -> str:
        return f'{p[0]:.5f},{-p[1]:.5f}'  # flip y for SVG

    parts = [f'M {pt(profile.segments[0].start)}']
    for seg in profile.segments:
        if isinstance(seg, Arc):
            large = 1 if seg.sweep > math.pi else 0
            # y is flipped, so CCW in math coordinates is sweep-flag 0 in SVG.
            parts.append(f'A {seg.radius:.5f},{seg.radius:.5f} 0 {large} 0 {pt(seg.end)}')
        elif isinstance(seg, Spline):
            parts.extend(f'L {pt(p)}' for p in seg.points[1:])
        else:
            parts.append(f'L {pt(seg.end)}')
    parts.append('Z')
    return ' '.join(parts)


def to_svg(profiles: Union[Profile, list[Profile]], path: str) -> None:
    """Write one profile, or a meshing set, to an SVG for visual checks.

    Pitch circles are dashed, base circles dotted, tooth 0 centerlines drawn in red.
    """
    if isinstance(profiles, Profile):
        profiles = [profiles]
    xs, ys = [], []
    for p in profiles:
        reach = max(p.tip_radius, p.root_radius) + p.params.module
        xs += [p.center[0] - reach, p.center[0] + reach]
        ys += [-p.center[1] - reach, -p.center[1] + reach]
    x0, y0 = min(xs), min(ys)
    w, h = max(xs) - x0, max(ys) - y0
    stroke = max(w, h) / 1500.0
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:.4f} {y0:.4f} {w:.4f} {h:.4f}" '
           f'width="1000" height="{1000 * h / w:.0f}">',
           f'<rect x="{x0}" y="{y0}" width="{w}" height="{h}" fill="white"/>']
    colors = ['#3b6fb6', '#c9762b', '#3a9a5b', '#8a4fb0']
    for i, p in enumerate(profiles):
        cx, cy = p.center[0], -p.center[1]
        color = colors[i % len(colors)]
        fill = 'none' if p.params.internal else color
        out.append(f'<path d="{_svg_path(p)}" fill="{fill}" fill-opacity="0.25" stroke="{color}" '
                   f'stroke-width="{stroke}" fill-rule="evenodd"/>')
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{p.params.pitch_radius}" fill="none" stroke="#666" '
                   f'stroke-width="{stroke / 2}" stroke-dasharray="{stroke * 6},{stroke * 4}"/>')
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{p.params.base_radius}" fill="none" stroke="#999" '
                   f'stroke-width="{stroke / 2}" stroke-dasharray="{stroke},{stroke * 3}"/>')
        tip = _polar(p.center, max(p.tip_radius, p.root_radius), p.theta0)
        out.append(f'<line x1="{cx}" y1="{cy}" x2="{tip[0]}" y2="{-tip[1]}" stroke="red" '
                   f'stroke-width="{stroke / 2}"/>')
    out.append('</svg>')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out))
