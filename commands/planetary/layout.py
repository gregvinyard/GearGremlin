"""Drawing for the planetary helper: plain functions taking a sketch, so dev_scripts can call them."""
import math
import uuid

import adsk.core
import adsk.fusion

from ... import gearmath as gm
from ..gearProfile import drawing


def draw_planetary(center: adsk.fusion.SketchPoint, result: gm.PlanetaryResult) -> dict:
    """Draw a planetary set's pitch circles around a sketch point (see SPEC: "Planetary helper").

    Returns {'sun': circle, 'ring': circle, 'planets': [circles], 'set_id': str}.
    """
    sketch = center.parentSketch
    circles = sketch.sketchCurves.sketchCircles
    lines = sketch.sketchCurves.sketchLines
    cons = sketch.geometricConstraints
    dims = sketch.sketchDimensions
    cm = drawing.to_cm
    g = center.geometry
    n = result.planets
    set_id = uuid.uuid4().hex

    deferred = sketch.isComputeDeferred
    sketch.isComputeDeferred = True
    try:
        sun = circles.addByCenterRadius(center, cm(result.sun.pitch_radius))
        ring = circles.addByCenterRadius(center, cm(result.ring.pitch_radius))
        planets, spokes = [], []
        for i in range(n):
            ang = 2.0 * math.pi * i / n
            pc = adsk.core.Point3D.create(g.x + cm(result.orbit_radius) * math.cos(ang),
                                          g.y + cm(result.orbit_radius) * math.sin(ang), 0)
            planet = circles.addByCenterRadius(pc, cm(result.planet.pitch_radius))
            spoke = lines.addByTwoPoints(center, planet.centerSketchPoint)
            spoke.isConstruction = True
            planets.append(planet)
            spokes.append(spoke)
    finally:
        sketch.isComputeDeferred = deferred

    for planet in planets:
        cons.addTangent(sun, planet)
        cons.addTangent(ring, planet)
    cons.addHorizontal(spokes[0])
    if n == 2:
        cons.addCollinear(spokes[0], spokes[1])
    else:
        step = 2.0 * math.pi / n
        text_r = cm(result.sun.pitch_radius) * 0.6
        for i in range(1, n):
            mid = step * (i - 0.5)
            text = adsk.core.Point3D.create(g.x + text_r * math.cos(mid), g.y + text_r * math.sin(mid), 0)
            dim = dims.addAngularDimension(spokes[i - 1], spokes[i], text)
            dim.parameter.value = step
    r_sun = cm(result.sun.pitch_radius)
    dims.addDiameterDimension(sun, adsk.core.Point3D.create(g.x - r_sun * 0.7, g.y + r_sun * 0.7, 0))
    p0 = planets[0].centerSketchPoint.geometry
    r_p = cm(result.planet.pitch_radius)
    dims.addDiameterDimension(planets[0], adsk.core.Point3D.create(p0.x + r_p * 0.7, p0.y + r_p * 0.7, 0))

    def plan(role: str, index: int = 0) -> gm.PlanRecord:
        return gm.PlanRecord(set_id, role, index, result.sun.teeth, result.planet.teeth, n,
                             result.sun.module, result.sun.pressure_angle, result.sun.height_factor)

    drawing.write_plan(sun, plan('sun'))
    drawing.write_plan(ring, plan('ring'))
    for i, planet in enumerate(planets):
        drawing.write_plan(planet, plan('planet', i))
    return {'sun': sun, 'ring': ring, 'planets': planets, 'set_id': set_id}
