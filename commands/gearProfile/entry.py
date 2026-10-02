import math
import os
from typing import Optional

import adsk.core
import adsk.fusion

from ...lib import fusionAddInUtils as futil
from ... import config
from ... import gearmath as gm
from . import drawing
from . import settings

app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_gearProfile'
CMD_NAME = 'GearGremlin'
CMD_Description = 'Turn a sketch circle into an involute spur gear profile that meshes with its neighbours.'

EDIT_CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_gearEdit'
EDIT_CMD_NAME = 'Edit Gear'
EDIT_CMD_Description = "Change this gear's settings and redraw it."

IS_PROMOTED = False
WORKSPACE_ID = 'FusionSolidEnvironment'
PANEL_ID = 'SketchCreatePanel'

ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

local_handlers = []

# Input ids
PITCH = 'pitch_circle'
GEAR_TYPE = 'gear_type'
MODULE = 'module'
MODULE_CUSTOM = 'module_custom'
PRESSURE = 'pressure_angle'
TEETH = 'teeth'
RESIZE = 'resize'
MESH = 'mesh_with'
ROTATION = 'rotation'
BACKLASH = 'backlash'
REFS = 'reference_circles'
INFO = 'info'

CUSTOM = 'Custom'
TYPE_NAMES = {gm.EXTERNAL: 'External', gm.INTERNAL: 'Internal'}

# Circle diameters (mm) as they were when the command started, keyed by entityToken.
# Preview resizes circles, so reading them live while a preview is up could show the new size.
_original_diameters = {}

# Last HTML written to the info box. Compared against this rather than the box's
# formattedText, which Fusion may hand back reformatted.
_last_info = None

# The gear being edited, or None when creating a new gear.
_edit_circle: Optional[adsk.fusion.SketchCircle] = None

_menu_handler = None


def start():
    global _menu_handler
    cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER)
    futil.add_handler(cmd_def.commandCreated, command_created)
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    panel = workspace.toolbarPanels.itemById(PANEL_ID)
    control = panel.controls.addCommand(cmd_def)
    control.isPromoted = IS_PROMOTED

    # "Edit Gear" isn't on a toolbar; it's offered in the right-click menu of a gear.
    edit_def = ui.commandDefinitions.addButtonDefinition(EDIT_CMD_ID, EDIT_CMD_NAME, EDIT_CMD_Description,
                                                         ICON_FOLDER)
    futil.add_handler(edit_def.commandCreated, command_created)
    _menu_handler = futil.add_handler(ui.markingMenuDisplaying, marking_menu_displaying)


def stop():
    global _menu_handler
    if _menu_handler:
        ui.markingMenuDisplaying.remove(_menu_handler)
        _menu_handler = None
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    panel = workspace.toolbarPanels.itemById(PANEL_ID)
    command_control = panel.controls.itemById(CMD_ID)
    if command_control:
        command_control.deleteMe()
    for cmd_id in (CMD_ID, EDIT_CMD_ID):
        command_definition = ui.commandDefinitions.itemById(cmd_id)
        if command_definition:
            command_definition.deleteMe()


def marking_menu_displaying(args: adsk.core.MarkingMenuEventArgs):
    """Offer "Edit Gear" when the right-clicked entity is a gear's circle or one of its curves."""
    entities = args.selectedEntities
    is_gear = len(entities) == 1 and drawing.gear_for_entity(entities[0]) is not None
    controls = args.linearMarkingMenu.controls
    separator_id = EDIT_CMD_ID + '_separator'
    if not is_gear:
        # In case Fusion reuses the menu, don't leave the entry behind for other entities.
        for control_id in (EDIT_CMD_ID, separator_id):
            control = controls.itemById(control_id)
            if control:
                control.deleteMe()
        return
    if controls.itemById(EDIT_CMD_ID):
        return
    controls.addSeparator(separator_id)
    controls.addCommand(ui.commandDefinitions.itemById(EDIT_CMD_ID))


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

def _module_label(m: float) -> str:
    return f'{m:g} mm'


def _tip(cmd_input, text: str) -> None:
    cmd_input.tooltip = text


