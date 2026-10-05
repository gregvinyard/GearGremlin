import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gearmath as gm  # noqa: E402

DEG20 = math.radians(20)
B = 0.05  # default backlash


def ext(z: int, m: float = 2.0, alpha: float = DEG20, backlash: float = 0.0, k: float = 1.0) -> gm.GearParams:
    return gm.GearParams(m, z, alpha, backlash, gm.EXTERNAL, k)


def ring(z: int, m: float = 2.0, alpha: float = DEG20, backlash: float = 0.0, k: float = 1.0) -> gm.GearParams:
    return gm.GearParams(m, z, alpha, backlash, gm.INTERNAL, k)


def polar_of(center, p):
    return math.hypot(p[0] - center[0], p[1] - center[1]), math.atan2(p[1] - center[1], p[0] - center[0])


def wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


# --- basic involute math ---------------------------------------------------

def test_inverse_involute_roundtrip():
    for deg in (1, 14.5, 20, 25, 40, 60):
        phi = math.radians(deg)
        assert gm.inverse_involute(gm.involute(phi)) == pytest.approx(phi, abs=1e-12)


@pytest.mark.parametrize('params', [ext(22), ext(8, backlash=0.2), ring(60), ring(40, backlash=0.15),
                                    ext(30, alpha=math.radians(14.5)), ext(12, alpha=math.radians(25)),
                                    ext(20, k=0.8), ring(50, k=1.2)])
def test_half_thickness_at_pitch_circle_equals_psi(params):
    assert gm.flank_angle(params, params.pitch_radius) == pytest.approx(params.half_angle, abs=1e-12)


def test_backlash_thins_teeth_and_widens_spaces_by_half():
    b = 0.2
    e0, e1 = ext(20), ext(20, backlash=b)
    assert 2 * e0.pitch_radius * (e0.half_angle - e1.half_angle) == pytest.approx(b / 2)
    r0, r1 = ring(60), ring(60, backlash=b)
    assert 2 * r0.pitch_radius * (r1.half_angle - r0.half_angle) == pytest.approx(b / 2)


# --- hybrid sizing ----------------------------------------------------------

@pytest.mark.parametrize('d, m, z', [(43.0, 2.0, 22), (44.0, 2.0, 22), (40.9, 1.0, 41), (12.3, 0.5, 25),
                                     (100.0, 3.0, 33), (101.0, 3.0, 34)])
def test_suggest_teeth_is_round_d_over_m(d, m, z):
    assert gm.suggest_teeth(d, m) == round(d / m) == z


# --- tooth height factor ----------------------------------------------------

def test_factor_sets_addendum_and_dedendum():
    p = ext(20, k=0.8)
    assert p.tip_radius == pytest.approx(20 + 1.6)
    assert p.root_radius == pytest.approx(20 - 2.1)
    r = ring(60, k=0.8)
    assert r.tip_radius == pytest.approx(60 - 1.6)
    assert r.root_radius == pytest.approx(60 + 2.1)


@pytest.mark.parametrize('params', [ext(10), ext(20), ext(40), ext(15, backlash=B), ext(12, alpha=math.radians(25))])
def test_factor_max_is_where_the_tip_gets_too_narrow(params):
    k_max = gm.factor_max(params)
    assert gm.FACTOR_SCAN_MIN < k_max < gm.FACTOR_CAP
    at_max = params.with_factor(k_max)
    assert gm.factor_geometry_ok(at_max)
    assert not gm.factor_geometry_ok(params.with_factor(k_max + 0.01))
    # At the maximum, the binding limit is the 0.2·m top land.
    assert gm.top_land(at_max) == pytest.approx(gm.MIN_TOP_LAND * params.module, abs=0.01 * params.module)


@pytest.mark.parametrize('a, b', [(ext(20, backlash=B), ext(30, backlash=B)),
                                  (ext(14, backlash=B), ext(40, backlash=B)),
                                  (ring(60, backlash=B), ext(15, backlash=B)),
                                  (ring(80, backlash=B), ext(20, backlash=B))])
def test_factor_min_is_where_contact_ratio_reaches_1_2(a, b):
    k_min = gm.factor_min_pair(a, b)
    assert k_min is not None
    cr_at = gm.pair_contact_ratio(a.with_factor(k_min), b.with_factor(k_min))
    cr_below = gm.pair_contact_ratio(a.with_factor(k_min - 0.03), b.with_factor(k_min - 0.03))
    assert cr_at == pytest.approx(gm.CONTACT_RATIO_GOOD, abs=0.02)
    assert cr_below < gm.CONTACT_RATIO_GOOD


def test_factor_range_without_partner_uses_fixed_floor():
    lo, hi = gm.factor_range(ext(20))
    assert lo == gm.FACTOR_FLOOR
    assert hi == gm.factor_max(ext(20))


def test_factor_limits_enforced_in_check_params():
    assert not gm.check_params(ext(20, k=1.0)).errors
    assert gm.check_params(ext(20, k=gm.factor_max(ext(20)) + 0.05)).errors
    assert gm.check_params(ext(20, k=0.5)).errors


def test_factor_mismatch_blocks_mesh():
    check = gm.check_mesh(ext(20, k=0.8), (0, 0), ext(20, k=1.0), (40, 0), 20)
    assert any('Tooth height' in e for e in check.errors)
    assert not gm.check_mesh(ext(20, k=0.8), (0, 0), ext(20, k=0.8), (40, 0), 20).errors


def test_pair_report_low_contact_ratio_names_the_fix():
    a, b = ext(20, backlash=B, k=0.65), ext(30, backlash=B, k=0.65)
    report = gm.pair_report(a, gm.gear_radii(a), b)
    assert any('below 1.2' in w and 'at least' in w for w in report.warnings)
    assert report.infos and report.infos[0].startswith('Contact ratio')


def test_contact_ratio_matches_textbook_for_plain_pair():
    # 20 + 30 teeth, m = 2, 20°, k = 1: textbook ε ≈ 1.606 (contact stays above both form radii).
    assert gm.pair_contact_ratio(ext(20), ext(30)) == pytest.approx(1.606, abs=0.005)


# --- generated root ----------------------------------------------------------

