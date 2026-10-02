import os
from typing import Optional

import adsk.core
import adsk.fusion

from ...lib import fusionAddInUtils as futil
from ... import config
from ... import gearmath as gm
from ..common import inputs as ci
from ..gearProfile import settings
from . import layout

app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_planetary'
CMD_NAME = 'Planetary Set'
CMD_Description = 'Check a sun, planet and ring tooth-count combination and draw its pitch circles.'

WORKSPACE_ID = 'FusionSolidEnvironment'
PANEL_ID = 'SketchCreatePanel'
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

local_handlers = []

CENTER = 'center'
SUN = 'sun_teeth'
PLANET = 'planet_teeth'
PLANETS = 'planets'
INFO = 'info'

_last_text = {}


def start():
    cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER)
    futil.add_handler(cmd_def.commandCreated, command_created)
    panel = ui.workspaces.itemById(WORKSPACE_ID).toolbarPanels.itemById(PANEL_ID)
    panel.controls.addCommand(cmd_def).isPromoted = False


def stop():
    panel = ui.workspaces.itemById(WORKSPACE_ID).toolbarPanels.itemById(PANEL_ID)
    control = panel.controls.itemById(CMD_ID)
    if control:
        control.deleteMe()
    definition = ui.commandDefinitions.itemById(CMD_ID)
    if definition:
        definition.deleteMe()


def command_created(args: adsk.core.CommandCreatedEventArgs):
    futil.log(f'{CMD_NAME} Command Created Event')
    _last_text.clear()
    remembered = settings.load()
    inputs = args.command.commandInputs

    center = inputs.addSelectionInput(CENTER, 'Center', 'Select a sketch point for the sun\'s center')
    center.addSelectionFilter('SketchPoints')
    center.setSelectionLimits(1, 1)
    center.tooltip = 'The sun gear\'s center. The circles are drawn in this point\'s sketch.'
    if ui.activeSelections.count == 1:
        point = adsk.fusion.SketchPoint.cast(ui.activeSelections.item(0).entity)
        if point is not None:
            center.addSelection(point)

    ci.add_module(inputs, remembered)
    ci.add_pressure(inputs, remembered)
    ci.add_height(inputs, remembered)
    sun = inputs.addIntegerSpinnerCommandInput(SUN, 'Sun teeth', gm.MIN_TEETH, gm.MAX_TEETH, 1, 20)
    sun.tooltip = 'Teeth on the central gear.'
    planet = inputs.addIntegerSpinnerCommandInput(PLANET, 'Planet teeth', gm.MIN_TEETH, gm.MAX_TEETH, 1, 15)
    planet.tooltip = 'Teeth on each planet gear. The ring gets sun + 2 × planet teeth.'
    count = inputs.addIntegerSpinnerCommandInput(PLANETS, 'Planets', 2, 12, 1, 3)
    count.tooltip = 'How many planet gears, spaced evenly around the sun.'
    ci.add_backlash(inputs, remembered)
    inputs.addTextBoxCommandInput(INFO, '', '', 12, True)
    _update(inputs)

    cmd = args.command
    futil.add_handler(cmd.execute, command_execute, local_handlers=local_handlers)
    futil.add_handler(cmd.inputChanged, command_input_changed, local_handlers=local_handlers)
    futil.add_handler(cmd.executePreview, command_preview, local_handlers=local_handlers)
    futil.add_handler(cmd.validateInputs, command_validate_input, local_handlers=local_handlers)
    futil.add_handler(cmd.destroy, command_destroy, local_handlers=local_handlers)


def _center(inputs) -> Optional[adsk.fusion.SketchPoint]:
    sel = inputs.itemById(CENTER)
    return adsk.fusion.SketchPoint.cast(sel.selection(0).entity) if sel.selectionCount else None


def _evaluate(inputs) -> tuple:
    """(PlanetaryResult or None, blocking errors). Gear-level errors (e.g. tooth height out of range)
    are checked first, since the planetary checks need valid gears."""
    module = ci.module_mm(inputs)
    if module <= 0:
        return None, ['Module must be greater than 0.']
    args = (module, ci.pressure_rad(inputs), ci.height_factor(inputs), ci.backlash_mm(inputs),
            inputs.itemById(SUN).value, inputs.itemById(PLANET).value)
    errors = []
    for params in gm.planetary_params(*args):
        errors += [e for e in gm.check_params(params).errors if e not in errors]
    if errors:
        return None, errors
    return gm.check_planetary(*args, inputs.itemById(PLANETS).value), []