def command_created(args: adsk.core.CommandCreatedEventArgs):
    """Shared by GearGremlin and Edit Gear. A selected gear (circle or tooth) opens it for editing."""
    global _edit_circle
    futil.log(f'{CMD_NAME} Command Created Event')
    preselected = None
    _edit_circle = None
    record = None
    if ui.activeSelections.count == 1:
        entity = ui.activeSelections.item(0).entity
        _edit_circle = drawing.gear_for_entity(entity)
        if _edit_circle is not None:
            preselected = _edit_circle
            record = drawing.read_gear(_edit_circle)
        else:
            preselected = adsk.fusion.SketchCircle.cast(entity)

    _original_diameters.clear()
    _cache_diameters(preselected.parentSketch if preselected else
                     adsk.fusion.Sketch.cast(app.activeEditObject))

    remembered = settings.load()
    if record is not None:
        remembered.update(_record_values(record))
    inputs = args.command.commandInputs
    if record is not None:
        args.command.okButtonText = 'Update'

    pitch = inputs.addSelectionInput(PITCH, 'Pitch circle', 'Select the circle to turn into a gear')
    pitch.addSelectionFilter('SketchCircles')
    pitch.setSelectionLimits(1, 1)
    _tip(pitch, 'The circle becomes the gear\'s pitch circle, where it touches the gear it meshes with.')

    gear_type = inputs.addDropDownCommandInput(GEAR_TYPE, 'Gear type', adsk.core.DropDownStyles.TextListDropDownStyle)
    for key, name in TYPE_NAMES.items():
        gear_type.listItems.add(name, key == remembered['gear_type'])
    _tip(gear_type, 'External gears have teeth pointing out. Internal (ring) gears have teeth pointing in.')

    module = inputs.addDropDownCommandInput(MODULE, 'Module', adsk.core.DropDownStyles.TextListDropDownStyle)
    custom = remembered['module_custom'] or remembered['module_mm'] not in gm.STANDARD_MODULES_MM
    for m in gm.STANDARD_MODULES_MM:
        module.listItems.add(_module_label(m), not custom and m == remembered['module_mm'])
    module.listItems.add(CUSTOM, custom)
    _tip(module, 'Tooth size: pitch diameter ÷ tooth count. Gears must share a module to mesh.')

    module_custom = inputs.addValueInput(MODULE_CUSTOM, 'Custom module', 'mm',
                                         adsk.core.ValueInput.createByString(f'{remembered["module_mm"]} mm'))
    module_custom.isVisible = custom
    _tip(module_custom, 'Tooth size: pitch diameter ÷ tooth count. Gears must share a module to mesh.')

    pressure = inputs.addDropDownCommandInput(PRESSURE, 'Pressure angle', adsk.core.DropDownStyles.TextListDropDownStyle)
    for deg in gm.PRESSURE_ANGLES_DEG:
        pressure.listItems.add(f'{deg:g}°', deg == remembered['pressure_angle_deg'])
    _tip(pressure, 'Slope of the tooth faces. 20° is standard. Meshing gears must match.')

    teeth = inputs.addIntegerSpinnerCommandInput(TEETH, 'Teeth', gm.MIN_TEETH, gm.MAX_TEETH, 1,
                                                 remembered.get('teeth', 20))
    _tip(teeth, 'Suggested from the circle size. Change it to override.')

    resize = inputs.addBoolValueInput(RESIZE, 'Resize circle to pitch diameter', True, '',
                                      remembered.get('resize', True))
    _tip(resize, 'Sets the circle to exactly module × teeth, so tangent gears stay correctly spaced.')

    mesh = inputs.addSelectionInput(MESH, 'Mesh with', 'Select a gear made by GearGremlin to line the teeth up with')
    mesh.addSelectionFilter('SketchCircles')
    mesh.setSelectionLimits(0, 1)
    _tip(mesh, 'Optional. Pick an existing gear\'s circle and the teeth are rotated to mesh with it.')

    rotation = inputs.addAngleValueCommandInput(ROTATION, 'Rotation offset',
                                                adsk.core.ValueInput.createByReal(remembered.get('rotation', 0.0)))
    _tip(rotation, 'Extra rotation of the teeth, added after any automatic alignment.')

    backlash = inputs.addValueInput(BACKLASH, 'Backlash', 'mm',
                                    adsk.core.ValueInput.createByString(f'{remembered["backlash_mm"]} mm'))
    _tip(backlash, 'Play between meshing teeth, split between the two gears. 3D-printed gears usually need about 0.1–0.2 mm.')

    inputs.addBoolValueInput(REFS, 'Draw reference circles', True, '', remembered.get('reference_circles', False))
    _tip(inputs.itemById(REFS), 'Adds the tip and root circles as construction geometry.')

    inputs.addTextBoxCommandInput(INFO, '', '', 5, True)

    if record is not None:
        pitch.addSelection(_edit_circle)
        pitch.isEnabled = False
        partner = drawing.find_gear_circle(_edit_circle.parentSketch, record.mesh_with)
        if partner is not None:
            mesh.addSelection(partner)
    elif preselected and drawing.read_gear(preselected) is None:
        pitch.addSelection(preselected)
        mesh.hasFocus = True
        _suggest_teeth(inputs)

    cmd = args.command
    futil.add_handler(cmd.execute, command_execute, local_handlers=local_handlers)
    futil.add_handler(cmd.inputChanged, command_input_changed, local_handlers=local_handlers)
    futil.add_handler(cmd.executePreview, command_preview, local_handlers=local_handlers)
    futil.add_handler(cmd.validateInputs, command_validate_input, local_handlers=local_handlers)
    futil.add_handler(cmd.preSelect, command_pre_select, local_handlers=local_handlers)
    futil.add_handler(cmd.destroy, command_destroy, local_handlers=local_handlers)


