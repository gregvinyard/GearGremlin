"""Pure-Python involute spur gear math for GearGremlin.

No `adsk` imports. All lengths are millimetres, all angles radians.
See SPEC.md for the formulas and conventions used here.
"""
from __future__ import annotations

import bisect
import functools
import json
import math
from dataclasses import dataclass, field, replace
from typing import Optional, Union

Point = tuple[float, float]

STANDARD_MODULES_MM: list[float] = [0.5, 0.8, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0]
PRESSURE_ANGLES_DEG: list[float] = [14.5, 20.0, 25.0]
HEIGHT_FACTORS: dict[str, float] = {'Standard': 1.0, 'Stub': 0.8}
MIN_TEETH = 6
MAX_TEETH = 400
TANGENCY_TOL_MM = 0.001
ATTR_VERSION = 3
PLAN_VERSION = 1

EXTERNAL = 'external'
INTERNAL = 'internal'

CUTTER_TIP_RADIUS = 0.38    # × module: ISO 53 profile A basic rack
MIN_TOP_LAND = 0.2          # × module: narrowest allowed tooth tip
FACTOR_FLOOR = 0.6          # lowest factor allowed without a partner to check against
FACTOR_SCAN_MIN = 0.3
FACTOR_CAP = 2.0
CONTACT_RATIO_GOOD = 1.2
CONTACT_RATIO_MIN = 1.0     # below this a pair can't run smoothly at all
TRIM_MARGIN = 0.01          # × module: minimum clearance a ring trim leaves
PLANET_GAP = 0.25           # × module: minimum gap between neighbouring planets' tips
ANGLE_TOL = 1e-9            # radians: conjugate flank contact counts as touching, not colliding


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
    height_factor: float = 1.0

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
    def addendum(self) -> float:
        return self.height_factor * self.module

    @property
    def dedendum(self) -> float:
        return (self.height_factor + 0.25) * self.module

    @property
    def tip_radius(self) -> float:
        """Nominal tip radius (before any clamping or trim)."""
        if self.internal:
            return self.pitch_radius - self.addendum
        return self.pitch_radius + self.addendum

    @property
    def root_radius(self) -> float:
        """Nominal root radius (before any clamping)."""
        if self.internal:
            return self.pitch_radius + self.dedendum
        return self.pitch_radius - self.dedendum

    @property
    def half_angle(self) -> float:
        """ψ (external: half tooth thickness) or ψs (internal: half space width), at the pitch circle."""
        base = math.pi / (2.0 * self.teeth)
        correction = self.backlash / (4.0 * self.pitch_radius)
        return base + correction if self.internal else base - correction

    def with_factor(self, k: float) -> 'GearParams':
        return replace(self, height_factor=k)


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


def _wrap(a: float) -> float:
    return (a + math.pi) % (2.0 * math.pi) - math.pi


# ---------------------------------------------------------------------------
# Generating rack cutter and the generated root (external gears)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Cutter:
    """Basic rack cutter (ISO 53 profile A) sized for one gear. Depths are below the pitch line."""
    depth: float            # hf: tip line depth
    corner_radius: float    # ρc
    half_width: float       # w: half tooth width at the pitch line
    corner_x: float         # x of the corner circle's center, from the cutter tooth's centerline
    corner_depth: float     # depth of the corner circle's center
    straight_depth: float   # hs: depth where the straight flank ends
    valid: bool             # False if the cutter tooth comes to a point above its tip line


def cutter(params: GearParams) -> Cutter:
    return _cutter(params.module, params.pressure_angle, params.dedendum, params.backlash)


def _cutter(m: float, a: float, hf: float, backlash: float) -> Cutter:
    """The cutter for module m, pressure angle a, depth hf and backlash (shared by gears and racks)."""
    w = math.pi * m / 4.0 + backlash / 4.0
    tip_half = w - hf * math.tan(a)
    if tip_half <= 0.0:
        return Cutter(hf, 0.0, w, 0.0, hf, hf, False)
    rho = min(CUTTER_TIP_RADIUS * m, tip_half / (1.0 / math.cos(a) - math.tan(a)))
    d_c = hf - rho
    x_c = max(0.0, w - d_c * math.tan(a) - rho / math.cos(a))
    return Cutter(hf, rho, w, x_c, d_c, hf - rho * (1.0 - math.sin(a)), True)


def undercut_threshold(params: GearParams) -> float:
    """Teeth below this are undercut by the cutter: 2·hs / (m·sin²α)."""
    return 2.0 * cutter(params).straight_depth / (params.module * math.sin(params.pressure_angle) ** 2)


def undercut_min_teeth(params: GearParams) -> int:
    """Whole-tooth form of the undercut threshold (17 at 20°, k = 1)."""
    return int(undercut_threshold(params))


@dataclass(frozen=True)
class RootShape:
    """Tooth half-angle below the involute: from the root circle up to the form radius."""
    form: float
    rhos: tuple            # ascending, root radius .. form radius
    halves: tuple

    def half_at(self, rho: float) -> float:
        i = bisect.bisect_left(self.rhos, rho)
        if i <= 0:
            return self.halves[0]
        if i >= len(self.rhos):
            return self.halves[-1]
        r0, r1 = self.rhos[i - 1], self.rhos[i]
        h0, h1 = self.halves[i - 1], self.halves[i]
        return h0 + (h1 - h0) * (rho - r0) / (r1 - r0) if r1 > r0 else h1


def _gear_frame(q: Point, t: float, tau: float) -> tuple[float, float]:
    """Rack-world point at roll angle t → (ρ, tooth half-angle) in the gear's frame.

    The tooth space is centered on +y; the tooth whose flank this is sits clockwise of it.
    """
    c, s = math.cos(-t), math.sin(-t)
    x, y = q[0] * c - q[1] * s, q[0] * s + q[1] * c
    return math.hypot(x, y), math.atan2(y, x) - math.pi / 2.0 + tau / 2.0


def _envelope_families(params: GearParams, samples: int = 240) -> tuple[float, list[list[tuple[float, float]]]]:
    """Cutter contact curves below the involute, as (ρ, half-angle) polylines, and the lowest radius
    the involute is still generated at.

    Families: the straight flank past the involute's cusp (only when undercut), and the corner
    circle's envelope, where the contact normal passes through the pitch point.
    """
    cut = cutter(params)
    r, a, tau = params.pitch_radius, params.pressure_angle, params.angular_pitch
    sa, ca = math.sin(a), math.cos(a)
    w, hs, d_c, x_c, rho = cut.half_width, cut.straight_depth, cut.corner_depth, cut.corner_x, cut.corner_radius

    def flank_point(depth: float) -> tuple[float, float]:
        length = depth / (sa * ca)          # = w − r·t
        t = (w - length) / r
        return _gear_frame((length * ca * ca, r - length * sa * ca), t, tau)

    d_cusp = r * sa * sa                     # depth where the line of action touches the base circle
    involute_low = flank_point(min(hs, d_cusp))[0]
    families = []
    if hs > d_cusp:
        families.append([flank_point(d_cusp + (hs - d_cusp) * i / samples) for i in range(samples + 1)])
    corner = []
    x_top = 3.0 * d_c / math.tan(a)
    for i in range(samples + 1):
        x = x_top * (1.0 - i / samples)      # = x_c − r·t
        t = (x_c - x) / r
        n = math.hypot(x, d_c)
        q = (x + rho * x / n, r - d_c - rho * d_c / n)
        corner.append(_gear_frame(q, t, tau))
    families.append(corner)
    return involute_low, families


def _family_min(families: list[list[tuple[float, float]]], rho: float) -> Optional[float]:
    best = None
    for fam in families:
        for (r0, h0), (r1, h1) in zip(fam, fam[1:]):
            lo, hi = (r0, r1) if r0 <= r1 else (r1, r0)
            if lo <= rho <= hi:
                h = h0 if hi == lo else h0 + (h1 - h0) * (rho - r0) / (r1 - r0)
                if best is None or h < best:
                    best = h
    return best


@functools.lru_cache(maxsize=1024)
def root_shape(params: GearParams, table_size: int = 64) -> RootShape:
    """The generated root of an external gear: the region the rack cutter leaves below the involute."""
    involute_low, families = _envelope_families(params)
    rf = params.root_radius

    # Form radius: the highest crossing of a cutter curve with the involute, or where the
    # involute stops being generated.
    form = involute_low
    for fam in families:
        prev = None
        for rho, h in fam:
            g = h - flank_angle(params, rho) if rho >= involute_low else None
            if prev is not None and g is not None and prev[1] is not None and (prev[1] < 0) != (g < 0):
                r0, g0 = prev
                form = max(form, r0 + (rho - r0) * g0 / (g0 - g))
            prev = (rho, g)

    # The corner envelope's lowest point is on the root circle; use it where sampling misses.
    bottom = min((pt for fam in families for pt in fam), key=lambda pt: pt[0])
    rhos, halves = [], []
    for i in range(table_size + 1):
        rho = rf + (form - rf) * (i / table_size) ** 2
        h = _family_min(families, rho)
        if rho >= involute_low:
            inv = flank_angle(params, rho)
            h = inv if h is None else min(h, inv)
        if h is None:
            h = bottom[1] if rho <= bottom[0] + 1e-9 else (halves[-1] if halves else bottom[1])
        rhos.append(rho)
        halves.append(h)
    halves[-1] = flank_angle(params, form)
    return RootShape(form, tuple(rhos), tuple(halves))


# ---------------------------------------------------------------------------
# Drawn radii: one calculation for tip, root and form
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Trim:
    """What one pinion requires of a ring's tip."""
    pinion: GearParams
    required_tip: float     # smallest ring tip radius that clears (0 if no trim is needed)
    limited_by: str         # 'root' (ring tip into pinion root), 'tip' (pinion tip into ring), or ''
    clears: bool            # False if no trim can clear (it would remove the whole addendum)


