import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gearmath as gm  # noqa: E402

DEG20 = math.radians(20)


def ext(z: int, m: float = 2.0, alpha: float = DEG20, backlash: float = 0.0) -> gm.GearParams:
    return gm.GearParams(m, z, alpha, backlash, gm.EXTERNAL)


def ring(z: int, m: float = 2.0, alpha: float = DEG20, backlash: float = 0.0) -> gm.GearParams:
    return gm.GearParams(m, z, alpha, backlash, gm.INTERNAL)


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
                                    ext(30, alpha=math.radians(14.5)), ext(12, alpha=math.radians(25))])
def test_half_thickness_at_pitch_circle_equals_psi(params):
    assert gm.flank_angle(params, params.pitch_radius) == pytest.approx(params.half_angle, abs=1e-12)


def test_backlash_thins_teeth_and_widens_spaces_by_half():
    b = 0.2
    e0, e1 = ext(20), ext(20, backlash=b)
    # arc-length thickness at the pitch circle drops by B/2
    assert 2 * e0.pitch_radius * (e0.half_angle - e1.half_angle) == pytest.approx(b / 2)
    r0, r1 = ring(60), ring(60, backlash=b)
    assert 2 * r0.pitch_radius * (r1.half_angle - r0.half_angle) == pytest.approx(b / 2)


# --- hybrid sizing ----------------------------------------------------------

@pytest.mark.parametrize('d, m, z', [(43.0, 2.0, 22), (44.0, 2.0, 22), (40.9, 1.0, 41), (12.3, 0.5, 25),
                                     (100.0, 3.0, 33), (101.0, 3.0, 34)])
def test_suggest_teeth_is_round_d_over_m(d, m, z):
    assert gm.suggest_teeth(d, m) == round(d / m) == z


# --- profile geometry -------------------------------------------------------

ALL_PROFILES = [ext(22), ext(8), ext(50), ext(17, backlash=0.15), ring(60), ring(24), ring(45, backlash=0.1),
                ext(13, alpha=math.radians(14.5)), ring(30, alpha=math.radians(25))]


@pytest.mark.parametrize('params', ALL_PROFILES)
def test_profile_is_closed_loop(params):
    prof = gm.build_profile(params, center=(3.0, -7.0), theta0=0.3)
    segs = prof.segments
    for a, b in zip(segs, segs[1:] + segs[:1]):
        assert a.end == pytest.approx(b.start, abs=1e-9)


@pytest.mark.parametrize('params', ALL_PROFILES)
def test_flank_endpoints_land_on_stated_radii(params):
    center = (3.0, -7.0)
    prof = gm.build_profile(params, center=center, theta0=0.3)
    involute_start = max(params.base_radius, min(prof.tip_radius, prof.root_radius))
    outer = max(prof.tip_radius, prof.root_radius)
    inner = min(prof.tip_radius, prof.root_radius)
    for seg in prof.segments:
        if isinstance(seg, gm.Spline):
            ends = sorted(polar_of(center, p)[0] for p in (seg.start, seg.end))
            assert ends[0] == pytest.approx(involute_start, abs=1e-9)
            assert ends[1] == pytest.approx(outer, abs=1e-9)
        elif isinstance(seg, gm.Line):
            ends = sorted(polar_of(center, p)[0] for p in (seg.start, seg.end))
            assert ends == pytest.approx([inner, params.base_radius], abs=1e-9)
        else:
            assert seg.radius in (pytest.approx(inner), pytest.approx(outer))
            assert seg.sweep > 0


def test_external_radii_and_radial_foot():
    small = gm.build_profile(ext(20))  # rf < rb, needs radial lines
    assert small.tip_radius == pytest.approx(22.0)
    assert small.root_radius == pytest.approx(17.5)
    assert any(isinstance(s, gm.Line) for s in small.segments)
    big = gm.build_profile(ext(60))  # rf > rb
    assert not any(isinstance(s, gm.Line) for s in big.segments)
    assert len(big.segments) == 60 * 4


def test_internal_radii():
    prof = gm.build_profile(ring(60))
    assert prof.tip_radius == pytest.approx(58.0)
    assert prof.root_radius == pytest.approx(62.5)
    assert not prof.warnings


def test_internal_tip_clamped_to_base_circle():
    params = ring(24)  # r − m = 22 < rb = 22.55
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
    """Mirroring the profile across tooth 0's centerline maps it onto itself."""
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
    """Of angles base + k·step, the one nearest target (returned as a signed offset from target)."""
    return wrap(angles_base - target + step * round((target - angles_base) / step))


def assert_tooth_faces_gap(a: gm.GearParams, ca, ta, b: gm.GearParams, cb, tb):
    """Rotate the pair conjugately until a tooth of A points at the contact point, then check that a
    gap center of B sits exactly at the contact point."""
    d_a, d_b = gm.contact_directions(a.gear_type, ca, b.gear_type, cb)
    # A's tooth centerlines: θ₀ + kτ for both types (internal teeth too, per SPEC).
    delta_a = -nearest(ta, a.angular_pitch, d_a)          # rotation of A that brings a tooth to d_a
    same_direction = a.internal or b.internal
    delta_b = delta_a * a.teeth / b.teeth * (1 if same_direction else -1)
    gap_offset = nearest(tb + delta_b + b.angular_pitch / 2, b.angular_pitch, d_b)
    assert gap_offset == pytest.approx(0, abs=1e-9)
    # Contact point: the two pitch circles meet at the same physical point.
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


# Material check: with backlash, densely sampled points of one gear never fall inside the other's teeth.