def test_undercut_threshold_is_17_at_20_degrees():
    assert gm.undercut_min_teeth(ext(20)) == 17
    assert gm.undercut_threshold(ext(20, k=0.8)) < gm.undercut_threshold(ext(20))  # stub teeth undercut less
    assert gm.check_params(ext(17)).warnings
    assert not any('undercut' in w for w in gm.check_params(ext(18)).warnings)
    assert any('weaker' in w for w in gm.check_params(ext(10)).warnings)


@pytest.mark.parametrize('z', [6, 8, 10, 14, 17, 25, 42, 60, 120])
def test_generated_root_endpoints_and_join(z):
    params = ext(z, backlash=B)
    rs = gm.root_shape(params)
    radii = gm.gear_radii(params)
    assert rs.rhos[0] == pytest.approx(params.root_radius)
    assert rs.rhos[-1] == pytest.approx(rs.form)
    assert rs.halves[-1] == pytest.approx(gm.flank_angle(params, rs.form), abs=1e-12)
    assert params.root_radius < rs.form < radii.tip
    # Half-angle at the root never exceeds half the angular pitch (teeth don't overlap at the root).
    assert rs.halves[0] <= params.angular_pitch / 2 + 1e-12


def _cutter_outline(params):
    cut = gm.cutter(params)
    a, r, m = params.pressure_angle, params.pitch_radius, params.module
    pts = []
    for i in range(80):  # straight flank, from above the pitch line down to where it meets the corner
        d = -1.5 * m + (cut.straight_depth + 1.5 * m) * i / 79
        pts.append((cut.half_width - d * math.tan(a), r - d))
    for i in range(40):  # rounded corner
        th = -a - (math.pi / 2 - a) * i / 39
        pts.append((cut.corner_x + cut.corner_radius * math.cos(th),
                    r - cut.corner_depth + cut.corner_radius * math.sin(th)))
    pts.append((0.0, r - cut.depth))
    return pts


@pytest.mark.parametrize('z', [8, 10, 14, 17, 30, 60])
def test_cutter_never_cuts_into_the_drawn_tooth(z):
    """Brute force: sweep the actual cutter outline; no cutter point may lie inside the drawn tooth."""
    params = ext(z, backlash=B)
    r, tau = params.pitch_radius, params.angular_pitch
    tip = gm.gear_radii(params).tip
    worst = 0.0
    for j in range(1500):
        t = -0.9 * tau + 2.4 * tau * j / 1499
        c, s = math.cos(-t), math.sin(-t)
        for (x, y) in _cutter_outline(params):
            wx = x - r * t
            gx, gy = wx * c - y * s, wx * s + y * c
            rho = math.hypot(gx, gy)
            if rho >= tip or rho <= params.root_radius:
                continue
            half = math.atan2(gy, gx) - math.pi / 2 + tau / 2      # angle from the tooth on this side
            depth = gm.tooth_half_angle(params, rho) - half          # > 0: cutter point inside the tooth
            worst = max(worst, depth * rho)
    assert worst < 0.01 * params.module


# --- profile geometry -------------------------------------------------------

ALL_PROFILES = [ext(22), ext(8), ext(50), ext(17, backlash=0.15), ring(60), ring(24), ring(45, backlash=0.1),
                ext(13, alpha=math.radians(14.5)), ring(30, alpha=math.radians(25)), ext(20, k=0.8), ring(60, k=0.8)]


@pytest.mark.parametrize('params', ALL_PROFILES)
def test_profile_is_closed_loop(params):
    prof = gm.build_profile(params, center=(3.0, -7.0), theta0=0.3)
    segs = prof.segments
    for a, b in zip(segs, segs[1:] + segs[:1]):
        assert a.end == pytest.approx(b.start, abs=1e-9)


@pytest.mark.parametrize('params', ALL_PROFILES)
def test_segment_endpoints_land_on_stated_radii(params):
    center = (3.0, -7.0)
    prof = gm.build_profile(params, center=center, theta0=0.3)
    radii = prof.radii
    allowed = sorted({radii.tip, radii.root, radii.form})
    for seg in prof.segments:
        for p in (seg.start, seg.end):
            rho = polar_of(center, p)[0]
            assert min(abs(rho - a) for a in allowed) < 1e-9
        if isinstance(seg, gm.Arc):
            assert seg.radius in (pytest.approx(radii.tip), pytest.approx(radii.root))
            assert seg.sweep > 0


def test_external_radii():
    prof = gm.build_profile(ext(20))
    assert prof.tip_radius == pytest.approx(22.0)
    assert prof.root_radius == pytest.approx(17.5)
    assert not any(isinstance(s, gm.Line) for s in prof.segments)  # the radial line is gone
    assert len(gm.build_profile(ext(60)).segments) == 60 * 6


def test_internal_radii():
    prof = gm.build_profile(ring(60))
    assert prof.tip_radius == pytest.approx(58.0)
    assert prof.root_radius == pytest.approx(62.5)
    assert not prof.warnings


def test_internal_tip_clamped_to_base_circle():
    params = ring(24)
    prof = gm.build_profile(params)
    assert prof.tip_radius == pytest.approx(params.base_radius)
    assert prof.warnings


def test_pointed_teeth_clamped():
    params = ext(6, alpha=math.radians(25), backlash=1.2)
    assert gm.flank_angle(params, params.tip_radius) < 0
    prof = gm.build_profile(params)
    assert prof.tip_radius < params.tip_radius
    assert gm.flank_angle(params, prof.tip_radius) == pytest.approx(0, abs=1e-12)
    assert prof.warnings


@pytest.mark.parametrize('params', ALL_PROFILES)
def test_profile_symmetric_about_tooth_centerline(params):
    theta0 = 0.3
    prof = gm.build_profile(params, theta0=theta0, points_per_flank=12)
    points = [p for s in prof.segments for p in ([s.start, s.mid, s.end] if isinstance(s, gm.Arc) else
                                                  s.points if isinstance(s, gm.Spline) else [s.start, s.end])]
    polar = [(math.hypot(*p), math.atan2(p[1], p[0])) for p in points]
    for rho, ang in polar:
        mirrored = 2 * theta0 - ang
        assert any(abs(rho - r2) < 1e-9 and abs(wrap(mirrored - a2)) < 1e-9 for r2, a2 in polar)


# --- mesh alignment ---------------------------------------------------------