@dataclass(frozen=True)
class Radii:
    tip: float
    root: float
    form: float
    untrimmed_tip: float
    warnings: tuple = ()
    trim: Optional[Trim] = None       # the pinion that set a ring's trim, if trimmed
    failed: tuple = ()                # trims that can't clear

    @property
    def trimmed_by(self) -> float:
        return self.tip - self.untrimmed_tip


def tooth_half_angle(params: GearParams, rho: float) -> float:
    """Half-angle of an external gear's tooth at ρ, as drawn (involute above the form radius)."""
    rs = root_shape(params)
    return rs.half_at(rho) if rho < rs.form else flank_angle(params, rho)


@functools.lru_cache(maxsize=1024)
def gear_radii(params: GearParams, pinions: tuple = ()) -> Radii:
    """Tip, root and form radii as drawn. `pinions` (rings only) are the external gears to trim against."""
    warnings = []
    if params.internal:
        tip = params.tip_radius
        if tip < params.base_radius:
            tip = params.base_radius
            warnings.append('Internal tips clamped to the base circle (teeth slightly short).')
        root = params.root_radius
        pointed = radius_where_pointed(params)
        if pointed < root:
            root = pointed
            warnings.append('Tooth spaces come to a point; root clamped.')
        untrimmed = tip
        trims = [ring_trim(params, p) for p in pinions]
        ok = [t for t in trims if t.clears]
        worst = max(ok, key=lambda t: t.required_tip) if ok else None
        if worst is not None and worst.required_tip > tip:
            tip = worst.required_tip
        else:
            worst = None
        failed = tuple(t for t in trims if not t.clears)
        return Radii(tip, root, tip, untrimmed, tuple(warnings), worst, failed)

    tip = params.tip_radius
    pointed = radius_where_pointed(params)
    if pointed < tip:
        tip = pointed
        warnings.append('Teeth come to a point; tip clamped.')
    form = root_shape(params).form
    return Radii(tip, params.root_radius, form, tip, tuple(warnings))


# ---------------------------------------------------------------------------
# Material tests and the ring tip trim
# ---------------------------------------------------------------------------

def _nearest_center(angle: float, first: float, step: float) -> float:
    return first + round((angle - first) / step) * step


def _inside_external(params: GearParams, radii: Radii, theta0: float, rho: float, angle: float) -> bool:
    """Is a point (polar, gear frame) inside an external gear's material?"""
    if rho <= radii.root - 1e-9:
        return True
    if rho >= radii.tip:
        return False
    rel = angle - _nearest_center(angle, theta0, params.angular_pitch)
    return abs(rel) < tooth_half_angle(params, rho) - ANGLE_TOL


def _inside_ring_tooth_span(params: GearParams, theta0: float, rho: float, angle: float) -> bool:
    """Is a point between a ring tooth's flanks at this radius (ignoring where the tip is)?"""
    if rho < params.base_radius:
        return False
    rel = angle - _nearest_center(angle, theta0, params.angular_pitch)
    return abs(rel) < params.angular_pitch / 2.0 - flank_angle(params, rho) - ANGLE_TOL


def _external_boundary(params: GearParams, radii: Radii) -> list[tuple[Point, str]]:
    """Boundary points of one tooth (centered on angle 0) and its root, in the gear's frame.

    Kind 'work' = involute and tip; 'root' = generated root and root circle.
    """
    pts: list[tuple[Point, str]] = []
    for rho in flank_radii(radii.form, radii.tip, 12):
        h = flank_angle(params, rho)
        pts += [((rho * math.cos(h), rho * math.sin(h)), 'work'), ((rho * math.cos(-h), rho * math.sin(-h)), 'work')]
    ht = flank_angle(params, radii.tip)
    for i in range(5):
        ang = -ht + 2 * ht * i / 4
        pts.append(((radii.tip * math.cos(ang), radii.tip * math.sin(ang)), 'work'))
    for (x, y) in _root_polyline(params):
        pts += [((x, y), 'root'), ((x, -y), 'root')]
    return pts


@functools.lru_cache(maxsize=1024)
def _root_polyline(params: GearParams, below: float = 0.0) -> tuple:
    """One side of an external tooth's non-working surface, tooth on angle 0: the generated root
    from `below` under the form radius down to the root circle, then the root circle out to the
    middle of the space."""
    rs = root_shape(params)
    pts = [(rho * math.cos(h), rho * math.sin(h)) for rho, h in zip(reversed(rs.rhos), reversed(rs.halves))
           if rho <= rs.form - below or rho == rs.rhos[0]]
    h_root = rs.halves[0]
    for i in range(1, 9):
        ang = h_root + (params.angular_pitch / 2 - h_root) * i / 8
        pts.append((params.root_radius * math.cos(ang), params.root_radius * math.sin(ang)))
    return tuple(pts)


def _near_root(params: GearParams, form: float, rho: float, rel: float, margin: float) -> bool:
    """Is a point (tooth-relative polar) within `margin` of the tooth's non-working surface?

    The surface within one margin of the form radius is left out: there the root meets the
    working involute, whose clearance is set by backlash, not by the trim.
    """
    if rho > form:
        return False
    px, py = rho * math.cos(abs(rel)), rho * math.sin(abs(rel))
    poly = _root_polyline(params, round(margin, 9))
    m2 = margin * margin
    for (x0, y0), (x1, y1) in zip(poly, poly[1:]):
        dx, dy = x1 - x0, y1 - y0
        L = dx * dx + dy * dy
        t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / L))
        ex, ey = x0 + t * dx - px, y0 + t * dy - py
        if ex * ex + ey * ey < m2:
            return True
    return False


def trim_margin(ring: GearParams, pinion: GearParams) -> float:
    backlash = (ring.backlash + pinion.backlash) / 2.0
    return max(TRIM_MARGIN * ring.module, backlash * math.cos(ring.pressure_angle) / 4.0)


class _Mesh:
    """A ring and pinion at standard spacing: ring at the origin, pinion center on +y, phases in pinion teeth."""

    def __init__(self, ring: GearParams, pinion: GearParams):
        self.ring, self.pinion = ring, pinion
        self.a = ring.pitch_radius - pinion.pitch_radius
        self.theta0_r = align_theta0(ring, (0.0, 0.0), pinion, (0.0, self.a), 0.0)
        self.pin_radii = gear_radii(pinion)

    def angles(self, s: float) -> tuple[float, float]:
        phi_p = s * self.pinion.angular_pitch
        return phi_p, phi_p * self.pinion.teeth / self.ring.teeth

    def ring_tip_points(self, tip: float) -> list[tuple[float, float]]:
        """Ring tooth tip arc and the flank just below it, tooth on angle 0, as (ρ, angle) in the ring frame."""
        ring = self.ring
        ht = ring.angular_pitch / 2.0 - flank_angle(ring, tip)
        pts = [(tip, -ht + 2 * ht * i / 8) for i in range(9)]
        for rho in (tip + 0.05 * ring.module, tip + 0.15 * ring.module):
            if rho < ring.pitch_radius:
                h = ring.angular_pitch / 2.0 - flank_angle(ring, rho)
                pts += [(rho, h), (rho, -h)]
        return pts

    def tip_clears(self, tip: float, margin: float, phase_list) -> bool:
        """Do the ring's tips (at this radius) stay out of the pinion and its root margin at these phases?"""
        ring, pin, pr = self.ring, self.pinion, self.pin_radii
        pts = self.ring_tip_points(tip)
        # Only ring teeth that can reach the pinion: within its tip circle as seen from the ring center.
        reach = (pr.tip + ring.module) / self.a
        window = math.asin(reach) + 0.05 if reach < 1.0 else math.pi / 2.0
        for s in phase_list:
            phi_p, phi_r = self.angles(s)
            for j in range(ring.teeth):
                rot = self.theta0_r + j * ring.angular_pitch + phi_r
                if abs(_wrap(rot - math.pi / 2.0)) > window:
                    continue
                for rho_r, ang in pts:
                    wx, wy = rho_r * math.cos(rot + ang), rho_r * math.sin(rot + ang)
                    rho = math.hypot(wx, wy - self.a)
                    pa = math.atan2(wy - self.a, wx) - phi_p
                    rel = pa - _nearest_center(pa, 0.0, pin.angular_pitch)
                    if rho <= pr.root - 1e-9:
                        return False
                    if rho < pr.tip and abs(rel) < tooth_half_angle(pin, rho) - ANGLE_TOL:
                        return False
                    if margin > 0 and _near_root(pin, pr.form, rho, rel, margin):
                        return False
        return True

    def pinion_tip_demand(self, phases: int) -> float:
        """Largest ring radius at which the pinion's working surface enters a ring tooth's span.

        The ring's tip must be above this, or the pinion's tips hit the ring's teeth.
        """
        ring, pin = self.ring, self.pinion
        pts = [p for p, kind in _external_boundary(pin, self.pin_radii) if kind == 'work']
        need = 0.0
        for i in range(phases):
            phi_p, phi_r = self.angles(i / phases)
            for j in range(pin.teeth):
                rot = phi_p + j * pin.angular_pitch
                if abs(_wrap(rot - math.pi / 2.0)) > math.radians(110):
                    continue
                c, sn = math.cos(rot), math.sin(rot)
                for x, y in pts:
                    wx, wy = x * c - y * sn, self.a + x * sn + y * c
                    rho = math.hypot(wx, wy)
                    if rho <= need or rho < ring.base_radius:
                        continue
                    ang = math.atan2(wy, wx) - phi_r
                    rel = ang - _nearest_center(ang, self.theta0_r, ring.angular_pitch)
                    if abs(rel) < ring.angular_pitch / 2.0 - flank_angle(ring, rho) - ANGLE_TOL:
                        need = rho
        return need