def _record_values(record: gm.GearRecord) -> dict:
    """Dialog values for editing an existing gear, in the same keys as remembered settings."""
    params = record.params
    return {
        'module_mm': params.module,
        'module_custom': params.module not in gm.STANDARD_MODULES_MM,
        'pressure_angle_deg': round(math.degrees(params.pressure_angle), 6),
        'gear_type': params.gear_type,
        'backlash_mm': params.backlash,
        'teeth': params.teeth,
        'resize': record.resize,
        'rotation': record.rotation_offset,
        'reference_circles': record.reference_circles,
    }


def _cache_diameters(sketch: Optional[adsk.fusion.Sketch]) -> None:
    if not sketch:
        return
    circles = sketch.sketchCurves.sketchCircles
    for i in range(circles.count):
        c = circles.item(i)
        _original_diameters[c.entityToken] = 2 * drawing.circle_radius_mm(c)


def _original_diameter(circle: adsk.fusion.SketchCircle) -> float:
    token = circle.entityToken
    if token not in _original_diameters:
        _original_diameters[token] = 2 * drawing.circle_radius_mm(circle)
    return _original_diameters[token]


# ---------------------------------------------------------------------------
# Reading inputs
# ---------------------------------------------------------------------------

def _selected_circle(inputs, input_id: str) -> Optional[adsk.fusion.SketchCircle]:
    sel: adsk.core.SelectionCommandInput = inputs.itemById(input_id)
    if sel.selectionCount == 0:
        return None
    return adsk.fusion.SketchCircle.cast(sel.selection(0).entity)


def _module_mm(inputs) -> float:
    item = inputs.itemById(MODULE).selectedItem
    if item is None or item.name == CUSTOM:
        return drawing.to_mm(inputs.itemById(MODULE_CUSTOM).value)
    return float(item.name.split()[0])


def _gear_type(inputs) -> str:
    item = inputs.itemById(GEAR_TYPE).selectedItem
    return gm.INTERNAL if item and item.name == TYPE_NAMES[gm.INTERNAL] else gm.EXTERNAL


def _pressure_deg(inputs) -> float:
    item = inputs.itemById(PRESSURE).selectedItem
    return float(item.name.rstrip('°')) if item else 20.0


def _params(inputs) -> gm.GearParams:
    return gm.GearParams(
        module=_module_mm(inputs),
        teeth=inputs.itemById(TEETH).value,
        pressure_angle=math.radians(_pressure_deg(inputs)),
        backlash=drawing.to_mm(inputs.itemById(BACKLASH).value),
        gear_type=_gear_type(inputs),
    )


def _suggest_teeth(inputs) -> None:
    circle = _selected_circle(inputs, PITCH)
    module = _module_mm(inputs)
    if circle and module > 0:
        inputs.itemById(TEETH).value = gm.clamp_teeth(gm.suggest_teeth(_original_diameter(circle), module))


# ---------------------------------------------------------------------------
# Checks and info text
# ---------------------------------------------------------------------------

def _pre_check(inputs) -> gm.Check:
    """Checks that don't need the sketch modified: everything validateInputs can know."""
    check = gm.Check()
    circle = _selected_circle(inputs, PITCH)
    if circle is None:
        check.errors.append('Select a circle.')
        return check
    params = _params(inputs)
    check.extend(gm.check_params(params))
    if check.errors:
        return check
    if circle == _edit_circle:
        record = drawing.read_gear(circle)
        if record is None or not record.editable:
            check.errors.append("This gear was made by an older GearGremlin and can't be edited. "
                                'Delete it and make it again.')
            return check
        check.warnings.append('If you extruded this gear, reselect its profile in that feature after updating.')
    elif drawing.read_gear(circle) is not None:
        check.errors.append('This circle is already a gear. Pick a different circle.')
        return check
    if inputs.itemById(RESIZE).value:
        error = drawing.resize_check(circle)
        if error:
            check.errors.append(error)
        warning = drawing.dimension_expression_warning(circle)
        if warning:
            check.warnings.append(warning)
    partner = _selected_circle(inputs, MESH)
    if partner is not None:
        data = drawing.read_gear(partner)
        if data is None:
            check.errors.append('Mesh with: not a gear made by this tool.')
        elif partner == circle:
            check.errors.append('Mesh with must be a different circle.')
        elif partner.parentSketch != circle.parentSketch:
            check.errors.append('Mesh with must be in the same sketch.')
        else:
            mesh = gm.check_mesh(params, drawing.circle_center_mm(circle), data.params,
                                 drawing.circle_center_mm(partner), drawing.circle_radius_mm(partner))
            check.errors.extend(mesh.errors)  # positional warnings wait for the preview, after resizing
    return check