def nearest(angles_base: float, step: float, target: float) -> float:
    return wrap(angles_base - target + step * round((target - angles_base) / step))


def assert_tooth_faces_gap(a: gm.GearParams, ca, ta, b: gm.GearParams, cb, tb):
    d_a, d_b = gm.contact_directions(a.gear_type, ca, b.gear_type, cb)
    delta_a = -nearest(ta, a.angular_pitch, d_a)
    same_direction = a.internal or b.internal
    delta_b = delta_a * a.teeth / b.teeth * (1 if same_direction else -1)
    gap_offset = nearest(tb + delta_b + b.angular_pitch / 2, b.angular_pitch, d_b)
    assert gap_offset == pytest.approx(0, abs=1e-9)
    pa = (ca[0] + a.pitch_radius * math.cos(d_a), ca[1] + a.pitch_radius * math.sin(d_a))
    pb = (cb[0] + b.pitch_radius * math.cos(d_b), cb[1] + b.pitch_radius * math.sin(d_b))
    assert pa == pytest.approx(pb, abs=1e-9)


@pytest.mark.parametrize('za, zb, angle_deg, tb', [(22, 22, 0, 0.0), (20, 31, 37, 0.11), (13, 50, -120, 0.5),
                                                   (40, 9, 200, 1.3)])
def test_external_pair_meshes_after_alignment(za, zb, angle_deg, tb):
    a, b = ext(za), ext(zb)
    cb = (5.0, 2.0)
    ang = math.radians(angle_deg)
    dist = a.pitch_radius + b.pitch_radius
    ca = (cb[0] + dist * math.cos(ang), cb[1] + dist * math.sin(ang))
    ta = gm.align_theta0(a, ca, b, cb, tb)
    assert_tooth_faces_gap(a, ca, ta, b, cb, tb)
    assert_tooth_faces_gap(b, cb, tb, a, ca, ta)
    assert gm.check_mesh(a, ca, b, cb, b.pitch_radius).warnings == []


@pytest.mark.parametrize('zr, zp, angle_deg, t_partner, new_is_ring',
                         [(60, 20, 0, 0.0, True), (72, 23, 51, 0.2, True), (60, 20, -75, 0.4, False),
                          (45, 17, 160, 2.0, False)])
def test_internal_pair_meshes_after_alignment(zr, zp, angle_deg, t_partner, new_is_ring):
    r, p = ring(zr), ext(zp)
    cr = (1.0, -4.0)
    ang = math.radians(angle_deg)
    dist = r.pitch_radius - p.pitch_radius
    cp = (cr[0] + dist * math.cos(ang), cr[1] + dist * math.sin(ang))
    if new_is_ring:
        tp = t_partner
        tr = gm.align_theta0(r, cr, p, cp, tp)
    else:
        tr = t_partner
        tp = gm.align_theta0(p, cp, r, cr, tr)
    assert_tooth_faces_gap(r, cr, tr, p, cp, tp)
    assert_tooth_faces_gap(p, cp, tp, r, cr, tr)


@pytest.mark.parametrize('internal', [False, True])
def test_mesh_error(internal):
    if internal:
        a, b = ring(60), ext(20)
        ca, cb = (0.0, 0.0), (a.pitch_radius - b.pitch_radius, 0.0)
    else:
        a, b = ext(22), ext(15)
        ca, cb = (0.0, 0.0), (a.pitch_radius + b.pitch_radius, 0.0)
    tb = 0.3
    ta = gm.align_theta0(a, ca, b, cb, tb)
    assert gm.mesh_error(a, ca, ta, b, cb, tb) == pytest.approx(0, abs=1e-9)
    assert gm.mesh_error(b, cb, tb, a, ca, ta) == pytest.approx(0, abs=1e-9)
    assert gm.mesh_error(a, ca, ta + a.angular_pitch / 2, b, cb, tb) == pytest.approx(0.5)
    assert gm.mesh_error(a, ca, ta + a.angular_pitch, b, cb, tb) == pytest.approx(0, abs=1e-9)


# --- collision sweeps (independent polygon check, full tooth of rotation) ----

def _points(segments, arc_step=0.01, spline_step=0.03):
    pts = []
    for s in segments:
        if isinstance(s, gm.Arc):
            n = max(2, int(s.sweep / arc_step))
            pts += [s.point_at(s.start_angle + s.sweep * i / n) for i in range(n)]
        else:
            ps = s.points if isinstance(s, gm.Spline) else [s.start, s.end]
            for (x0, y0), (x1, y1) in zip(ps, ps[1:]):
                n = max(1, int(math.hypot(x1 - x0, y1 - y0) / spline_step))
                pts += [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(n)]
    return pts


def inside(poly, pt) -> bool:
    x, y = pt
    result = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            result = not result
        j = i
    return result


def _near(pts, center, radius):
    return [p for p in pts if math.dist(p, center) < radius]


def local_material(loop, center, facing, half_width, close_radius):
    """Material polygon of one gear near the mesh: the part of its (CCW) profile loop within an
    angular window, closed by an arc at close_radius (inside for an external gear, outside for a ring)."""
    def rel(p):
        return wrap(math.atan2(p[1] - center[1], p[0] - center[0]) - facing)
    mask = [abs(rel(p)) <= half_width for p in loop]
    n = len(loop)
    start = next(i for i in range(n) if mask[i] and not mask[i - 1])
    chain = []
    i = start
    while mask[i % n]:
        chain.append(loop[i % n])
        i += 1
    a0, a1 = rel(chain[0]), rel(chain[-1])
    arc = [(center[0] + close_radius * math.cos(facing + a1 + (a0 - a1) * j / 40),
            center[1] + close_radius * math.sin(facing + a1 + (a0 - a1) * j / 40)) for j in range(41)]
    return chain + arc


def in_window(pts, center, facing, half_width):
    return [p for p in pts if abs(wrap(math.atan2(p[1] - center[1], p[0] - center[0]) - facing)) < half_width]