@functools.lru_cache(maxsize=512)
def ring_trim(ring: GearParams, pinion: GearParams, phases: int = 40) -> Trim:
    """Smallest ring tip radius that clears this pinion over a full tooth of rotation (see SPEC)."""
    mesh = _Mesh(ring, pinion)
    margin = trim_margin(ring, pinion)
    base_tip = gear_radii(ring).tip
    top = ring.pitch_radius - 1e-6

    tip_need = mesh.pinion_tip_demand(phases)
    if tip_need > 0:
        tip_need += margin
    if tip_need >= top:
        return Trim(pinion, tip_need, 'tip', False)

    # Raise the tip phase by phase: each phase only needs a bisection if it beats the current bound.
    start = max(base_tip, tip_need)
    bound = start
    for i in range(phases):
        phase_i = (i / phases,)
        if mesh.tip_clears(bound, margin, phase_i):
            continue
        if not mesh.tip_clears(top, margin, phase_i):
            return Trim(pinion, top, 'root', False)
        lo, hi = bound, top
        for _ in range(16):
            mid = (lo + hi) / 2.0
            if mesh.tip_clears(mid, margin, phase_i):
                hi = mid
            else:
                lo = mid
        bound = hi
    if bound > start:
        return Trim(pinion, bound, 'root', True)
    need, kind = (tip_need, 'tip') if tip_need > base_tip else (0.0, '')
    return Trim(pinion, need, kind, True)


def pair_collides(a: GearParams, b: GearParams, phases: int = 48) -> bool:
    """Do two external gears, as drawn and meshed at standard spacing, collide over a full tooth?"""
    ra_, rb_ = gear_radii(a), gear_radii(b)
    dist = a.pitch_radius + b.pitch_radius
    tb = 0.0
    ta = align_theta0(a, (0.0, 0.0), b, (dist, 0.0), tb)
    pts_a = _external_boundary(a, ra_)
    pts_b = _external_boundary(b, rb_)
    for i in range(phases):
        phi_a = i / phases * a.angular_pitch
        phi_b = -phi_a * a.teeth / b.teeth
        for pts, own, own_t, own_c, other, other_r, other_t, other_c in (
                (pts_a, a, ta + phi_a, (0.0, 0.0), b, rb_, tb + phi_b, (dist, 0.0)),
                (pts_b, b, tb + phi_b, (dist, 0.0), a, ra_, ta + phi_a, (0.0, 0.0))):
            facing = math.atan2(other_c[1] - own_c[1], other_c[0] - own_c[0])
            for j in range(own.teeth):
                rot = own_t + j * own.angular_pitch
                if abs(_wrap(rot - facing)) > math.radians(100):
                    continue
                c, s = math.cos(rot), math.sin(rot)
                for (x, y), _ in pts:
                    wx, wy = own_c[0] + x * c - y * s, own_c[1] + x * s + y * c
                    rho = math.hypot(wx - other_c[0], wy - other_c[1])
                    ang = math.atan2(wy - other_c[1], wx - other_c[0])
                    if _inside_external(other, other_r, other_t, rho, ang):
                        return True
    return False


# ---------------------------------------------------------------------------
# Contact ratio and the tooth height factor's range
# ---------------------------------------------------------------------------

def contact_ratio(a: GearParams, a_radii: Radii, b: GearParams, b_radii: Radii,
                  center_distance: Optional[float] = None) -> float:
    """Average number of tooth pairs in contact, from the drawn profiles (see SPEC)."""
    if center_distance is None:
        center_distance = expected_center_distance(a, b)
    pb = math.pi * a.module * math.cos(a.pressure_angle)

    def s(radius: float, rb: float) -> float:
        return math.sqrt(max(radius * radius - rb * rb, 0.0))

    if a.internal or b.internal:
        ring, rr, pin, pr = (a, a_radii, b, b_radii) if a.internal else (b, b_radii, a, a_radii)
        d = math.sqrt(max(center_distance ** 2 - (ring.base_radius - pin.base_radius) ** 2, 0.0))
        ring_lo, ring_hi = s(rr.tip, ring.base_radius) - d, s(rr.root, ring.base_radius) - d
        pin_lo, pin_hi = s(pr.form, pin.base_radius), s(pr.tip, pin.base_radius)
    else:
        d = math.sqrt(max(center_distance ** 2 - (a.base_radius + b.base_radius) ** 2, 0.0))
        ring_lo, ring_hi = d - s(b_radii.tip, b.base_radius), d - s(b_radii.form, b.base_radius)
        pin_lo, pin_hi = s(a_radii.form, a.base_radius), s(a_radii.tip, a.base_radius)
    return max(0.0, min(ring_hi, pin_hi) - max(ring_lo, pin_lo)) / pb


def pair_radii(a: GearParams, b: GearParams) -> tuple[Radii, Radii]:
    """Drawn radii for a meshing pair, with a ring trimmed against its pinion."""
    if a.internal:
        return gear_radii(a, (b,)), gear_radii(b)
    if b.internal:
        return gear_radii(a), gear_radii(b, (a,))
    return gear_radii(a), gear_radii(b)


def pair_contact_ratio(a: GearParams, b: GearParams, phases: int = 40) -> float:
    """Contact ratio of a pair as drawn. Fewer phases give a faster, slightly coarser ring trim."""
    if phases == 40 or not (a.internal or b.internal):
        ra_, rb_ = pair_radii(a, b)
        return contact_ratio(a, ra_, b, rb_)
    ring, pin = (a, b) if a.internal else (b, a)
    base = gear_radii(ring)
    trim = ring_trim(ring, pin, phases)
    tip = max(base.tip, trim.required_tip) if trim.clears else base.tip
    return contact_ratio(ring, replace(base, tip=tip, form=tip), pin, gear_radii(pin))


def top_land(params: GearParams) -> float:
    """Width of the tooth tip at the nominal tip (clamps and trim excluded)."""
    if params.internal:
        tip = max(params.tip_radius, params.base_radius)
        return 2.0 * tip * (params.angular_pitch / 2.0 - flank_angle(params, tip))
    tip = params.tip_radius
    return 2.0 * tip * flank_angle(params, tip)


def factor_geometry_ok(params: GearParams) -> bool:
    """Is the tooth valid at its factor: wide enough tip, and (external) working involute left?"""
    if top_land(params) < MIN_TOP_LAND * params.module - 1e-12:
        return False
    if not params.internal:
        if not cutter(params).valid or params.root_radius <= 0:
            return False
        if root_shape(params).form >= params.tip_radius:
            return False
    return True


def _boundary(lo: float, hi: float, ok_at, iterations: int = 14) -> float:
    """Bisect between lo (ok) and hi (not ok)."""
    for _ in range(iterations):
        mid = (lo + hi) / 2.0
        if ok_at(mid):
            lo = mid
        else:
            hi = mid
    return lo


@functools.lru_cache(maxsize=1024)
def factor_max(params: GearParams) -> float:
    """Largest tooth height factor for this gear (see SPEC)."""
    def ok(k: float) -> bool:
        return factor_geometry_ok(params.with_factor(k))
    k, step = FACTOR_SCAN_MIN, 0.05
    if not ok(k):
        return FACTOR_SCAN_MIN
    while k + step <= FACTOR_CAP + 1e-9:
        if not ok(k + step):
            return math.floor(_boundary(k, k + step, ok) * 1e4) / 1e4
        k += step
    return FACTOR_CAP


def _pair_key(a: GearParams, b: GearParams) -> tuple:
    return a.with_factor(1.0), b.with_factor(1.0)


@functools.lru_cache(maxsize=512)
def _factor_min_pair(a: GearParams, b: GearParams) -> Optional[float]:
    k_hi = min(factor_max(a), factor_max(b))

    def ok(k: float) -> bool:
        return pair_contact_ratio(a.with_factor(k), b.with_factor(k), phases=20) >= CONTACT_RATIO_GOOD

    if not ok(k_hi):
        return None
    k = k_hi
    while k - 0.1 >= FACTOR_SCAN_MIN:
        if not ok(k - 0.1):
            # boundary between k - 0.1 (not ok) and k (ok)
            lo, hi = k - 0.1, k
            for _ in range(7):
                mid = (lo + hi) / 2.0
                if ok(mid):
                    hi = mid
                else:
                    lo = mid
            return round(hi, 3)
        k -= 0.1
    return FACTOR_SCAN_MIN


def factor_min_pair(a: GearParams, b: GearParams) -> Optional[float]:
    """Smallest shared factor at which the pair's contact ratio reaches 1.2, or None if none does."""
    return _factor_min_pair(*_pair_key(a, b))


def factor_range(params: GearParams, partner: Optional[GearParams] = None) -> tuple[Optional[float], float]:
    """(minimum, maximum) valid tooth height factor. Minimum is None when no factor works for the pair."""
    if partner is None:
        return FACTOR_FLOOR, factor_max(params.with_factor(1.0))
    hi = min(factor_max(params.with_factor(1.0)), factor_max(partner.with_factor(1.0)))
    return factor_min_pair(params, partner), hi


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
    """The arc from start_angle sweeping `sweep` (> 0) radians counter-clockwise.

    With `clockwise`, the same arc is traversed the other way: start and end swap.
    """
    center: Point
    radius: float
    start_angle: float
    sweep: float
    clockwise: bool = False

    def point_at(self, angle: float) -> Point:
        return (self.center[0] + self.radius * math.cos(angle),
                self.center[1] + self.radius * math.sin(angle))

    @property
    def start(self) -> Point:
        return self.point_at(self.start_angle + (self.sweep if self.clockwise else 0.0))

    @property
    def mid(self) -> Point:
        return self.point_at(self.start_angle + self.sweep / 2.0)

    @property
    def end(self) -> Point:
        return self.point_at(self.start_angle + (0.0 if self.clockwise else self.sweep))


Segment = Union[Line, Spline, Arc]


@dataclass
class Profile:
    """A closed loop of segments. Each segment's end equals the next one's start."""
    params: GearParams
    center: Point
    theta0: float
    segments: list[Segment]
    radii: Radii
    warnings: list[str] = field(default_factory=list)

    @property
    def tip_radius(self) -> float:
        return self.radii.tip

    @property
    def root_radius(self) -> float:
        return self.radii.root


def _polar(center: Point, rho: float, angle: float) -> Point:
    return (center[0] + rho * math.cos(angle), center[1] + rho * math.sin(angle))


