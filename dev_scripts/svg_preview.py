"""Write SVG previews of sample gears and meshing pairs to tests/out/. Runs without Fusion:

    python dev_scripts/svg_preview.py
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import gearmath as gm  # noqa: E402

OUT = os.path.join(ROOT, 'tests', 'out')


def external_pair(za: int, zb: int, m: float, backlash: float, angle_deg: float):
    a = gm.GearParams(m, za, math.radians(20), backlash)
    b = gm.GearParams(m, zb, math.radians(20), backlash)
    ca = (0.0, 0.0)
    ang = math.radians(angle_deg)
    d = a.pitch_radius + b.pitch_radius
    cb = (d * math.cos(ang), d * math.sin(ang))
    tb = 0.0
    ta = gm.align_theta0(a, ca, b, cb, tb)
    return [gm.build_profile(a, ca, ta, 40), gm.build_profile(b, cb, tb, 40)]


def ring_pair(zr: int, zp: int, m: float, backlash: float, angle_deg: float):
    r = gm.GearParams(m, zr, math.radians(20), backlash, gm.INTERNAL)
    p = gm.GearParams(m, zp, math.radians(20), backlash)
    cr = (0.0, 0.0)
    ang = math.radians(angle_deg)
    d = r.pitch_radius - p.pitch_radius
    cp = (d * math.cos(ang), d * math.sin(ang))
    tr = 0.0
    tp = gm.align_theta0(p, cp, r, cr, tr)
    return [gm.build_profile(r, cr, tr, 40), gm.build_profile(p, cp, tp, 40)]


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    cases = {
        'external_22_22.svg': external_pair(22, 22, 2.0, 0.0, 0.0),
        'external_12_31_angled.svg': external_pair(12, 31, 2.0, 0.15, 33.0),
        'ring_60_20.svg': ring_pair(60, 20, 2.0, 0.1, 70.0),
        'ring_24_clamped_tips.svg': [gm.build_profile(gm.GearParams(2.0, 24, math.radians(20), 0.0,
                                                                    gm.INTERNAL), points_per_flank=40)],
    }
    for name, profiles in cases.items():
        path = os.path.join(OUT, name)
        gm.to_svg(profiles, path)
        print(path)


if __name__ == '__main__':
    main()
