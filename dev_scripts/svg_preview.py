"""Write SVG previews of sample gears and meshing sets to tests/out/. Runs without Fusion:

    python dev_scripts/svg_preview.py
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import gearmath as gm  # noqa: E402

OUT = os.path.join(ROOT, 'tests', 'out')
ALPHA = math.radians(20)
BACKLASH = 0.05


def external_pair(za: int, zb: int, m: float = 2.0, angle_deg: float = 0.0):
    a = gm.GearParams(m, za, ALPHA, BACKLASH)
    b = gm.GearParams(m, zb, ALPHA, BACKLASH)
    ang = math.radians(angle_deg)
    d = a.pitch_radius + b.pitch_radius
    cb = (d * math.cos(ang), d * math.sin(ang))
    ta = gm.align_theta0(a, (0.0, 0.0), b, cb, 0.0)
    return [gm.build_profile(a, (0.0, 0.0), ta, 40), gm.build_profile(b, cb, 0.0, 40)]


def ring_pair(zr: int, zp: int, m: float = 2.0, angle_deg: float = 90.0):
    r = gm.GearParams(m, zr, ALPHA, BACKLASH, gm.INTERNAL)
    p = gm.GearParams(m, zp, ALPHA, BACKLASH)
    ang = math.radians(angle_deg)
    d = r.pitch_radius - p.pitch_radius
    cp = (d * math.cos(ang), d * math.sin(ang))
    tp = gm.align_theta0(p, cp, r, (0.0, 0.0), 0.0)
    return [gm.build_profile(r, (0.0, 0.0), 0.0, 40, radii=gm.gear_radii(r, (p,))),
            gm.build_profile(p, cp, tp, 40)]


def planetary(zs: int, zp: int, n: int, m: float = 2.0):
    sun, planet, ring = gm.planetary_params(m, ALPHA, 1.0, BACKLASH, zs, zp)
    orbit = sun.pitch_radius + planet.pitch_radius
    centers = [(orbit * math.cos(2 * math.pi * i / n), orbit * math.sin(2 * math.pi * i / n)) for i in range(n)]
    # The recommended order: sun, then planets aligned to the sun, then the ring aligned to planet 1.
    profiles = [gm.build_profile(sun, (0.0, 0.0), 0.0, 40)]
    t_planets = [gm.align_theta0(planet, c, sun, (0.0, 0.0), 0.0) for c in centers]
    profiles += [gm.build_profile(planet, c, t, 40) for c, t in zip(centers, t_planets)]
    t_ring = gm.align_theta0(ring, (0.0, 0.0), planet, centers[0], t_planets[0])
    profiles.insert(0, gm.build_profile(ring, (0.0, 0.0), t_ring, 40, radii=gm.gear_radii(ring, (planet,))))
    return profiles


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    cases = {
        'planet10_in_ring40_trimmed.svg': ring_pair(40, 10),
        'external_10_10_undercut.svg': external_pair(10, 10),
        'planetary_20_10_40_x3.svg': planetary(20, 10, 3),
        'external_22_22.svg': external_pair(22, 22),
        'ring_60_15_trimmed.svg': ring_pair(60, 15),
    }
    for name, profiles in cases.items():
        path = os.path.join(OUT, name)
        gm.to_svg(profiles, path)
        print(path)


if __name__ == '__main__':
    main()