def flank_radii(r_start: float, r_end: float, count: int) -> list[float]:
    """`count` radii from r_start to r_end, denser near r_start (where involute curvature is highest)."""
    count = max(count, 2)
    return [r_start + (r_end - r_start) * (i / (count - 1)) ** 2 for i in range(count)]


def _root_curve(params: GearParams, count: int) -> list[tuple[float, float]]:
    """(ρ, half-angle) points of the generated root from the root circle up to the form radius."""
    rs = root_shape(params)
    n = len(rs.rhos) - 1
    idx = sorted({round(n * i / (count - 1)) for i in range(count)})
    return [(rs.rhos[i], rs.halves[i]) for i in idx]


def build_profile(params: GearParams, center: Point = (0.0, 0.0), theta0: float = 0.0,
                  points_per_flank: int = 10, radii: Optional[Radii] = None) -> Profile:
    """Build the closed tooth profile (see SPEC: "Gear geometry")."""
    if radii is None:
        radii = gear_radii(params)
    tau = params.angular_pitch
    segments: list[Segment] = []
    if params.internal:
        inner, outer = radii.tip, radii.root
        half_inner = flank_angle(params, inner)
        half_outer = flank_angle(params, outer)
        radii_list = flank_radii(inner, outer, points_per_flank)
        for k in range(params.teeth):
            c = theta0 + tau / 2.0 + k * tau
            segments.append(Spline([_polar(center, rho, c - flank_angle(params, rho)) for rho in radii_list]))
            segments.append(Arc(center, outer, c - half_outer, 2.0 * half_outer))
            segments.append(Spline([_polar(center, rho, c + flank_angle(params, rho))
                                    for rho in reversed(radii_list)]))
            segments.append(Arc(center, inner, c + half_inner, tau - 2.0 * half_inner))
        return Profile(params, center, theta0, segments, radii, list(radii.warnings))

    root = _root_curve(params, max(6, points_per_flank - 2))
    involute = flank_radii(radii.form, radii.tip, points_per_flank)
    half_tip = flank_angle(params, radii.tip)
    h_root = root[0][1]
    root_gap = tau - 2.0 * h_root
    for k in range(params.teeth):
        c = theta0 + k * tau
        segments.append(Spline([_polar(center, rho, c - h) for rho, h in root]))
        segments.append(Spline([_polar(center, rho, c - flank_angle(params, rho)) for rho in involute]))
        segments.append(Arc(center, radii.tip, c - half_tip, 2.0 * half_tip))
        segments.append(Spline([_polar(center, rho, c + flank_angle(params, rho)) for rho in reversed(involute)]))
        segments.append(Spline([_polar(center, rho, c + h) for rho, h in reversed(root)]))
        if root_gap > 1e-6 / max(radii.root, 1e-9):
            segments.append(Arc(center, radii.root, c + h_root, root_gap))
    return Profile(params, center, theta0, segments, radii, list(radii.warnings))


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
# Validation
# ---------------------------------------------------------------------------

@dataclass
class Check:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)

    def extend(self, other: 'Check') -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        self.infos.extend(other.infos)

    @property
    def ok(self) -> bool:
        return not self.errors


def same_system(a: GearParams, b: GearParams) -> bool:
    return (abs(a.module - b.module) < 1e-9 and abs(a.pressure_angle - b.pressure_angle) < 1e-9
            and abs(a.height_factor - b.height_factor) < 1e-9)


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
    k_max = factor_max(params.with_factor(1.0))
    if params.height_factor > k_max + 1e-9:
        check.errors.append(f'Tooth height {params.height_factor:g} is above the maximum {k_max:.2f} for '
                            f'{params.teeth} teeth (the tips would get too narrow).')
        return check
    if params.height_factor < FACTOR_FLOOR - 1e-9:
        check.errors.append(f'Tooth height {params.height_factor:g} is below the minimum {FACTOR_FLOOR:g}.')
        return check
    if not params.internal and params.teeth < undercut_threshold(params):
        check.warnings.append(f'Under {undercut_min_teeth(params) + 1} teeth, the root is undercut (as on a hobbed '
                              'gear) so mating teeth clear it. Undercut teeth are thinner at the base and weaker.')
    check.warnings.extend(gear_radii(params).warnings)
    return check


def check_sizing(circle_diameter: float, params: GearParams, resize: bool) -> Check:
    check = Check()
    pitch_d = 2.0 * params.pitch_radius
    if not resize and abs(pitch_d - circle_diameter) > TANGENCY_TOL_MM:
        check.warnings.append('Resize is off: the gear won\'t match the drawn circle.')
    return check


def _match_errors(new, partner) -> list[str]:
    """Module, pressure angle and tooth height must match for any meshing pair (gears or a rack)."""
    errors = []
    if abs(new.module - partner.module) > 1e-9:
        errors.append('Module doesn\'t match the mesh partner.')
    if abs(new.pressure_angle - partner.pressure_angle) > 1e-9:
        errors.append('Pressure angle doesn\'t match the mesh partner.')
    if abs(new.height_factor - partner.height_factor) > 1e-9:
        errors.append(f'Tooth height doesn\'t match the mesh partner ({partner.height_factor:g}).')
    return errors


def check_mesh(new: GearParams, new_center: Point,
               partner: GearParams, partner_center: Point, partner_radius: float) -> Check:
    """Validate a mesh pair's compatibility and placement. partner_radius is the partner circle's radius."""
    check = Check()
    check.errors += _match_errors(new, partner)
    if new.internal and partner.internal:
        check.errors.append('Two internal gears can\'t mesh.')
    if check.errors:
        return check
    if new.internal or partner.internal:
        ring, pinion = (new, partner) if new.internal else (partner, new)
        if ring.teeth <= pinion.teeth:
            check.errors.append('The ring gear needs more teeth than the pinion.')
            return check
    dist = math.hypot(new_center[0] - partner_center[0], new_center[1] - partner_center[1])
    if dist < TANGENCY_TOL_MM:
        check.errors.append('Gears share a center, so there\'s no contact point to align to.')
        return check
    if abs(dist - expected_center_distance(new, partner)) > TANGENCY_TOL_MM:
        check.warnings.append('Circles aren\'t tangent: the gears won\'t mesh at this spacing.')
    if abs(partner_radius - partner.pitch_radius) > TANGENCY_TOL_MM:
        check.warnings.append('Mesh partner was resized after it was made.')
    return check


def _min_ring_teeth(pinion: GearParams, ring: GearParams) -> Optional[int]:
    """Smallest ring tooth count, from this ring's up, whose trim clears this pinion."""
    for z in range(ring.teeth + 1, ring.teeth + 41):
        if ring_trim(replace(ring, teeth=z), pinion).clears:
            return z
    return None


def _min_pinion_teeth(ring: GearParams, pinion: GearParams, good_ratio: bool) -> Optional[int]:
    """Smallest pinion tooth count, from this pinion's up, that clears this ring (and reaches 1.2)."""
    for z in range(pinion.teeth + 1, ring.teeth - 1):
        p = replace(pinion, teeth=z)
        trim = ring_trim(ring, p)
        if not trim.clears:
            continue
        if good_ratio and pair_contact_ratio(ring, p) < CONTACT_RATIO_GOOD:
            continue
        return z
    return None


def trim_failure_message(ring: GearParams, trim: Trim) -> str:
    pinion = trim.pinion
    if trim.limited_by == 'tip':
        n = _min_ring_teeth(pinion, ring)
        need = f' With this pinion the ring needs at least {n} teeth.' if n else ''
        return f'The {pinion.teeth}-tooth pinion\'s tips hit the ring\'s teeth, and trimming the ring can\'t fix it.{need}'
    n = _min_pinion_teeth(ring, pinion, good_ratio=False)
    need = f' Use at least {n} pinion teeth.' if n else ''
    return f'The ring can\'t be trimmed enough to clear the {pinion.teeth}-tooth pinion.{need}'


def pair_report(new: GearParams, new_radii: Radii, partner: GearParams,
                partner_tip: Optional[float] = None, planned: bool = False) -> Check:
    """Contact ratio and interference messages for a gear and its Mesh with partner.

    partner_tip is the partner's tip radius as drawn (from its attribute); None means compute it.
    planned: the gear belongs to a planetary set, where changing one tooth count changes the
    ring too, so the fix points to the planetary helper instead of a pinion tooth count.
    """
    check = Check()
    if new.internal or partner.internal:
        ring, pinion = (new, partner) if new.internal else (partner, new)
        trim = ring_trim(ring, pinion)
        if not trim.clears:
            check.warnings.append(trim_failure_message(ring, trim))
        if new.internal:
            ring_radii, pin_radii = new_radii, gear_radii(pinion)
        else:
            base = gear_radii(ring)
            tip = partner_tip if partner_tip else base.tip
            ring_radii, pin_radii = replace(base, tip=tip, form=tip), new_radii
            if trim.clears and trim.required_tip > tip + 1e-6:
                check.warnings.append(f'The ring\'s tips hit this pinion. Edit the ring and set Mesh with to this '
                                      f'{pinion.teeth}-tooth gear to re-trim it.')
        cr = contact_ratio(ring, ring_radii, pinion, pin_radii)
    else:
        cr = contact_ratio(new, new_radii, partner, gear_radii(partner))
    check.infos.append(f'Contact ratio {cr:.2f}')
    if cr < CONTACT_RATIO_GOOD:
        k_min = factor_min_pair(new, partner)
        if k_min is not None and k_min > new.height_factor + 1e-9:
            fix = (f'Remake the partner, then this gear, with a tooth height factor of at least {k_min:.2f}.')
        elif planned:
            fix = 'Run Planetary Set to see nearby tooth counts that reach 1.2.'
        elif new.internal or partner.internal:
            ring, pinion = (new, partner) if new.internal else (partner, new)
            n = _min_pinion_teeth(ring, pinion, good_ratio=True)
            fix = f'Use at least {n} pinion teeth.' if n else 'Use more pinion teeth.'
        else:
            fix = 'Use more teeth on one or both gears.'
        check.warnings.append(f'Contact ratio {cr:.2f} is below {CONTACT_RATIO_GOOD}. {fix}')
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
    tip_radius: float = 0.0        # as drawn; 0 = not stored (older versions)

    @property
    def editable(self) -> bool:
        return bool(self.gear_id)

    @property
    def drawn_tip(self) -> float:
        return self.tip_radius if self.tip_radius > 0 else gear_radii(self.params).tip