def ring_pinion_collides(zr, zp, backlash=B, k=1.0, steps=24, ignore_flank_contact=False, trim=True):
    """Polygon sweep: the trimmed ring (as the gear command draws it) against the pinion.

    ignore_flank_contact (for zero backlash, where flanks touch by design): only count hits that
    involve the pinion's non-working root or the ring's tip.
    """
    r, p = ring(zr, backlash=backlash, k=k), ext(zp, backlash=backlash, k=k)
    r_radii, p_radii = gm.gear_radii(r, (p,) if trim else ()), gm.gear_radii(p)
    cp = (0.0, r.pitch_radius - p.pitch_radius)
    tr0 = gm.align_theta0(r, (0.0, 0.0), p, cp, 0.0)
    up = math.pi / 2
    ring_window = math.asin(min(1.0, (p_radii.tip + 1.0) / cp[1])) + 0.1

    def counts(q):
        if not ignore_flank_contact:
            return True
        return math.dist(q, cp) < p_radii.form - 1e-6 or math.hypot(*q) < r_radii.tip + 0.02

    for i in range(steps):
        d = i / steps * p.angular_pitch
        pin_loop = _points(gm.build_profile(p, cp, d, 30).segments)
        ring_loop = _points(gm.build_profile(r, (0.0, 0.0), tr0 + d * zp / zr, 30, radii=r_radii).segments)
        pin_mat = local_material(pin_loop, cp, up, 1.4, p_radii.root * 0.5)
        ring_mat = local_material(ring_loop, (0.0, 0.0), up, ring_window, r_radii.root + 2 * r.module)
        for q in in_window(ring_loop, cp, up, 1.3):
            if math.dist(q, cp) < p_radii.tip + 0.1 and inside(pin_mat, q) and counts(q):
                return True
        for q in in_window(pin_loop, (0.0, 0.0), up, ring_window - 0.05):
            if q[1] > cp[1] and inside(ring_mat, q) and counts(q):
                return True
    return False


@pytest.mark.parametrize('zp', [10, 11, 12, 13, 14])
def test_small_planets_in_rings_dont_collide_after_trim(zp):
    """The planetary geometry with sun = 2·planet, so ring = 4·planet."""
    assert not ring_pinion_collides(4 * zp, zp)


def test_small_planet_collides_without_trim():
    """Sanity check for the sweep itself: the untrimmed ring hits a 10-tooth planet."""
    r, p = ring(40, backlash=B), ext(10, backlash=B)
    cp = (0.0, r.pitch_radius - p.pitch_radius)
    tr0 = gm.align_theta0(r, (0.0, 0.0), p, cp, 0.0)
    hit = False
    for i in range(24):
        d = i / 24 * p.angular_pitch
        pin_mat = local_material(_points(gm.build_profile(p, cp, d, 30).segments), cp, math.pi / 2, 1.4, 3.0)
        ring_pts = in_window(_points(gm.build_profile(r, (0.0, 0.0), tr0 + d * 10 / 40, 30).segments), cp,
                             math.pi / 2, 1.3)
        if any(inside(pin_mat, q) for q in ring_pts if math.dist(q, cp) < 12.1):
            hit = True
            break
    assert hit


def test_ring_trim_at_zero_backlash():
    assert not ring_pinion_collides(40, 10, backlash=0.0, ignore_flank_contact=True)
    trim = gm.ring_trim(ring(40), ext(10))
    assert trim.clears and trim.required_tip > ring(40).tip_radius


def ext_pair_collides(za, zb, backlash=B, steps=24, offset=0.0):
    a, b = ext(za, backlash=backlash), ext(zb, backlash=backlash)
    cb = (a.pitch_radius + b.pitch_radius, 0.0)
    ta0 = gm.align_theta0(a, (0.0, 0.0), b, cb, 0.0) + offset
    ra_, rb_ = gm.gear_radii(a), gm.gear_radii(b)
    for i in range(steps):
        d = i / steps * a.angular_pitch
        pa = _points(gm.build_profile(a, (0.0, 0.0), ta0 + d, 30).segments)
        pb = _points(gm.build_profile(b, cb, -d * za / zb, 30).segments)
        mat_a = local_material(pa, (0.0, 0.0), 0.0, 1.0, ra_.root * 0.5)
        mat_b = local_material(pb, cb, math.pi, 1.0, rb_.root * 0.5)
        if any(inside(mat_b, q) for q in in_window(pa, (0.0, 0.0), 0.0, 0.9)) or                 any(inside(mat_a, q) for q in in_window(pb, cb, math.pi, 0.9)):
            return True
    return False


@pytest.mark.parametrize('za, zb', [(8, 8), (10, 10), (10, 20), (10, 40), (12, 30), (14, 14), (8, 60)])
def test_small_external_pairs_dont_collide(za, zb):
    assert not ext_pair_collides(za, zb)


def test_sweeps_detect_collisions():
    """Sanity checks for the sweeps themselves."""
    assert ext_pair_collides(14, 23, offset=ext(14).angular_pitch / 2, steps=4)
    assert ring_pinion_collides(40, 10, trim=False)


def test_analytic_external_check_agrees():
    assert not gm.pair_collides(ext(10, backlash=B), ext(10, backlash=B))
    assert not gm.pair_collides(ext(10, backlash=B), ext(40, backlash=B))


def test_trim_reports_the_pinion_that_set_it():
    r = ring(60, backlash=B)
    radii = gm.gear_radii(r, (ext(20, backlash=B), ext(12, backlash=B), ext(15, backlash=B)))
    assert radii.trim is not None and radii.trim.pinion.teeth == 12
    assert radii.tip > r.tip_radius


def test_pinion_tips_hitting_ring_gets_its_own_message():
    """A real small-difference pair: 20 teeth in a 21-tooth ring can't be fixed by trimming."""
    p = ext(20, backlash=B)
    tight = ring(21, backlash=B)
    trim = gm.ring_trim(tight, p)
    assert not trim.clears and trim.limited_by == 'tip'
    report = gm.pair_report(tight, gm.gear_radii(tight, (p,)), p)
    assert any("tips hit the ring's teeth" in w and "can't fix" in w and 'at least 24 teeth' in w
               for w in report.warnings)
    # The N in the message is right: 22 and 23 still fail, 24 clears.
    assert not gm.ring_trim(ring(22, backlash=B), p).clears
    assert not gm.ring_trim(ring(23, backlash=B), p).clears
    assert gm.ring_trim(ring(24, backlash=B), p).clears