def _info_html(inputs, errors: list, warnings: list) -> str:
    lines = []
    circle = _selected_circle(inputs, PITCH)
    module = _module_mm(inputs)
    if circle is not None and module > 0:
        params = _params(inputs)
        d0 = _original_diameter(circle)
        pitch_d = 2 * params.pitch_radius
        lines.append(f'{d0:.2f} mm → {pitch_d:.2f} mm ({pitch_d - d0:+.2f} mm), {params.teeth} teeth')
        if params.teeth >= gm.MIN_TEETH:
            ra, rf, _ = gm.drawn_radii(params)
            lines.append(f'Tip ⌀ {2 * ra:.2f} mm, root ⌀ {2 * rf:.2f} mm')
    for e in errors:
        lines.append(f'<span style="color:#d03030">✖ {e}</span>')
    for w in dict.fromkeys(warnings):
        lines.append(f'<span style="color:#c08000">⚠ {w}</span>')
    return '<br>'.join(lines)


def _set_info(inputs, errors: list, warnings: list) -> None:
    global _last_info
    html = _info_html(inputs, errors, warnings)
    if html != _last_info:
        _last_info = html
        inputs.itemById(INFO).formattedText = html


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _run(inputs, finalize: bool) -> drawing.GearResult:
    check = _pre_check(inputs)
    if check.errors:
        result = drawing.GearResult(errors=check.errors, warnings=check.warnings)
    else:
        result = drawing.make_gear(
            _selected_circle(inputs, PITCH),
            _params(inputs),
            resize=inputs.itemById(RESIZE).value,
            partner=_selected_circle(inputs, MESH),
            rotation_offset=inputs.itemById(ROTATION).value,
            reference_circles=inputs.itemById(REFS).value,
            finalize=finalize,
            edit=_edit_circle is not None,
        )
        result.warnings = check.warnings + result.warnings
    _set_info(inputs, result.errors, result.warnings)
    return result


def command_execute(args: adsk.core.CommandEventArgs):
    futil.log(f'{CMD_NAME} Command Execute Event')
    inputs = args.command.commandInputs
    result = _run(inputs, finalize=True)
    if not result.ok:
        args.executeFailed = True
        args.executeFailedMessage = '\n'.join(result.errors)
        return
    if _edit_circle is not None:
        return  # editing one gear shouldn't change the defaults for new gears
    settings.save({
        'module_mm': _module_mm(inputs),
        'module_custom': inputs.itemById(MODULE).selectedItem.name == CUSTOM,
        'pressure_angle_deg': _pressure_deg(inputs),
        'gear_type': _gear_type(inputs),
        'backlash_mm': drawing.to_mm(inputs.itemById(BACKLASH).value),
    })


def command_preview(args: adsk.core.CommandEventArgs):
    _run(args.command.commandInputs, finalize=False)
    args.isValidResult = False


def command_input_changed(args: adsk.core.InputChangedEventArgs):
    changed = args.input
    inputs = args.inputs
    futil.log(f'{CMD_NAME} Input Changed Event fired from a change to {changed.id}')
    if changed.id == MODULE:
        inputs.itemById(MODULE_CUSTOM).isVisible = inputs.itemById(MODULE).selectedItem.name == CUSTOM
    # In edit mode the pitch circle is locked and the stored tooth count wins; only a module
    # change re-suggests.
    if changed.id in (MODULE, MODULE_CUSTOM) or (changed.id == PITCH and _edit_circle is None):
        _suggest_teeth(inputs)
    if changed.id == PITCH and _selected_circle(inputs, PITCH) is not None:
        inputs.itemById(MESH).hasFocus = True


def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    check = _pre_check(args.inputs)
    args.areInputsValid = check.ok
    if not check.ok:
        _set_info(args.inputs, check.errors, check.warnings)


def command_pre_select(args: adsk.core.SelectionEventArgs):
    """Pitch circle accepts only circles that aren't gears yet; Mesh with accepts only gears."""
    if args.activeInput is None or args.activeInput.id not in (PITCH, MESH):
        return
    circle = adsk.fusion.SketchCircle.cast(args.selection.entity)
    is_gear = circle is not None and drawing.read_gear(circle) is not None
    if args.activeInput.id == MESH:
        args.isSelectable = is_gear and circle != _edit_circle
    else:
        args.isSelectable = not is_gear


def command_destroy(args: adsk.core.CommandEventArgs):
    futil.log(f'{CMD_NAME} Command Destroy Event')
    global local_handlers, _last_info, _edit_circle
    local_handlers = []
    _last_info = None
    _edit_circle = None
    _original_diameters.clear()