def to_attribute(record: GearRecord) -> str:
    params = record.params
    return json.dumps({
        'version': ATTR_VERSION,
        'id': record.gear_id,
        'type': params.gear_type,
        'module_mm': params.module,
        'pressure_angle_deg': math.degrees(params.pressure_angle),
        'tooth_height_factor': params.height_factor,
        'teeth': params.teeth,
        'theta0_rad': record.theta0,
        'backlash_mm': params.backlash,
        'rotation_offset_rad': record.rotation_offset,
        'resize': record.resize,
        'reference_circles': record.reference_circles,
        'mesh_with': record.mesh_with,
        'tip_radius_mm': record.tip_radius,
    })


def from_attribute(value: str) -> Optional[GearRecord]:
    """Parse a stored gear attribute (versions 1–3), or None if it isn't valid."""
    try:
        data = json.loads(value)
        params = GearParams(
            module=float(data['module_mm']),
            teeth=int(data['teeth']),
            pressure_angle=math.radians(float(data['pressure_angle_deg'])),
            backlash=float(data.get('backlash_mm', 0.0)),
            gear_type=INTERNAL if data['type'] == INTERNAL else EXTERNAL,
            height_factor=float(data.get('tooth_height_factor', 1.0)),
        )
        return GearRecord(
            params=params,
            theta0=float(data['theta0_rad']),
            gear_id=str(data.get('id', '')),
            rotation_offset=float(data.get('rotation_offset_rad', 0.0)),
            resize=bool(data.get('resize', True)),
            reference_circles=bool(data.get('reference_circles', False)),
            mesh_with=str(data.get('mesh_with', '')),
            tip_radius=float(data.get('tip_radius_mm', 0.0)),
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


@dataclass
class PlanRecord:
    """What the planetary helper stores on each circle it draws."""
    set_id: str
    role: str                      # 'sun', 'planet' or 'ring'
    index: int
    sun_teeth: int
    planet_teeth: int
    planets: int
    module: float
    pressure_angle: float          # radians
    height_factor: float

    @property
    def ring_teeth(self) -> int:
        return self.sun_teeth + 2 * self.planet_teeth

    @property
    def teeth(self) -> int:
        return {'sun': self.sun_teeth, 'planet': self.planet_teeth}.get(self.role, self.ring_teeth)

    @property
    def gear_type(self) -> str:
        return INTERNAL if self.role == 'ring' else EXTERNAL

    def params(self, backlash: float) -> GearParams:
        return GearParams(self.module, self.teeth, self.pressure_angle, backlash, self.gear_type, self.height_factor)

    def planet_params(self, backlash: float) -> GearParams:
        return GearParams(self.module, self.planet_teeth, self.pressure_angle, backlash, EXTERNAL,
                          self.height_factor)


def to_plan_attribute(plan: PlanRecord) -> str:
    return json.dumps({
        'version': PLAN_VERSION, 'set': plan.set_id, 'role': plan.role, 'index': plan.index,
        'teeth': plan.teeth, 'sun_teeth': plan.sun_teeth, 'planet_teeth': plan.planet_teeth,
        'ring_teeth': plan.ring_teeth, 'planets': plan.planets, 'module_mm': plan.module,
        'pressure_angle_deg': math.degrees(plan.pressure_angle), 'tooth_height_factor': plan.height_factor,
    })


def from_plan_attribute(value: str) -> Optional[PlanRecord]:
    try:
        d = json.loads(value)
        role = str(d['role'])
        if role not in ('sun', 'planet', 'ring'):
            return None
        return PlanRecord(str(d['set']), role, int(d.get('index', 0)), int(d['sun_teeth']), int(d['planet_teeth']),
                          int(d['planets']), float(d['module_mm']), math.radians(float(d['pressure_angle_deg'])),
                          float(d.get('tooth_height_factor', 1.0)))
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Planetary sets
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    label: str
    ok: bool
    detail: str


@dataclass
class PlanetaryResult:
    sun: GearParams
    planet: GearParams
    ring: GearParams
    planets: int
    rules: list[Rule]
    warnings: list[str]
    infos: list[str]
    orbit_radius: float
    ratio: float
    cr_sun_planet: float
    cr_planet_ring: float
    trim: Optional[Trim]

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.rules)

    @property
    def good(self) -> bool:
        """Passes, and both contact ratios reach 1.2."""
        return self.ok and min(self.cr_sun_planet, self.cr_planet_ring) >= CONTACT_RATIO_GOOD


def planetary_params(module: float, pressure_angle: float, k: float, backlash: float,
                     sun_teeth: int, planet_teeth: int) -> tuple[GearParams, GearParams, GearParams]:
    sun = GearParams(module, sun_teeth, pressure_angle, backlash, EXTERNAL, k)
    planet = GearParams(module, planet_teeth, pressure_angle, backlash, EXTERNAL, k)
    ring = GearParams(module, sun_teeth + 2 * planet_teeth, pressure_angle, backlash, INTERNAL, k)
    return sun, planet, ring


def spacing_ok(sun_teeth: int, planet_teeth: int, planets: int) -> bool:
    return (2 * sun_teeth + 2 * planet_teeth) % planets == 0


def planet_gap(module: float, pressure_angle: float, k: float, backlash: float,
               sun_teeth: int, planet_teeth: int, planets: int) -> float:
    """Gap between neighbouring planets' tip circles."""
    sun, planet, _ = planetary_params(module, pressure_angle, k, backlash, sun_teeth, planet_teeth)
    orbit = sun.pitch_radius + planet.pitch_radius
    return 2.0 * orbit * math.sin(math.pi / planets) - 2.0 * gear_radii(planet).tip


@functools.lru_cache(maxsize=512)
def check_planetary(module: float, pressure_angle: float, k: float, backlash: float,
                    sun_teeth: int, planet_teeth: int, planets: int) -> PlanetaryResult:
    sun, planet, ring = planetary_params(module, pressure_angle, k, backlash, sun_teeth, planet_teeth)
    z_ring = ring.teeth
    orbit = sun.pitch_radius + planet.pitch_radius
    rules, warnings, infos = [], [], []

    total = sun_teeth + z_ring
    rules.append(Rule('Planets fit evenly', total % planets == 0,
                      f'({sun_teeth} + {z_ring}) ÷ {planets} = {total / planets:g}'
                      + ('' if total % planets == 0 else ', not a whole number')))

    gap = planet_gap(module, pressure_angle, k, backlash, sun_teeth, planet_teeth, planets)
    need = PLANET_GAP * module
    rules.append(Rule('Planets clear each other', gap >= need,
                      f'gap between planet tips {gap:.2f} mm (need {need:.2f} mm)'))

    sp_collide = pair_collides(sun, planet)
    cr_sp = pair_contact_ratio(sun, planet)
    rules.append(Rule('Sun and planet mesh cleanly', not sp_collide and cr_sp >= CONTACT_RATIO_MIN,
                      ('teeth collide' if sp_collide else f'contact ratio {cr_sp:.2f}')))

    trim = ring_trim(ring, planet)
    ring_radii = gear_radii(ring, (planet,))
    cr_pr = contact_ratio(ring, ring_radii, planet, gear_radii(planet))
    if not trim.clears:
        detail = trim_failure_message(ring, trim)
    else:
        detail = f'contact ratio {cr_pr:.2f}'
        if ring_radii.trim is not None:
            infos.append(f'Ring tips will be trimmed {ring_radii.trimmed_by:.2f} mm to clear the planets.')
    rules.append(Rule('Planet and ring mesh cleanly', trim.clears and cr_pr >= CONTACT_RATIO_MIN, detail))

    for label, cr in (('Sun–planet', cr_sp), ('Planet–ring', cr_pr)):
        if CONTACT_RATIO_MIN <= cr < CONTACT_RATIO_GOOD:
            warnings.append(f'{label} contact ratio {cr:.2f}: works, but runs rougher than 1.2 or more.')
    return PlanetaryResult(sun, planet, ring, planets, rules, warnings, infos, orbit,
                           1.0 + z_ring / sun_teeth, cr_sp, cr_pr, trim if trim.required_tip else None)


def suggest_planetary(module: float, pressure_angle: float, k: float, backlash: float,
                      sun_teeth: int, planet_teeth: int, planets: int,
                      limit: int = 3, window: int = 10) -> list[tuple[int, int]]:
    """Nearby (sun, planet) tooth counts that pass every rule with both contact ratios ≥ 1.2."""
    target = 1.0 + (sun_teeth + 2 * planet_teeth) / sun_teeth
    candidates = []
    for zs in range(max(MIN_TEETH, sun_teeth - window), sun_teeth + window + 1):
        for zp in range(max(MIN_TEETH, planet_teeth - window), planet_teeth + window + 1):
            if (zs, zp) == (sun_teeth, planet_teeth) or zs + 2 * zp > MAX_TEETH:
                continue
            if not spacing_ok(zs, zp, planets):
                continue
            if planet_gap(module, pressure_angle, k, backlash, zs, zp, planets) < PLANET_GAP * module:
                continue
            ratio = 1.0 + (zs + 2 * zp) / zs
            candidates.append((abs(zs - sun_teeth) + abs(zp - planet_teeth), abs(ratio - target), zs, zp))
    found = []
    for _, _, zs, zp in sorted(candidates):
        # Cheap upper bounds first: trimming only lowers the ring's contact ratio, so an untrimmed
        # ring below 1.2 can't pass; the full check (collision sweeps and trim) runs on the rest.
        sun, planet, ring = planetary_params(module, pressure_angle, k, backlash, zs, zp)
        if pair_contact_ratio(sun, planet) < CONTACT_RATIO_GOOD:
            continue
        if contact_ratio(ring, gear_radii(ring), planet, gear_radii(planet)) < CONTACT_RATIO_GOOD:
            continue
        if check_planetary(module, pressure_angle, k, backlash, zs, zp, planets).good:
            found.append((zs, zp))
            if len(found) == limit:
                break
    return found