def _set_text(inputs, input_id: str, html: str) -> None:
    if _last_text.get(input_id) != html:
        _last_text[input_id] = html
        inputs.itemById(input_id).formattedText = html


def _update(inputs) -> None:
    lines = []
    module = ci.module_mm(inputs)
    if module > 0:
        gears = gm.planetary_params(module, ci.pressure_rad(inputs), 1.0, ci.backlash_mm(inputs),
                                    inputs.itemById(SUN).value, inputs.itemById(PLANET).value)
        k_hi = min(gm.factor_max(p) for p in gears)
        _set_text(inputs, ci.HEIGHT_NOTE, f'<span style="color:#707070">Valid: {gm.FACTOR_FLOOR:.2f}–{k_hi:.2f}. '
                                          'Contact ratios are checked below.</span>')
    res, errors = _evaluate(inputs)
    if res is None:
        _set_text(inputs, INFO, '<br>'.join(f'<span style="color:#d03030">✖ {e}</span>' for e in errors))
        return
    lines.append(f'Ring: {res.ring.teeth} teeth')
    lines.append(f'Pitch ⌀: sun {2 * res.sun.pitch_radius:.2f} mm, planet {2 * res.planet.pitch_radius:.2f} mm, '
                 f'ring {2 * res.ring.pitch_radius:.2f} mm')
    lines.append(f'Planet orbit radius {res.orbit_radius:.2f} mm')
    lines.append(f'Ratio {res.ratio:.3g} : 1 (ring fixed, sun drives, planet carrier output)')
    for rule in res.rules:
        mark = '<span style="color:#2a8a3a">✓</span>' if rule.ok else '<span style="color:#d03030">✖</span>'
        lines.append(f'{mark} {rule.label}: {rule.detail}')
    lines += res.infos
    lines += [f'<span style="color:#c08000">⚠ {w}</span>' for w in res.warnings]
    if not res.good:
        suggestions = gm.suggest_planetary(res.sun.module, res.sun.pressure_angle, res.sun.height_factor,
                                           res.sun.backlash, res.sun.teeth, res.planet.teeth, res.planets)
        if suggestions:
            text = ', '.join(f'sun {zs} / planet {zp} (ratio {1 + (zs + 2 * zp) / zs:.3g})' for zs, zp in suggestions)
            lines.append(f'Nearby sets that pass: {text}')
        else:
            lines.append('No passing set found within 10 teeth of these counts.')
    if _center(inputs) is None:
        lines.append('<span style="color:#d03030">✖ Select a center point.</span>')
    lines.append('Then make the gears in this order: sun → each planet (Mesh with: sun) → ring (Mesh with: any planet).')
    _set_text(inputs, INFO, '<br>'.join(lines))


def command_execute(args: adsk.core.CommandEventArgs):
    futil.log(f'{CMD_NAME} Command Execute Event')
    inputs = args.command.commandInputs
    (res, _), center = _evaluate(inputs), _center(inputs)
    if res is None or center is None or not res.ok:
        args.executeFailed = True
        args.executeFailedMessage = 'This planetary set doesn\'t pass its checks.'
        return
    layout.draw_planetary(center, res)
    settings.save(ci.remembered_values(inputs))


def command_preview(args: adsk.core.CommandEventArgs):
    inputs = args.command.commandInputs
    (res, _), center = _evaluate(inputs), _center(inputs)
    if res is not None and center is not None and res.ok:
        layout.draw_planetary(center, res)
    args.isValidResult = False


def command_input_changed(args: adsk.core.InputChangedEventArgs):
    ci.on_changed(args.inputs, args.input.id)
    _update(args.inputs)


def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    inputs = args.inputs
    res, _ = _evaluate(inputs)
    args.areInputsValid = res is not None and _center(inputs) is not None and res.ok


def command_destroy(args: adsk.core.CommandEventArgs):
    futil.log(f'{CMD_NAME} Command Destroy Event')
    global local_handlers
    local_handlers = []
    _last_text.clear()