def test_tip_limited_trim_clears_in_polygon_sweep():
    """24 teeth around a 20-tooth pinion: cleared by trimming for the pinion's tips."""
    assert gm.ring_trim(ring(24, backlash=B), ext(20, backlash=B)).limited_by == 'tip'
    assert not ring_pinion_collides(24, 20)


def test_later_pinion_warns_when_ring_tips_are_too_deep():
    r, p = ring(40, backlash=B), ext(10, backlash=B)
    report = gm.pair_report(p, gm.gear_radii(p), r, partner_tip=r.tip_radius)
    assert any('Edit the ring' in w and '10-tooth' in w for w in report.warnings)
    trimmed = gm.gear_radii(r, (p,)).tip
    report2 = gm.pair_report(p, gm.gear_radii(p), r, partner_tip=trimmed)
    assert not any('Edit the ring' in w for w in report2.warnings)


# --- planetary sets ------------------------------------------------------------

def plan(zs, zp, n, k=1.0, backlash=B):
    return gm.check_planetary(2.0, DEG20, k, backlash, zs, zp, n)


def test_planetary_passing_set():
    res = plan(30, 15, 3)
    assert res.ok and res.good
    assert res.ring.teeth == 60
    assert res.ratio == pytest.approx(3.0)
    assert res.orbit_radius == pytest.approx(45.0)


def test_planetary_borderline_set_passes_with_warning():
    res = plan(20, 10, 3)
    assert res.ok and not res.good
    assert any('contact ratio' in w for w in res.warnings)


def test_planetary_blocks_below_contact_ratio_1():
    res = plan(16, 8, 3)
    assert res.rules[0].ok and res.rules[1].ok
    assert not res.ok
    assert not res.rules[2].ok and 'contact ratio 0.9' in res.rules[2].detail
    assert not res.rules[3].ok


def test_planetary_spacing_rule_fails():
    res = plan(18, 11, 3)
    assert not res.ok
    assert not res.rules[0].ok and 'not a whole number' in res.rules[0].detail


def test_planetary_planets_collide_with_each_other():
    res = plan(10, 20, 6)
    assert res.rules[0].ok and not res.rules[1].ok


def test_planetary_spacing_rule_matches_mesh_alignment():
    """With the spacing rule met, planets aligned to the sun all mesh with a ring aligned to one planet."""
    for zs, zp, n, expect in ((20, 10, 3, True), (24, 18, 4, True), (18, 11, 3, False)):
        sun, pl, rg = gm.planetary_params(2.0, DEG20, 1.0, 0.0, zs, zp)
        orbit = sun.pitch_radius + pl.pitch_radius
        centers = [(orbit * math.cos(2 * math.pi * i / n), orbit * math.sin(2 * math.pi * i / n)) for i in range(n)]
        t_pl = [gm.align_theta0(pl, c, sun, (0, 0), 0.0) for c in centers]
        t_ring = gm.align_theta0(rg, (0, 0), pl, centers[0], t_pl[0])
        meshed = all(gm.mesh_error(rg, (0, 0), t_ring, pl, c, t) < 1e-9 for c, t in zip(centers, t_pl))
        assert meshed == expect == gm.spacing_ok(zs, zp, n)


@pytest.mark.parametrize('zs, zp, n', [(18, 11, 3), (20, 10, 3), (10, 20, 6)])
def test_planetary_suggestions_are_valid(zs, zp, n):
    suggestions = gm.suggest_planetary(2.0, DEG20, 1.0, B, zs, zp, n)
    assert suggestions
    for s_zs, s_zp in suggestions:
        assert plan(s_zs, s_zp, n).good


# --- validation ---------------------------------------------------------------

def test_check_params():
    assert gm.check_params(ext(5)).errors
    assert gm.check_params(gm.GearParams(0, 20, DEG20)).errors
    assert gm.check_params(ext(16)).warnings
    assert not gm.check_params(ring(60)).errors


def test_check_mesh_errors_and_warnings():
    a = ext(20)
    assert gm.check_mesh(a, (0, 0), ext(20, m=1.5), (35, 0), 15).errors
    assert gm.check_mesh(a, (0, 0), ext(20, alpha=math.radians(25)), (40, 0), 20).errors
    assert gm.check_mesh(ring(60), (0, 0), ring(80), (20, 0), 80).errors
    assert gm.check_mesh(a, (0, 0), ext(20), (0, 0), 20).errors
    not_tangent = gm.check_mesh(a, (0, 0), ext(20), (41, 0), 20)
    assert not not_tangent.errors and not_tangent.warnings
    resized = gm.check_mesh(a, (0, 0), ext(20), (40, 0), 21)
    assert resized.warnings


def test_check_sizing():
    assert gm.check_sizing(43.0, ext(22), resize=False).warnings
    assert not gm.check_sizing(43.0, ext(22), resize=True).warnings


# --- metadata -----------------------------------------------------------------

def test_attribute_roundtrip():
    params = gm.GearParams(2.5, 31, math.radians(14.5), 0.1, gm.INTERNAL, 0.8)
    record = gm.GearRecord(params, 0.42, 'abc', rotation_offset=0.1, resize=False, reference_circles=True,
                           mesh_with='xyz', tip_radius=37.9)
    parsed = gm.from_attribute(gm.to_attribute(record))
    assert parsed is not None
    p2 = parsed.params
    assert p2.module == 2.5 and p2.teeth == 31 and p2.internal and p2.backlash == 0.1 and p2.height_factor == 0.8
    assert p2.pressure_angle == pytest.approx(params.pressure_angle)
    assert (parsed.theta0, parsed.gear_id, parsed.rotation_offset) == (0.42, 'abc', 0.1)
    assert (parsed.resize, parsed.reference_circles, parsed.mesh_with) == (False, True, 'xyz')
    assert parsed.drawn_tip == 37.9
    assert parsed.editable
    assert gm.from_attribute('not json') is None
    assert gm.from_attribute('{"version": 1}') is None
    assert gm.from_attribute('[1, 2]') is None


def test_missing_factor_defaults_to_standard():
    v2 = ('{"version": 2, "id": "a", "type": "internal", "module_mm": 2.0, "pressure_angle_deg": 20.0, '
          '"teeth": 40, "theta0_rad": 0.0, "backlash_mm": 0.05}')
    rec = gm.from_attribute(v2)
    assert rec.params.height_factor == 1.0
    assert rec.drawn_tip == pytest.approx(gm.gear_radii(rec.params).tip)  # no stored tip: nominal, no trim


