"""Render the command icons (16/32/64 px) from gearmath. Needs matplotlib; run without Fusion."""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import matplotlib  # noqa: E402
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Circle, Polygon  # noqa: E402

import gearmath as gm  # noqa: E402

OUT = os.path.join(ROOT, 'commands', 'gearProfile', 'resources')
OUT_PLANETARY = os.path.join(ROOT, 'commands', 'planetary', 'resources')


def outline(profile):
    pts = []
    for s in profile.segments:
        if isinstance(s, gm.Arc):
            pts += [s.point_at(s.start_angle + s.sweep * i / 6) for i in range(6)]
        elif isinstance(s, gm.Spline):
            pts += s.points[:-1]
        else:
            pts.append(s.start)
    return pts


def main():
    os.makedirs(OUT, exist_ok=True)
    prof = gm.build_profile(gm.GearParams(1.0, 10, math.radians(25)), theta0=math.pi / 2, points_per_flank=8)
    for size in (16, 32, 64):
        fig = plt.figure(figsize=(1, 1), dpi=size)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.add_patch(Polygon(outline(prof), closed=True, facecolor='#4a7fc1', edgecolor='#1d3d66',
                             linewidth=max(0.4, size / 64)))
        ax.add_patch(Circle((0, 0), 1.4, facecolor='white', edgecolor='#1d3d66', linewidth=max(0.4, size / 64)))
        ax.set_xlim(-6.1, 6.1)
        ax.set_ylim(-6.1, 6.1)
        ax.axis('off')
        fig.savefig(os.path.join(OUT, f'{size}x{size}.png'), transparent=True)
        plt.close(fig)


def planetary_icons():
    os.makedirs(OUT_PLANETARY, exist_ok=True)
    a = math.radians(25)
    sun = gm.build_profile(gm.GearParams(1.0, 8, a), theta0=0.2, points_per_flank=6)
    planets = []
    for i in range(3):
        ang = math.pi / 2 + 2 * math.pi * i / 3
        planets.append(gm.build_profile(gm.GearParams(1.0, 6, a), center=(7 * math.cos(ang), 7 * math.sin(ang)),
                                        theta0=ang, points_per_flank=6))
    for size in (16, 32, 64):
        fig = plt.figure(figsize=(1, 1), dpi=size)
        ax = fig.add_axes([0, 0, 1, 1])
        lw = max(0.4, size / 64)
        ax.add_patch(Circle((0, 0), 11.2, facecolor='none', edgecolor='#1d3d66', linewidth=2.2 * lw))
        ax.add_patch(Polygon(outline(sun), closed=True, facecolor='#4a7fc1', edgecolor='#1d3d66', linewidth=lw))
        for p in planets:
            ax.add_patch(Polygon(outline(p), closed=True, facecolor='#e0a040', edgecolor='#6a4a10', linewidth=lw))
        ax.set_xlim(-12, 12)
        ax.set_ylim(-12, 12)
        ax.axis('off')
        fig.savefig(os.path.join(OUT_PLANETARY, f'{size}x{size}.png'), transparent=True)
        plt.close(fig)


if __name__ == '__main__':
    main()
    planetary_icons()