def dense_polygon(prof: gm.Profile) -> list[tuple[float, float]]:
    pts = []
    for s in prof.segments:
        if isinstance(s, gm.Arc):
            n = max(2, int(s.sweep / 0.01))
            pts += [s.point_at(s.start_angle + s.sweep * i / n) for i in range(n)]
        elif isinstance(s, gm.Spline):
            pts += s.points[:-1]
        else:
            pts.append(s.start)
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


def test_external_pair_teeth_do_not_overlap():
    a, b = ext(14, backlash=0.1), ext(23, backlash=0.1)
    ca, cb = (0.0, 0.0), (a.pitch_radius + b.pitch_radius, 0.0)
    tb = 0.07
    ta = gm.align_theta0(a, ca, b, cb, tb)
    pa = dense_polygon(gm.build_profile(a, ca, ta, points_per_flank=80))
    pb = dense_polygon(gm.build_profile(b, cb, tb, points_per_flank=80))
    assert not any(inside(pb, p) for p in pa)
    assert not any(inside(pa, p) for p in pb)


def test_internal_pair_teeth_do_not_overlap():
    r, p = ring(60, backlash=0.1), ext(20, backlash=0.1)
    cr = (0.0, 0.0)
    cp = (0.0, r.pitch_radius - p.pitch_radius)
    tr = 0.02
    tp = gm.align_theta0(p, cp, r, cr, tr)
    ring_poly = dense_polygon(gm.build_profile(r, cr, tr, points_per_flank=80))
    pin_poly = dense_polygon(gm.build_profile(p, cp, tp, points_per_flank=80))
    # Ring material lies outside its profile loop, so every pinion point must be inside it.
    assert all(inside(ring_poly, q) for q in pin_poly)


def test_misaligned_pair_does_overlap():
    """Sanity check for the overlap test itself: half a tooth off should collide."""
    a, b = ext(14, backlash=0.1), ext(23, backlash=0.1)
    ca, cb = (0.0, 0.0), (a.pitch_radius + b.pitch_radius, 0.0)
    ta = gm.align_theta0(a, ca, b, cb, 0.0) + a.angular_pitch / 2
    pa = dense_polygon(gm.build_profile(a, ca, ta, points_per_flank=40))
    pb = dense_polygon(gm.build_profile(b, cb, 0.0, points_per_flank=40))
    assert any(inside(pb, p) for p in pa)


# --- validation -------------------------------------------------------------

def test_check_params():
    assert gm.check_params(ext(5)).errors
    assert gm.check_params(gm.GearParams(0, 20, DEG20)).errors
    assert gm.check_params(ext(16)).warnings  # undercut at 20°
    assert not gm.check_params(ext(17)).warnings
    assert not gm.check_params(ring(10)).errors


def test_undercut_threshold():
    assert gm.undercut_min_teeth(DEG20) == 17


def test_check_mesh_errors_and_warnings():
    a = ext(20)
    assert gm.check_mesh(a, (0, 0), ext(20, m=1.5), (35, 0), 15).errors
    assert gm.check_mesh(a, (0, 0), ext(20, alpha=math.radians(25)), (40, 0), 20).errors
    assert gm.check_mesh(ring(60), (0, 0), ring(80), (20, 0), 80).errors
    assert gm.check_mesh(a, (0, 0), ext(20), (0, 0), 20).errors
    not_tangent = gm.check_mesh(a, (0, 0), ext(20), (41, 0), 20)
    assert not not_tangent.errors and not_tangent.warnings
    close_ring = gm.check_mesh(a, (0, 0), ring(28), (8, 0), 28)
    assert not close_ring.errors and any('12' in w for w in close_ring.warnings)
    resized = gm.check_mesh(a, (0, 0), ext(20), (40, 0), 21)
    assert resized.warnings


def test_check_sizing():
    assert gm.check_sizing(43.0, ext(22), resize=False).warnings
    assert not gm.check_sizing(43.0, ext(22), resize=True).warnings


def test_attribute_roundtrip():
    params = gm.GearParams(2.5, 31, math.radians(14.5), 0.1, gm.INTERNAL)
    record = gm.GearRecord(params, 0.42, 'abc', rotation_offset=0.1, resize=False, reference_circles=True,
                           mesh_with='xyz')
    parsed = gm.from_attribute(gm.to_attribute(record))
    assert parsed is not None
    p2 = parsed.params
    assert p2.module == 2.5 and p2.teeth == 31 and p2.internal and p2.backlash == 0.1
    assert p2.pressure_angle == pytest.approx(params.pressure_angle)
    assert (parsed.theta0, parsed.gear_id, parsed.rotation_offset) == (0.42, 'abc', 0.1)
    assert (parsed.resize, parsed.reference_circles, parsed.mesh_with) == (False, True, 'xyz')
    assert parsed.editable
    assert gm.from_attribute('not json') is None
    assert gm.from_attribute('{"version": 1}') is None
    assert gm.from_attribute('[1, 2]') is None


def test_version1_attribute_still_reads():
    v1 = ('{"version": 1, "type": "external", "module_mm": 2.0, "pressure_angle_deg": 20.0, '
          '"teeth": 22, "theta0_rad": 0.0, "backlash_mm": 0.0}')
    rec = gm.from_attribute(v1)
    assert rec is not None and rec.params.teeth == 22 and not rec.editable


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


def test_to_svg_writes_file():
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, 'test_to_svg.svg')
    gm.to_svg([gm.build_profile(ext(12)), gm.build_profile(ring(40))], out)
    with open(out, encoding='utf-8') as f:
        assert f.read().startswith('<svg')