def test_version1_attribute_still_reads():
    v1 = ('{"version": 1, "type": "external", "module_mm": 2.0, "pressure_angle_deg": 20.0, '
          '"teeth": 22, "theta0_rad": 0.0, "backlash_mm": 0.0}')
    rec = gm.from_attribute(v1)
    assert rec is not None and rec.params.teeth == 22 and not rec.editable and rec.params.height_factor == 1.0


def test_plan_attribute_roundtrip():
    plan_rec = gm.PlanRecord('s1', 'ring', 0, 20, 10, 3, 2.0, DEG20, 0.8)
    parsed = gm.from_plan_attribute(gm.to_plan_attribute(plan_rec))
    assert parsed == plan_rec
    assert parsed.teeth == 40 and parsed.gear_type == gm.INTERNAL
    assert gm.from_plan_attribute('{"role": "moon"}') is None


def test_to_svg_writes_file():
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, 'test_to_svg.svg')
    gm.to_svg([gm.build_profile(ext(12)), gm.build_profile(ring(40))], out)
    with open(out, encoding='utf-8') as f:
        assert f.read().startswith('<svg')


# --- racks -----------------------------------------------------------------

def rack(m: float = 2.0, alpha: float = DEG20, backlash: float = 0.0, k: float = 1.0) -> gm.RackParams:
    return gm.RackParams(m, alpha, backlash, k)


RACKS = [rack(), rack(backlash=B), rack(1.0, math.radians(14.5), 0.1), rack(3.0, math.radians(25), B),
         rack(k=0.8), rack(k=1.25), rack(1.5, math.radians(25), 0.0, 1.3)]


def _points_of(segments):
    return [p for seg in segments for p in (seg.start, seg.end)]


@pytest.mark.parametrize('n', [1, 5, 12])
def test_rack_teeth_for_length(n):
    p = rack().pitch
    assert gm.rack_teeth_for_length(n * p, rack()) == n
    assert gm.rack_teeth_for_length(n * p - 0.01, rack()) == n
    assert gm.rack_teeth_for_length(n * p + 0.01, rack()) == n
    assert gm.rack_teeth_for_length((n + 0.5) * p - 1e-6, rack()) == n
    assert gm.rack_teeth_for_length((n + 0.5) * p + 1e-6, rack()) == n + 1


def test_rack_teeth_for_length_is_at_least_one():
    assert gm.rack_teeth_for_length(0.1, rack()) == 1


def test_rack_tooth_centers():
    params = rack()
    p = params.pitch
    assert gm.rack_tooth_centers(5 * p, params) == pytest.approx([p / 2 + i * p for i in range(5)])
    assert gm.rack_tooth_centers(5 * p, params, p) == pytest.approx(gm.rack_tooth_centers(5 * p, params))
    assert gm.rack_tooth_centers(5 * p, params, -2 * p) == pytest.approx(gm.rack_tooth_centers(5 * p, params))
    shifted = gm.rack_tooth_centers(5 * p, params, 0.3)
    assert len(shifted) == 4
    for s in shifted + gm.rack_tooth_centers(7.3 * p, params, -1.1):
        assert -1e-9 <= s - p / 2 and s + p / 2 <= 7.3 * p + 1e-9
    assert gm.rack_tooth_centers(0.9 * p, params) == []


def test_rack_offset_shifts_pattern_by_offset_mod_pitch():
    params = rack()
    p = params.pitch
    base = gm.rack_tooth_centers(10 * p, params)
    for offset in (0.4, -0.4, 2.5 * p + 0.1):
        moved = gm.rack_tooth_centers(10 * p, params, offset)
        shift = offset % p
        assert moved
        for s in moved:
            assert min(abs((s - shift) - b) for b in base) < 1e-9


@pytest.mark.parametrize('params', RACKS)
def test_rack_tooth_width_at_pitch_line(params):
    """p/2 − B/2: each part of a mesh takes half the backlash, as gear teeth do."""
    segs = gm.rack_local_segments(params, 3 * params.pitch)
    crossings = sorted(seg.start[0] + (seg.end[0] - seg.start[0]) * (0 - seg.start[1]) / (seg.end[1] - seg.start[1])
                       for seg in segs if isinstance(seg, gm.Line) and min(seg.start[1], seg.end[1]) < 0
                       < max(seg.start[1], seg.end[1]))
    assert len(crossings) == 6
    for left, right in zip(crossings[0::2], crossings[1::2]):
        assert right - left == pytest.approx(params.pitch / 2 - params.backlash / 2, abs=1e-9)


@pytest.mark.parametrize('params', RACKS)
def test_rack_tip_and_root_heights(params):
    pts = _points_of(gm.rack_local_segments(params, 4 * params.pitch))
    assert max(h for _, h in pts) == pytest.approx(params.height_factor * params.module, abs=1e-12)
    assert min(h for _, h in pts) == pytest.approx(-(params.height_factor + 0.25) * params.module, abs=1e-12)


def test_rack_space_is_the_gear_cutter():
    params = rack(k=0.8, backlash=B)
    gear = gm.GearParams(params.module, 30, params.pressure_angle, params.backlash, gm.EXTERNAL, 0.8)
    assert gm.rack_cutter(params) == gm.cutter(gear)


def test_cutter_values_unchanged_by_refactor():
    c = gm.cutter(ext(20, backlash=B))   # values from the code before _cutter was split out
    assert (c.depth, c.half_width) == pytest.approx((2.5, math.pi / 2 + B / 4))
    assert c.corner_radius == pytest.approx(0.76)
    assert c.corner_x == pytest.approx(math.pi / 2 + B / 4 - (2.5 - 0.76) * math.tan(DEG20) - 0.76 / math.cos(DEG20))
    assert c.straight_depth == pytest.approx(2.5 - 0.76 * (1 - math.sin(DEG20)))