# ---------------------------------------------------------------------------
# Racks
# ---------------------------------------------------------------------------
#
# A rack is drawn along a pitch line from S (the line's start) in direction u. Rack coordinates
# (s, h): s along the line from S, h above the pitch line toward the tooth tips. The teeth are on
# the left of u for side = LEFT (+1), on the right for side = RIGHT (−1): n = side · (−u_y, u_x),
# and (s, h) maps to S + s·u + h·n. See docs/rack-spec.md.
#
# Tooth phase: tooth i is centred at s_i = p/2 + offset + i·p, so with offset 0 the line's start is
# at the center of a tooth space. A tooth is drawn only if its whole cell [s_i − p/2, s_i + p/2]
# (space center to space center) lies on [0, L]; elsewhere the edge runs along the root line.
#
# Backlash: as for gears, B is the total per mesh and each part takes half. A rack tooth is
# p/2 − B/2 wide at the pitch line, so its space is exactly the gear cutter's tooth (_cutter).

LEFT = 1
RIGHT = -1
RACK_ATTR_VERSION = 1


@dataclass(frozen=True)
class RackParams:
    module: float
    pressure_angle: float  # radians
    backlash: float = 0.0  # total per mesh, mm
    height_factor: float = 1.0

    @property
    def pitch(self) -> float:
        return math.pi * self.module

    @property
    def addendum(self) -> float:
        return self.height_factor * self.module

    @property
    def dedendum(self) -> float:
        return (self.height_factor + 0.25) * self.module

    def with_factor(self, k: float) -> 'RackParams':
        return replace(self, height_factor=k)


def rack_cutter(params: RackParams) -> Cutter:
    """The rack's tooth space: the same shape as the cutter that generates gear roots."""
    return _cutter(params.module, params.pressure_angle, params.dedendum, params.backlash)


def rack_teeth_for_length(length: float, params: RackParams) -> int:
    """Hybrid sizing for a rack: the whole number of pitches nearest the line length (at least 1)."""
    if params.pitch <= 0:
        return 1
    return max(1, int(math.floor(length / params.pitch + 0.5)))


def rack_tooth_centers(length: float, params: RackParams, offset: float = 0.0) -> list[float]:
    """s of every tooth whose whole cell fits on [0, length]."""
    p, tol = params.pitch, 1e-9
    if p <= 0 or length <= 0:
        return []
    first = math.ceil((-tol - offset) / p)
    last = math.floor((length + tol - offset) / p) - 1
    return [offset + p / 2.0 + i * p for i in range(first, last + 1)]


def rack_point(origin: Point, direction: float, side: int, s: float, h: float) -> Point:
    """Sketch point of rack coordinates (s, h)."""
    ux, uy = math.cos(direction), math.sin(direction)
    nx, ny = -side * uy, side * ux
    return (origin[0] + s * ux + h * nx, origin[1] + s * uy + h * ny)


def rack_coords(origin: Point, direction: float, side: int, pt: Point) -> tuple[float, float]:
    """Rack coordinates (s, h) of a sketch point (the inverse of rack_point)."""
    ux, uy = math.cos(direction), math.sin(direction)
    dx, dy = pt[0] - origin[0], pt[1] - origin[1]
    return dx * ux + dy * uy, side * (-dx * uy + dy * ux)


def rack_local_segments(params: RackParams, length: float, offset: float = 0.0,
                        body: float = 0.0) -> list[Segment]:
    """The outline in rack coordinates (s, h): the toothed edge from (0, −hf) to (L, −hf), then,
    if body > 0, down, back and up to close the loop. Every arc here is counter-clockwise."""
    c = rack_cutter(params)
    a, alpha, p = params.addendum, params.pressure_angle, params.pitch
    hf, rho, w, xc, dc = c.depth, c.corner_radius, c.half_width, c.corner_x, c.corner_depth
    foot_dx = rho * math.cos(alpha)
    foot_h = -dc - rho * math.sin(alpha)
    tip_dx = w + a * math.tan(alpha)
    corner_sweep = math.pi / 2.0 - alpha

    segments: list[Segment] = []
    cursor = 0.0   # s where the pending root run starts

    def root_to(s: float) -> None:
        if s - cursor > 1e-12:
            segments.append(Line((cursor, -hf), (s, -hf)))

    for center in rack_tooth_centers(length, params, offset):
        b = center - p / 2.0          # space center on the tooth's left
        e = center + p / 2.0          # space center on its right
        root_to(b + xc)
        tip_left = (b + tip_dx, a)
        tip_right = (e - tip_dx, a)
        if rho > 0:
            segments.append(Arc((b + xc, -dc), rho, -math.pi / 2.0, corner_sweep))
        segments.append(Line((b + xc + foot_dx, foot_h), tip_left))
        segments.append(Line(tip_left, tip_right))
        segments.append(Line(tip_right, (e - xc - foot_dx, foot_h)))
        if rho > 0:
            segments.append(Arc((e - xc, -dc), rho, -math.pi + alpha, corner_sweep))
        cursor = e - xc
    root_to(length)
    if body > 0:
        bottom = -hf - body
        segments += [Line((length, -hf), (length, bottom)), Line((length, bottom), (0.0, bottom)),
                     Line((0.0, bottom), (0.0, -hf))]
    return segments


def place_segment(seg: Segment, origin: Point, direction: float, side: int) -> Segment:
    """A segment in rack coordinates, moved onto the sketch. side = RIGHT mirrors, which reverses arcs."""
    def place(pt: Point) -> Point:
        return rack_point(origin, direction, side, pt[0], pt[1])
    if isinstance(seg, Line):
        return Line(place(seg.start), place(seg.end))
    if isinstance(seg, Arc):
        if side == LEFT:
            return Arc(place(seg.center), seg.radius, seg.start_angle + direction, seg.sweep, seg.clockwise)
        return Arc(place(seg.center), seg.radius, direction - seg.start_angle - seg.sweep, seg.sweep,
                   not seg.clockwise)
    return Spline([place(pt) for pt in seg.points])


@dataclass
class RackProfile:
    """A rack outline as drawn: segments in sketch mm, plus where and how it was placed."""
    params: RackParams
    origin: Point
    direction: float
    side: int
    length: float
    offset: float
    body: float
    tooth_centers: list[float]
    segments: list[Segment]
    warnings: list[str] = field(default_factory=list)

    @property
    def closed(self) -> bool:
        return self.body > 0

    def point(self, s: float, h: float) -> Point:
        return rack_point(self.origin, self.direction, self.side, s, h)


def build_rack(params: RackParams, origin: Point, direction: float, side: int, length: float,
               offset: float = 0.0, body: float = 0.0) -> RackProfile:
    local = rack_local_segments(params, length, offset, body)
    placed = [place_segment(seg, origin, direction, side) for seg in local]
    return RackProfile(params, origin, direction, side, length, offset, body,
                       rack_tooth_centers(length, params, offset), placed)


def rack_top_land(params: RackParams) -> float:
    a = params.addendum
    return params.pitch / 2.0 - params.backlash / 2.0 - 2.0 * a * math.tan(params.pressure_angle)


def rack_factor_max(params: RackParams) -> float:
    """Largest tooth height factor for a rack: top land ≥ 0.2·m, and the space (the cutter tooth)
    must not come to a point above its root."""
    m, tan_a = params.module, math.tan(params.pressure_angle)
    if m <= 0:
        return FACTOR_CAP
    by_land = (params.pitch / 2.0 - params.backlash / 2.0 - MIN_TOP_LAND * m) / (2.0 * m * tan_a)
    w = params.pitch / 4.0 + params.backlash / 4.0
    by_space = (w / tan_a) / m - 0.25 - 1e-6
    return math.floor(min(by_land, by_space, FACTOR_CAP) * 1e4) / 1e4


def check_rack(params: RackParams, length: float, resize: bool, offset: float = 0.0,
               body: float = 0.0) -> Check:
    """Blocking errors and warnings for a rack on a line of `length` mm (before any resize)."""
    check = Check()
    p = params.pitch
    if params.module <= 0:
        check.errors.append('Module must be greater than 0.')
    if params.backlash < 0:
        check.errors.append('Backlash cannot be negative.')
    if body < 0:
        check.errors.append('Backing thickness cannot be negative.')
    if length <= 0:
        check.errors.append('The line has no length.')
    if check.errors:
        return check
    if params.backlash >= p / 2.0:
        check.errors.append('Backlash is larger than the tooth itself.')
        return check
    k_max = rack_factor_max(params)
    if params.height_factor > k_max + 1e-9:
        check.errors.append(f'Tooth height {params.height_factor:g} is above the maximum {k_max:.2f} for a rack '
                            '(the tips would get too narrow).')
        return check
    if params.height_factor < FACTOR_FLOOR - 1e-9:
        check.errors.append(f'Tooth height {params.height_factor:g} is below the minimum {FACTOR_FLOOR:g}.')
        return check
    drawn_length = rack_teeth_for_length(length, params) * p if resize else length
    teeth = len(rack_tooth_centers(drawn_length, params, offset))
    if teeth > MAX_TEETH:
        check.errors.append(f"That's {teeth} teeth; a rack can have at most {MAX_TEETH}. Use a shorter line "
                            f'(up to {MAX_TEETH * p:.0f} mm) or a larger module.')
        return check
    if teeth == 0:
        if drawn_length < p:
            check.errors.append(f'The line is shorter than one tooth (pitch {p:.2f} mm).')
        else:
            check.errors.append('No whole tooth fits on the line at this offset.')
        return check
    if not resize:
        leftover = length - teeth * p
        if leftover > TANGENCY_TOL_MM:
            check.warnings.append(f'Resize is off: {leftover:.2f} mm of the line has no teeth.')
    return check