@pytest.mark.parametrize('params', RACKS)
def test_rack_flank_angle_equals_pressure_angle(params):
    flanks = [s for s in gm.rack_local_segments(params, 3 * params.pitch)
              if isinstance(s, gm.Line) and abs(s.end[1] - s.start[1]) > 1e-9]
    assert len(flanks) == 6
    for f in flanks:
        dx, dh = f.end[0] - f.start[0], f.end[1] - f.start[1]
        assert math.atan2(abs(dx), abs(dh)) == pytest.approx(params.pressure_angle, abs=1e-12)


def _direction(seg, at_end: bool):
    """Unit direction of travel at the segment's start or end."""
    if isinstance(seg, gm.Arc):
        angle = seg.start_angle + (seg.sweep if at_end != seg.clockwise else 0.0)
        sign = -1.0 if seg.clockwise else 1.0
        return (-sign * math.sin(angle), sign * math.cos(angle))
    dx, dy = seg.end[0] - seg.start[0], seg.end[1] - seg.start[1]
    n = math.hypot(dx, dy)
    return dx / n, dy / n


@pytest.mark.parametrize('params', RACKS)
@pytest.mark.parametrize('side', [gm.LEFT, gm.RIGHT])
def test_rack_fillets_are_tangent(params, side):
    prof = gm.build_rack(params, (1.0, 2.0), 0.8, side, 4 * params.pitch, 0.7)
    segs = prof.segments
    cut = gm.rack_cutter(params)
    arcs = 0
    for i, seg in enumerate(segs):
        if not isinstance(seg, gm.Arc):
            continue
        arcs += 1
        assert seg.radius == pytest.approx(cut.corner_radius)
        before, after = segs[i - 1], segs[i + 1]
        assert _direction(before, True) == pytest.approx(_direction(seg, False), abs=1e-9)
        assert _direction(seg, True) == pytest.approx(_direction(after, False), abs=1e-9)
        depths = sorted(-prof_h for prof_h in (gm.rack_coords(prof.origin, prof.direction, side, seg.start)[1],
                                              gm.rack_coords(prof.origin, prof.direction, side, seg.end)[1]))
        assert depths[0] == pytest.approx(cut.straight_depth, abs=1e-12)   # flank end of the fillet
        assert depths[1] == pytest.approx(cut.depth, abs=1e-12)            # root end
    assert arcs == 2 * len(prof.tooth_centers)


def test_rack_full_round_root():
    params = rack(alpha=math.radians(25))
    assert gm.rack_cutter(params).corner_x == pytest.approx(0, abs=1e-12)
    segs = gm.rack_local_segments(params, 3 * params.pitch)
    roots = [s for s in segs if isinstance(s, gm.Line) and abs(s.start[1] - s.end[1]) < 1e-12 and s.start[1] < 0]
    assert roots == []  # the arcs meet at each space center; no flat root (offset 0, L = N·p)
    for a, b in zip(segs, segs[1:]):
        assert a.end == pytest.approx(b.start, abs=1e-9)


@pytest.mark.parametrize('params', RACKS)
@pytest.mark.parametrize('offset', [0.0, 1.3, -0.4])
def test_rack_edge_is_continuous_and_closes_with_body(params, offset):
    L = 6.4 * params.pitch
    hf = params.dedendum
    edge = gm.rack_local_segments(params, L, offset)
    assert edge[0].start == pytest.approx((0.0, -hf), abs=1e-12)
    assert edge[-1].end == pytest.approx((L, -hf), abs=1e-12)
    for body in (0.0, 5.0):
        segs = gm.rack_local_segments(params, L, offset, body)
        pairs = list(zip(segs, segs[1:])) + ([(segs[-1], segs[0])] if body else [])
        for a, b in pairs:
            assert a.end == pytest.approx(b.start, abs=1e-9)
    root_lines = [s for s in edge if isinstance(s, gm.Line) and abs(s.start[1] - s.end[1]) < 1e-12
                  and abs(s.start[1] + hf) < 1e-12]
    for a, b in zip(root_lines, root_lines[1:]):
        assert b.start[0] > a.end[0] + 1e-9  # root runs are merged, never split into collinear pieces


@pytest.mark.parametrize('direction_deg', [0, 30, 90, 137, 180, -60])
def test_rack_placement_and_sides(direction_deg):
    params = rack(backlash=B)
    origin, direction, L = (12.0, -5.0), math.radians(direction_deg), 5.2 * params.pitch
    local = gm.rack_local_segments(params, L, 0.5, 4.0)
    left = gm.build_rack(params, origin, direction, gm.LEFT, L, 0.5, 4.0)
    right = gm.build_rack(params, origin, direction, gm.RIGHT, L, 0.5, 4.0)
    u = (math.cos(direction), math.sin(direction))
    for loc, a, b in zip(local, left.segments, right.segments):
        for (s, h), pa, pb in ((loc.start, a.start, b.start), (loc.end, a.end, b.end)):
            assert pa == pytest.approx((origin[0] + s * u[0] - h * u[1], origin[1] + s * u[1] + h * u[0]), abs=1e-9)
            assert pb == pytest.approx((origin[0] + s * u[0] + h * u[1], origin[1] + s * u[1] - h * u[0]), abs=1e-9)
            assert gm.rack_coords(origin, direction, gm.RIGHT, pb) == pytest.approx((s, h), abs=1e-9)
        if isinstance(loc, gm.Arc):
            assert a.mid == pytest.approx(gm.rack_point(origin, direction, gm.LEFT, *loc.mid), abs=1e-9)
            assert b.mid == pytest.approx(gm.rack_point(origin, direction, gm.RIGHT, *loc.mid), abs=1e-9)
    for prof, side in ((left, 1), (right, -1)):
        tip = prof.point(prof.tooth_centers[0], params.addendum)
        cross = u[0] * (tip[1] - origin[1]) - u[1] * (tip[0] - origin[0])
        assert cross * side > 0  # teeth on the chosen side of start → end
        segs = prof.segments
        for a, b in zip(segs, segs[1:] + segs[:1]):
            assert a.end == pytest.approx(b.start, abs=1e-9)