def profile_area(segments: list[Segment]) -> float:
    """Signed area enclosed by a closed loop of segments (counter-clockwise positive), by Green's
    theorem: exact for lines and arcs, splines as polylines through their points."""
    total = 0.0
    for seg in segments:
        if isinstance(seg, Arc):
            (cx, cy), r = seg.center, seg.radius
            f1, f2 = seg.start_angle, seg.start_angle + seg.sweep
            term = (cx * r * (math.sin(f2) - math.sin(f1)) - cy * r * (math.cos(f2) - math.cos(f1))
                    + r * r * (f2 - f1))
            total += -term if seg.clockwise else term
        else:
            pts = seg.points if isinstance(seg, Spline) else [seg.start, seg.end]
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                total += x1 * y2 - x2 * y1
    return total / 2.0


@dataclass
class RackRecord:
    """Everything stored on a rack's pitch line."""
    params: RackParams
    rack_id: str
    side: int = LEFT
    offset: float = 0.0
    resize: bool = True
    body: float = 0.0
    teeth: int = 0                 # as drawn
    origin: Point = (0.0, 0.0)     # where the rack was drawn: the line's start, direction and length then
    direction: float = 0.0
    length: float = 0.0
    mesh_with: str = ''            # gear_id of the gear it was aligned to, if any
    phase: Optional[float] = None  # total offset of the tooth pattern: alignment + offset (None = offset)

    @property
    def tooth_phase(self) -> float:
        """The offset the teeth were drawn with: tooth i at s = p/2 + tooth_phase + i·p."""
        return self.offset if self.phase is None else self.phase


def to_rack_attribute(record: RackRecord) -> str:
    params = record.params
    return json.dumps({
        'version': RACK_ATTR_VERSION,
        'id': record.rack_id,
        'module_mm': params.module,
        'pressure_angle_deg': math.degrees(params.pressure_angle),
        'tooth_height_factor': params.height_factor,
        'backlash_mm': params.backlash,
        'side': record.side,
        'offset_mm': record.offset,
        'resize': record.resize,
        'body_mm': record.body,
        'teeth': record.teeth,
        'origin_mm': list(record.origin),
        'direction_rad': record.direction,
        'length_mm': record.length,
        'mesh_with': record.mesh_with,
        'phase_mm': record.tooth_phase,
    })


def from_rack_attribute(value: str) -> Optional[RackRecord]:
    """Parse a stored rack attribute, or None if it isn't one."""
    try:
        data = json.loads(value)
        side = int(data['side'])
        if side not in (LEFT, RIGHT) or not data['id']:
            return None
        origin = data['origin_mm']
        params = RackParams(
            module=float(data['module_mm']),
            pressure_angle=math.radians(float(data['pressure_angle_deg'])),
            backlash=float(data.get('backlash_mm', 0.0)),
            height_factor=float(data.get('tooth_height_factor', 1.0)),
        )
        return RackRecord(
            params=params,
            rack_id=str(data['id']),
            side=side,
            offset=float(data.get('offset_mm', 0.0)),
            resize=bool(data.get('resize', True)),
            body=float(data.get('body_mm', 0.0)),
            teeth=int(data.get('teeth', 0)),
            origin=(float(origin[0]), float(origin[1])),
            direction=float(data['direction_rad']),
            length=float(data['length_mm']),
            mesh_with=str(data.get('mesh_with', '')),
            phase=float(data['phase_mm']) if 'phase_mm' in data else None,
        )
    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        return None


# ---------------------------------------------------------------------------
# Rack and pinion
# ---------------------------------------------------------------------------
#
# A gear meshes with a rack when its pitch circle touches the rack's pitch line on the teeth side
# (center at h = r) and a tooth on one faces a gap on the other at the contact point C, the foot
# of the perpendicular from the gear's center. With d = the direction from the gear's center to C
# (= −n), gear phase p_g = frac((d − θ₀)/τ) and rack phase q = frac((s_C − p/2 − phase)/p), both
# 0 when a tooth centerline is at C, the mesh condition is
#
#     p_g ≡ ½ + side · q   (mod 1)
#
# The gear's counter-clockwise tangent at C is side·u, and r·τ = p, so a gear tooth at arc length e
# from C (along that tangent) lines up with the rack feature at side·e along the line.

RACK_TOUCH_TOL = 1e-3       # × module: overlap this small counts as touching (B = 0 flank contact, rounding)


@dataclass(frozen=True)
class RackPose:
    """Where a rack is, for meshing: its (live) pitch line, and the phase its teeth were drawn with."""
    params: RackParams
    origin: Point
    direction: float
    side: int
    phase: float          # tooth i at s = p/2 + phase + i·p
    length: float

    @property
    def toward_line(self) -> float:
        """Direction from a gear on the teeth side to its contact point: −n."""
        return math.atan2(-self.side * math.cos(self.direction), self.side * math.sin(self.direction))


def rack_contact(pose: RackPose, center: Point) -> tuple[float, float]:
    """(s, h) of a gear's center in the rack frame: s is where it touches the pitch line, h its height
    above the line toward the teeth (the pitch radius, when tangent)."""
    return rack_coords(pose.origin, pose.direction, pose.side, center)


def rack_phase_at(pose: RackPose, s: float) -> float:
    """Rack phase in teeth at s along the line: 0 means a tooth centerline is there."""
    p = pose.params.pitch
    return _frac((s - p / 2.0 - pose.phase) / p)


def align_theta0_to_rack(gear: GearParams, center: Point, pose: RackPose) -> float:
    """θ₀ for a gear so it meshes with the rack at its contact point."""
    s, _ = rack_contact(pose, center)
    q = rack_phase_at(pose, s)
    return theta0_for_phase(pose.toward_line, _frac(0.5 + pose.side * q), gear.teeth)


def align_rack_phase(pose: RackPose, gear: GearParams, center: Point, theta0: float) -> float:
    """The rack phase (in [0, p)) that meshes with this gear; pose.phase is ignored."""
    p = pose.params.pitch
    s, _ = rack_contact(pose, center)
    q = _frac(pose.side * (phase(pose.toward_line, theta0, gear.teeth) - 0.5))
    return (s - p / 2.0 - q * p) % p


def rack_mesh_error(gear: GearParams, center: Point, theta0: float, pose: RackPose) -> float:
    """How far a gear and a rack are from the mesh condition, in teeth (0 = aligned, 0.5 = worst)."""
    s, _ = rack_contact(pose, center)
    off = _frac(phase(pose.toward_line, theta0, gear.teeth) - 0.5 - pose.side * rack_phase_at(pose, s))
    return min(off, 1.0 - off)


def check_rack_mesh(gear: GearParams, center: Point, pose: RackPose) -> Check:
    """Validate a gear and a rack as a meshing pair: compatibility and placement."""
    check = Check()
    check.errors += _match_errors(gear, pose.params)
    if gear.internal:
        check.errors.append('A rack can only mesh with an external gear.')
    if check.errors:
        return check
    s, h = rack_contact(pose, center)
    if h < TANGENCY_TOL_MM:
        check.errors.append("The gear is on the wrong side of the rack's line, away from its teeth. "
                            'Flip the rack, or move the gear.')
        return check
    if abs(h - gear.pitch_radius) > TANGENCY_TOL_MM:
        check.warnings.append("The circle isn't tangent to the rack's line: they won't mesh at this spacing.")
    if s < -TANGENCY_TOL_MM or s > pose.length + TANGENCY_TOL_MM:
        check.warnings.append("The gear touches the rack's line beyond the end of the line.")
    return check


def rack_contact_ratio(gear: GearParams, radii: Radii, rack: RackParams) -> float:
    """Contact ratio of a gear (as drawn) and a rack, along the line of action through the pitch point."""
    sa = math.sin(gear.pressure_angle)
    rb, pitch_point = gear.base_radius, gear.pitch_radius * sa

    def s(radius: float) -> float:
        return math.sqrt(max(radius * radius - rb * rb, 0.0))

    # Gear tip working down the rack's straight flank; rack tip working down the gear's involute.
    gear_side = min(s(radii.tip) - pitch_point, rack_cutter(rack).straight_depth / sa)
    rack_side = min(rack.addendum / sa, pitch_point - s(radii.form))
    return max(0.0, gear_side + rack_side) / (rack.pitch * math.cos(gear.pressure_angle))


def _rack_space_half(cut: Cutter, alpha: float, h: float) -> float:
    """Half width of a rack's tooth space at height h (−hf ≤ h): straight flank, or the root fillet."""
    if h >= -cut.straight_depth:
        return cut.half_width + h * math.tan(alpha)
    dy = h + cut.corner_depth
    return cut.corner_x + math.sqrt(max(cut.corner_radius ** 2 - dy * dy, 0.0))


def _inside_rack(rack: RackParams, cut: Cutter, phase_mm: float, s: float, h: float, tol: float) -> bool:
    """Is (s, h) more than tol inside an endless rack's material (backing included)?"""
    if h >= rack.addendum - tol:
        return False
    if h <= -cut.depth - tol:
        return True
    p = rack.pitch
    rel = s - (phase_mm + round((s - phase_mm) / p) * p)     # from the nearest space center
    return abs(rel) > _rack_space_half(cut, rack.pressure_angle, max(h, -cut.depth)) + tol


def _inside_gear_by(gear: GearParams, radii: Radii, theta0: float, rho: float, angle: float, tol: float) -> bool:
    """Is a point (polar, gear frame) more than tol (mm, along the circle) inside an external gear?"""
    if rho <= radii.root - tol:
        return True
    if rho >= radii.tip - tol:
        return False
    rel = angle - _nearest_center(angle, theta0, gear.angular_pitch)
    return rho * (tooth_half_angle(gear, max(rho, radii.root)) - abs(rel)) > tol