def _rack_area(params: gm.RackParams, L: float, offset: float, body: float) -> float:
    """Independent area count: the strip under the roots plus, per tooth, its cell minus the two
    half-spaces (trapezoids up to the tip, less the material each root fillet adds)."""
    a, hf, alpha, p = params.addendum, params.dedendum, params.pressure_angle, params.pitch
    w = p / 4 + params.backlash / 4
    rho = gm.rack_cutter(params).corner_radius
    gamma = math.pi / 2 + alpha                     # air-side angle between root line and flank
    fillet = rho * rho * (1 / math.tan(gamma / 2) - (math.pi - gamma) / 2)
    half_space = w * (a + hf) + math.tan(alpha) * (a * a - hf * hf) / 2 - fillet
    tooth = p * (a + hf) - 2 * half_space
    return L * body + tooth * len(gm.rack_tooth_centers(L, params, offset))


@pytest.mark.parametrize('params', RACKS)
def test_rack_area(params):
    L, offset, body = 7.7 * params.pitch, 0.9, 3 * params.module
    expected = _rack_area(params, L, offset, body)
    for side in (gm.LEFT, gm.RIGHT):
        for direction in (0.0, 2.1):
            prof = gm.build_rack(params, (4.0, 9.0), direction, side, L, offset, body)
            assert abs(gm.profile_area(prof.segments)) == pytest.approx(expected, rel=1e-12)


def test_profile_area_of_circle_and_square():
    assert gm.profile_area([gm.Arc((1.0, 2.0), 3.0, 0.3, 2 * math.pi)]) == pytest.approx(math.pi * 9)
    assert gm.profile_area([gm.Arc((1.0, 2.0), 3.0, 0.3, 2 * math.pi, True)]) == pytest.approx(-math.pi * 9)
    square = [gm.Line((0, 0), (2, 0)), gm.Line((2, 0), (2, 2)), gm.Line((2, 2), (0, 2)), gm.Line((0, 2), (0, 0))]
    assert gm.profile_area(square) == pytest.approx(4.0)


@pytest.mark.parametrize('params', [rack(), rack(backlash=0.2), rack(alpha=math.radians(14.5)),
                                    rack(1.0, math.radians(25), B)])
def test_rack_factor_max(params):
    k_max = gm.rack_factor_max(params)
    at_max = gm.RackParams(params.module, params.pressure_angle, params.backlash, k_max)
    assert gm.rack_top_land(at_max) >= gm.MIN_TOP_LAND * params.module - 1e-9
    assert gm.rack_cutter(at_max).valid
    above = gm.RackParams(params.module, params.pressure_angle, params.backlash, k_max + 0.001)
    if k_max < gm.FACTOR_CAP:
        assert gm.rack_top_land(above) < gm.MIN_TOP_LAND * params.module or not gm.rack_cutter(above).valid
    assert gm.check_rack(at_max, 50, True).ok
    assert not gm.check_rack(above, 50, True).ok


def test_rack_factor_max_is_where_top_land_reaches_its_minimum_at_20_degrees():
    at_max = gm.RackParams(2.0, DEG20, 0.0, gm.rack_factor_max(rack()))
    assert gm.rack_top_land(at_max) == pytest.approx(gm.MIN_TOP_LAND * 2.0, abs=1e-3)


def test_check_rack():
    p = rack().pitch
    assert gm.check_rack(rack(), 10 * p, True).ok
    assert 'shorter than one tooth' in gm.check_rack(rack(), 0.9 * p, False).errors[0]
    assert gm.check_rack(rack(), 0.9 * p, True).ok          # resize makes it one tooth long
    assert 'at this offset' in gm.check_rack(rack(), 0.9 * p, True, offset=0.5).errors[0]
    assert 'at most' in gm.check_rack(rack(), 401 * p, True).errors[0]
    assert 'Backlash' in gm.check_rack(rack(backlash=p / 2), 50, True).errors[0]
    assert 'negative' in gm.check_rack(rack(backlash=-0.1), 50, True).errors[0]
    assert 'Backing' in gm.check_rack(rack(), 50, True, body=-1).errors[0]
    assert 'minimum' in gm.check_rack(rack(k=0.5), 50, True).errors[0]
    assert 'no length' in gm.check_rack(rack(), 0, True).errors[0]
    leftover = gm.check_rack(rack(), 10.5 * p, False)
    assert leftover.ok and 'has no teeth' in leftover.warnings[0]
    assert not gm.check_rack(rack(), 10 * p, False).warnings


def test_rack_attribute_roundtrip():
    params = gm.RackParams(2.5, math.radians(14.5), 0.1, 0.8)
    record = gm.RackRecord(params, 'r1', gm.RIGHT, 1.25, False, 7.5, 11, (3.0, -4.0), 0.7, 86.4, 'g9')
    parsed = gm.from_rack_attribute(gm.to_rack_attribute(record))
    assert parsed is not None
    assert parsed.params.module == 2.5 and parsed.params.backlash == 0.1 and parsed.params.height_factor == 0.8
    assert parsed.params.pressure_angle == pytest.approx(params.pressure_angle)
    assert (parsed.rack_id, parsed.side, parsed.offset, parsed.resize, parsed.body, parsed.teeth) == \
        ('r1', gm.RIGHT, 1.25, False, 7.5, 11)
    assert (parsed.origin, parsed.direction, parsed.length, parsed.mesh_with) == ((3.0, -4.0), 0.7, 86.4, 'g9')
    for bad in ('not json', '[1, 2]', '{"version": 1}', '{"id": "x", "side": 3}'):
        assert gm.from_rack_attribute(bad) is None
    assert gm.from_rack_attribute(gm.to_attribute(gm.GearRecord(ext(20), 0.0, 'g1'))) is None
    assert gm.from_attribute(gm.to_rack_attribute(record)) is None


def test_rack_svgs():
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')
    os.makedirs(out_dir, exist_ok=True)
    params = rack(backlash=B)
    for name, side in (('left', gm.LEFT), ('right', gm.RIGHT)):
        out = os.path.join(out_dir, f'rack_{name}.svg')
        gm.to_svg(gm.build_rack(params, (0.0, 0.0), math.radians(30), side, 6 * params.pitch, 0.0, 6.0), out)
        with open(out, encoding='utf-8') as f:
            assert f.read().startswith('<svg')
    out = os.path.join(out_dir, 'rack_open_offset.svg')
    gm.to_svg(gm.build_rack(rack(alpha=math.radians(25)), (0.0, 0.0), 0.0, gm.LEFT, 6 * params.pitch, 1.5), out)