def _rack_boundary(rack: RackParams) -> list[Point]:
    """Points on one tooth's outline, (x from the space center on its left, h), from root to root."""
    cut = rack_cutter(rack)
    p, a, alpha = rack.pitch, rack.addendum, rack.pressure_angle
    pts = []
    for i in range(9):
        ang = -math.pi / 2.0 + (math.pi / 2.0 - alpha) * i / 8      # fillet
        pts.append((cut.corner_x + cut.corner_radius * math.cos(ang), -cut.corner_depth + cut.corner_radius * math.sin(ang)))
    for i in range(1, 13):
        h = -cut.straight_depth + (a + cut.straight_depth) * i / 12  # flank
        pts.append((cut.half_width + h * math.tan(alpha), h))
    left, right = cut.half_width + a * math.tan(alpha), p - cut.half_width - a * math.tan(alpha)
    pts += [(left + (right - left) * i / 6, a) for i in range(1, 6)]  # top land
    return pts + [(p - x, h) for x, h in pts]


def rack_pair_collides_at(gear: GearParams, radii: Radii, center: Point, theta0: float, pose: RackPose,
                          phases: int = 48) -> bool:
    """Do a gear and an endless rack, placed as given, collide while the gear rolls one tooth along it?"""
    rack = pose.params
    cut = rack_cutter(rack)
    p, tau, r = rack.pitch, gear.angular_pitch, gear.pitch_radius
    gear_pts = [pt for pt, _ in _external_boundary(gear, radii)]
    rack_pts = _rack_boundary(rack)
    tol = RACK_TOUCH_TOL * rack.module
    d = pose.toward_line
    s_c, _ = rack_contact(pose, center)
    for i in range(phases):
        phi = i / phases * tau
        shift = pose.side * r * phi          # the rack moves along the gear's tangent at C
        t0 = theta0 + phi
        for j in range(gear.teeth):
            rot = t0 + j * tau
            if abs(_wrap(rot - d)) > math.radians(100):
                continue
            c, sn = math.cos(rot), math.sin(rot)
            for x, y in gear_pts:
                world = (center[0] + x * c - y * sn, center[1] + x * sn + y * c)
                s, h = rack_coords(pose.origin, pose.direction, pose.side, world)
                if _inside_rack(rack, cut, pose.phase + shift, s, h, tol):
                    return True
        first = pose.phase + shift + math.floor((s_c - pose.phase - shift) / p) * p   # space center at or before C
        for k in range(-3, 3):
            base = first + k * p
            for x, h in rack_pts:
                wx, wy = rack_point(pose.origin, pose.direction, pose.side, base + x, h)
                rho = math.hypot(wx - center[0], wy - center[1])
                if _inside_gear_by(gear, radii, t0, rho, math.atan2(wy - center[1], wx - center[0]), tol):
                    return True
    return False


def _canonical_rack_pair(gear: GearParams, rack: RackParams) -> tuple:
    """A gear above an endless rack (teeth up, line along +x), aligned: (center, θ₀, pose)."""
    pose = RackPose(rack, (-gear.pitch_radius - 10.0 * rack.pitch, 0.0), 0.0, LEFT, 0.0, 1e9)
    center = (0.0, gear.pitch_radius)
    return center, align_theta0_to_rack(gear, center, pose), pose


@functools.lru_cache(maxsize=256)
def rack_pair_collides(gear: GearParams, rack: RackParams, phases: int = 48) -> bool:
    """Do a gear (as drawn) and a rack, meshed, collide over a full tooth?"""
    center, theta0, pose = _canonical_rack_pair(gear, rack)
    return rack_pair_collides_at(gear, gear_radii(gear), center, theta0, pose, phases)


@functools.lru_cache(maxsize=256)
def rack_factor_min_pair(gear: GearParams, rack: RackParams) -> Optional[float]:
    """Smallest shared factor at which a gear and a rack reach a contact ratio of 1.2, or None."""
    k_hi = min(factor_max(gear.with_factor(1.0)), rack_factor_max(rack.with_factor(1.0)))

    def ok(k: float) -> bool:
        g = gear.with_factor(k)
        return rack_contact_ratio(g, gear_radii(g), rack.with_factor(k)) >= CONTACT_RATIO_GOOD

    if not ok(k_hi):
        return None
    k = k_hi   # scan down (as _factor_min_pair does): don't assume the ratio rises steadily with k
    while k - 0.1 >= FACTOR_SCAN_MIN:
        if not ok(k - 0.1):
            lo, hi = k - 0.1, k
            for _ in range(7):
                mid = (lo + hi) / 2.0
                if ok(mid):
                    hi = mid
                else:
                    lo = mid
            return round(hi, 3)
        k -= 0.1
    return FACTOR_SCAN_MIN


def rack_pair_report(gear: GearParams, radii: Radii, rack: RackParams) -> Check:
    """Contact ratio and interference messages for a gear and the rack it meshes with."""
    check = Check()
    if rack_pair_collides(gear, rack):
        check.warnings.append(f"The rack's teeth hit the {gear.teeth}-tooth gear. Use more teeth on the gear, "
                              'or more backlash.')
    cr = rack_contact_ratio(gear, radii, rack)
    check.infos.append(f'Contact ratio {cr:.2f}')
    if cr < CONTACT_RATIO_GOOD:
        k_min = rack_factor_min_pair(gear, rack)
        if k_min is not None and k_min > gear.height_factor + 1e-9:
            fix = f'Remake the rack and the gear with a tooth height factor of at least {k_min:.2f}.'
        else:
            fix = 'Use more teeth on the gear.'
        check.warnings.append(f'Contact ratio {cr:.2f} is below {CONTACT_RATIO_GOOD}. {fix}')
    return check


# ---------------------------------------------------------------------------
# SVG debug output
# ---------------------------------------------------------------------------

def _svg_path(segments: list[Segment], close: bool = True) -> str:
    def pt(p: Point) -> str:
        return f'{p[0]:.5f},{-p[1]:.5f}'  # flip y for SVG

    parts = [f'M {pt(segments[0].start)}']
    for seg in segments:
        if isinstance(seg, Arc):
            large = 1 if seg.sweep > math.pi else 0
            # y is flipped, so CCW in math coordinates is sweep-flag 0 in SVG.
            sweep_flag = 1 if seg.clockwise else 0
            parts.append(f'A {seg.radius:.5f},{seg.radius:.5f} 0 {large} {sweep_flag} {pt(seg.end)}')
        elif isinstance(seg, Spline):
            parts.extend(f'L {pt(p)}' for p in seg.points[1:])
        else:
            parts.append(f'L {pt(seg.end)}')
    if close:
        parts.append('Z')
    return ' '.join(parts)


def _svg_extent(p: Union[Profile, RackProfile]) -> tuple[list[float], list[float]]:
    """x and y (SVG, y flipped) extremes a profile needs on the canvas."""
    if isinstance(p, RackProfile):
        pts = [seg_pt for seg in p.segments for seg_pt in (seg.start, seg.end)]
        pts += [p.origin, p.point(p.length, 0.0)]
        pad = p.params.module
        return ([min(x for x, _ in pts) - pad, max(x for x, _ in pts) + pad],
                [min(-y for _, y in pts) - pad, max(-y for _, y in pts) + pad])
    reach = max(p.tip_radius, p.root_radius) + p.params.module
    return [p.center[0] - reach, p.center[0] + reach], [-p.center[1] - reach, -p.center[1] + reach]


def to_svg(profiles: Union[Profile, RackProfile, list], path: str) -> None:
    """Write one profile, or a meshing set, to an SVG for visual checks.

    Gears: pitch circles dashed, base circles dotted, tooth 0 centerlines in red.
    Racks: pitch line dashed, an arrow along the line from its start, and a red tick toward the teeth.
    """
    if isinstance(profiles, (Profile, RackProfile)):
        profiles = [profiles]
    xs, ys = [], []
    for p in profiles:
        px, py = _svg_extent(p)
        xs += px
        ys += py
    x0, y0 = min(xs), min(ys)
    w, h = max(xs) - x0, max(ys) - y0
    stroke = max(w, h) / 1500.0
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:.4f} {y0:.4f} {w:.4f} {h:.4f}" '
           f'width="1000" height="{1000 * h / w:.0f}">',
           f'<rect x="{x0}" y="{y0}" width="{w}" height="{h}" fill="white"/>']
    colors = ['#3b6fb6', '#c9762b', '#3a9a5b', '#8a4fb0']
    for i, p in enumerate(profiles):
        color = colors[i % len(colors)]
        if isinstance(p, RackProfile):
            fill = color if p.closed else 'none'
            out.append(f'<path d="{_svg_path(p.segments, p.closed)}" fill="{fill}" fill-opacity="0.25" '
                       f'stroke="{color}" stroke-width="{stroke}"/>')
            (sx, sy), (ex, ey) = p.origin, p.point(p.length, 0.0)
            out.append(f'<line x1="{sx}" y1="{-sy}" x2="{ex}" y2="{-ey}" stroke="#666" stroke-width="{stroke / 2}" '
                       f'stroke-dasharray="{stroke * 6},{stroke * 4}"/>')
            m = p.params.module
            (ax, ay), (tx, ty) = p.point(2.0 * m, 0.0), p.point(0.0, 1.5 * m)
            out.append(f'<line x1="{sx}" y1="{-sy}" x2="{ax}" y2="{-ay}" stroke="black" stroke-width="{stroke}"/>')
            out.append(f'<circle cx="{ax}" cy="{-ay}" r="{stroke * 3}" fill="black"/>')
            out.append(f'<line x1="{sx}" y1="{-sy}" x2="{tx}" y2="{-ty}" stroke="red" stroke-width="{stroke}"/>')
            continue
        cx, cy = p.center[0], -p.center[1]
        fill = 'none' if p.params.internal else color
        out.append(f'<path d="{_svg_path(p.segments)}" fill="{fill}" fill-opacity="0.25" stroke="{color}" '
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
