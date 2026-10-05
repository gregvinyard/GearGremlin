import math
import os
from typing import Optional

import adsk.core
import adsk.fusion

from ...lib import fusionAddInUtils as futil
from ... import config
from ... import gearmath as gm
from ..common import inputs as ci
from . import drawing
from . import rack_drawing
from . import settings

app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_gearProfile'
CMD_NAME = 'GearGremlin'
CMD_Description = ('Turn a sketch circle into an involute spur gear profile that meshes with its neighbours, '
                   'or a sketch line into a rack.')

EDIT_CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_gearEdit'
EDIT_CMD_NAME = 'Edit Gear'
EDIT_CMD_Description = "Change this gear's (or rack's) settings and redraw it."

IS_PROMOTED = False
WORKSPACE_ID = 'FusionSolidEnvironment'
PANEL_ID = 'SketchCreatePanel'

ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

local_handlers = []

# Input ids (module, pressure angle, tooth height and backlash ids live in common.inputs)
PITCH = 'pitch_circle'
GEAR_TYPE = 'gear_type'
TEETH = 'teeth'
RESIZE = 'resize'
MESH = 'mesh_with'
ROTATION = 'rotation'
REFS = 'reference_circles'
INFO = 'info'
# Rack inputs, shown when the pitch selection is a line
RESIZE_LINE = 'resize_line'
FLIP = 'flip_side'
OFFSET = 'rack_offset'
BODY = 'rack_body'

GEAR_ONLY = (GEAR_TYPE, TEETH, RESIZE, MESH, ROTATION, REFS)
RACK_ONLY = (RESIZE_LINE, FLIP, OFFSET, BODY)

TYPE_NAMES = {gm.EXTERNAL: 'External', gm.INTERNAL: 'Internal'}

# Circle diameters (mm) as they were when the command started, keyed by entityToken.
# Preview resizes circles, so reading them live while a preview is up could show the new size.
_original_diameters = {}
# Line lengths (mm) likewise, for racks.
_original_lengths = {}

# Last HTML written to each text box. Compared against this rather than the box's
# formattedText, which Fusion may hand back reformatted.
_last_text = {}

# The gear (or rack) being edited, or None when creating one.
_edit_circle: Optional[adsk.fusion.SketchCircle] = None
_edit_line: Optional[adsk.fusion.SketchLine] = None

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
    """Offer "Edit Gear" when the right-clicked entity is a gear's circle, a rack's line, or one of their curves."""
    entities = args.selectedEntities
    is_gear = len(entities) == 1 and rack_drawing.owner_for_entity(entities[0]) is not None
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

def _tip(cmd_input, text: str) -> None:
    cmd_input.tooltip = text


def command_created(args: adsk.core.CommandCreatedEventArgs):
    """Shared by GearGremlin and Edit Gear. A selected gear or rack (its pitch curve or any of its
    curves) opens it for editing."""
    global _edit_circle, _edit_line
    futil.log(f'{CMD_NAME} Command Created Event')
    preselected = None
    _edit_circle = None
    _edit_line = None
    record = None
    rack_record = None
    if ui.activeSelections.count == 1:
        entity = ui.activeSelections.item(0).entity
        owner = rack_drawing.owner_for_entity(entity)
        if owner is not None and owner[0] == 'gear':
            _edit_circle = preselected = owner[1]
            record = drawing.read_gear(_edit_circle)
        elif owner is not None:
            _edit_line = preselected = owner[1]
            rack_record = rack_drawing.read_rack(_edit_line)
        else:
            preselected = adsk.fusion.SketchCircle.cast(entity) or adsk.fusion.SketchLine.cast(entity)

    _original_diameters.clear()
    _original_lengths.clear()
    _last_text.clear()
    _cache_diameters(preselected.parentSketch if preselected else
                     adsk.fusion.Sketch.cast(app.activeEditObject))
    if adsk.fusion.SketchLine.cast(preselected):
        _original_length(preselected)

    remembered = settings.load()
    if record is not None:
        remembered.update(_record_values(record))
    if rack_record is not None:
        remembered.update(_rack_record_values(rack_record))
    inputs = args.command.commandInputs
    if record is not None or rack_record is not None:
        args.command.okButtonText = 'Update'

    pitch = inputs.addSelectionInput(PITCH, 'Pitch circle or line', 'Select a circle (gear) or a line (rack)')
    pitch.addSelectionFilter('SketchCircles')
    pitch.addSelectionFilter('SketchLines')
    pitch.setSelectionLimits(1, 1)
    _tip(pitch, 'A circle becomes a gear\'s pitch circle; a line becomes a rack\'s pitch line, where it '
                'touches the gear it meshes with.')

    gear_type = inputs.addDropDownCommandInput(GEAR_TYPE, 'Gear type', adsk.core.DropDownStyles.TextListDropDownStyle)
    for key, name in TYPE_NAMES.items():
        gear_type.listItems.add(name, key == remembered['gear_type'])
    _tip(gear_type, 'External gears have teeth pointing out. Internal (ring) gears have teeth pointing in.')

    ci.add_module(inputs, remembered)
    ci.add_pressure(inputs, remembered)
    ci.add_height(inputs, remembered)

    teeth = inputs.addIntegerSpinnerCommandInput(TEETH, 'Teeth', gm.MIN_TEETH, gm.MAX_TEETH, 1,
                                                 remembered.get('teeth', 20))
    _tip(teeth, 'Suggested from the circle size. Change it to override.')

    resize = inputs.addBoolValueInput(RESIZE, 'Resize circle to pitch diameter', True, '',
                                      remembered.get('resize', True))
    _tip(resize, 'Sets the circle to exactly module × teeth, so tangent gears stay correctly spaced.')

    resize_line = inputs.addBoolValueInput(RESIZE_LINE, 'Resize line to whole teeth', True, '',
                                           remembered.get('resize', True))
    _tip(resize_line, 'Lengthens or shortens the line from its end so it holds a whole number of teeth.')
    flip = inputs.addBoolValueInput(FLIP, 'Flip side', True, '', remembered.get('rack_flip', False))
    _tip(flip, 'Puts the teeth on the other side of the line.')
    offset = inputs.addValueInput(OFFSET, 'Offset along line', 'mm',
                                  adsk.core.ValueInput.createByReal(remembered.get('rack_offset_mm', 0.0) / 10.0))
    _tip(offset, 'Slides the teeth along the line.')
    body_mm = remembered['rack_body_mm']
    if body_mm < 0:
        body_mm = 3.0 * remembered['module_mm']
    body = inputs.addValueInput(BODY, 'Backing thickness', 'mm', adsk.core.ValueInput.createByReal(body_mm / 10.0))
    _tip(body, 'Solid material below the tooth roots; 0 draws only the toothed edge.')

    mesh = inputs.addSelectionInput(MESH, 'Mesh with', 'Select a gear made by GearGremlin to line the teeth up with')
    mesh.addSelectionFilter('SketchCircles')
    mesh.setSelectionLimits(0, 1)
    _tip(mesh, 'Optional. Pick an existing gear\'s circle and the teeth are rotated to mesh with it.')

    rotation = inputs.addAngleValueCommandInput(ROTATION, 'Rotation offset',
                                                adsk.core.ValueInput.createByReal(remembered.get('rotation', 0.0)))
    _tip(rotation, 'Extra rotation of the teeth, added after any automatic alignment.')

    ci.add_backlash(inputs, remembered)

    inputs.addBoolValueInput(REFS, 'Draw reference circles', True, '', remembered.get('reference_circles', False))
    _tip(inputs.itemById(REFS), 'Adds the tip and root circles (as drawn) as construction geometry.')

    inputs.addTextBoxCommandInput(INFO, '', '', 6, True)

    if record is not None:
        pitch.addSelection(_edit_circle)
        pitch.isEnabled = False
        partner = drawing.find_gear_circle(_edit_circle.parentSketch, record.mesh_with)
        if partner is not None:
            mesh.addSelection(partner)
    elif rack_record is not None:
        pitch.addSelection(_edit_line)
        pitch.isEnabled = False
    elif adsk.fusion.SketchCircle.cast(preselected) and drawing.read_gear(preselected) is None:
        pitch.addSelection(preselected)
        if not _apply_plan(inputs, preselected):
            _suggest_teeth(inputs)
        mesh.hasFocus = True
    elif adsk.fusion.SketchLine.cast(preselected) and rack_drawing.owner_for_entity(preselected) is None:
        pitch.addSelection(preselected)
    _show_inputs_for(inputs)
    _sync_height(inputs)

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
        'height_factor': params.height_factor,
        'height_custom': params.height_factor not in ci.HEIGHT_LABELS.values(),
        'gear_type': params.gear_type,
        'backlash_mm': params.backlash,
        'teeth': params.teeth,
        'resize': record.resize,
        'rotation': record.rotation_offset,
        'reference_circles': record.reference_circles,
    }


def _rack_record_values(record: gm.RackRecord) -> dict:
    """Dialog values for editing an existing rack, in the same keys as remembered settings."""
    params = record.params
    return {
        'module_mm': params.module,
        'module_custom': params.module not in gm.STANDARD_MODULES_MM,
        'pressure_angle_deg': round(math.degrees(params.pressure_angle), 6),
        'height_factor': params.height_factor,
        'height_custom': params.height_factor not in ci.HEIGHT_LABELS.values(),
        'backlash_mm': params.backlash,
        'resize': record.resize,
        'rack_flip': record.side == gm.RIGHT,
        'rack_offset_mm': record.offset,
        'rack_body_mm': record.body,
    }


def _apply_plan(inputs, circle: adsk.fusion.SketchCircle) -> bool:
    """Preset type, teeth, module, pressure angle, height and Mesh with from a planetary plan."""
    plan = drawing.read_plan(circle)
    if plan is None:
        return False
    dropdown = inputs.itemById(GEAR_TYPE)
    for i in range(dropdown.listItems.count):
        item = dropdown.listItems.item(i)
        item.isSelected = item.name == TYPE_NAMES[plan.gear_type]
    ci.set_module(inputs, plan.module)
    ci.set_pressure(inputs, math.degrees(plan.pressure_angle))
    ci.set_height(inputs, plan.height_factor)
    inputs.itemById(TEETH).value = plan.teeth
    partner = _plan_partner(circle, plan)
    mesh = inputs.itemById(MESH)
    mesh.clearSelection()
    if partner is not None:
        mesh.addSelection(partner)
    return True


def _plan_partner(circle: adsk.fusion.SketchCircle, plan: gm.PlanRecord) -> Optional[adsk.fusion.SketchCircle]:
    """The Mesh with partner that keeps a planetary set aligned (see SPEC: "Plan attribute")."""
    made = {'sun': [], 'planet': [], 'ring': []}
    for c in drawing.gear_circles(circle.parentSketch):
        other = drawing.read_plan(c)
        if other is not None and other.set_id == plan.set_id and c != circle:
            made[other.role].append(c)
    order = {'planet': ('sun', 'planet', 'ring'), 'ring': ('planet',), 'sun': ('planet',)}[plan.role]
    for role in order:
        if made[role]:
            return made[role][0]
    return None


def _cache_diameters(sketch: Optional[adsk.fusion.Sketch]) -> None:
    if not sketch:
        return
    circles = sketch.sketchCurves.sketchCircles
    for i in range(circles.count):
        c = circles.item(i)
        _original_diameters[c.entityToken] = 2 * drawing.circle_radius_mm(c)
    # Lines aren't all measured here: a sketch holding a 400-tooth rack has ~1,600 lines, which
    # takes over a second. The preselected line is measured at start; others when first selected,
    # which happens with no preview up (see _original_length).


def _original_diameter(circle: adsk.fusion.SketchCircle) -> float:
    token = circle.entityToken
    if token not in _original_diameters:
        _original_diameters[token] = 2 * drawing.circle_radius_mm(circle)
    return _original_diameters[token]


def _original_length(line: adsk.fusion.SketchLine) -> float:
    """The line's length before any preview resized it (see _original_diameters). First measured at
    command start for a preselected line, else on the selection change that picked it."""
    token = line.entityToken
    if token not in _original_lengths:
        _original_lengths[token] = rack_drawing.line_frame(line)[2]
    return _original_lengths[token]


# ---------------------------------------------------------------------------
# Reading inputs
# ---------------------------------------------------------------------------

def _selected_circle(inputs, input_id: str) -> Optional[adsk.fusion.SketchCircle]:
    sel: adsk.core.SelectionCommandInput = inputs.itemById(input_id)
    if sel.selectionCount == 0:
        return None
    return adsk.fusion.SketchCircle.cast(sel.selection(0).entity)


def _selected_line(inputs) -> Optional[adsk.fusion.SketchLine]:
    """The pitch selection when it's a line (a rack), else None."""
    sel: adsk.core.SelectionCommandInput = inputs.itemById(PITCH)
    if sel.selectionCount == 0:
        return None
    return adsk.fusion.SketchLine.cast(sel.selection(0).entity)


def _show_inputs_for(inputs) -> None:
    """Show the rack inputs when the pitch selection is a line, the gear inputs otherwise."""
    rack = _selected_line(inputs) is not None
    for input_id in GEAR_ONLY:
        inputs.itemById(input_id).isVisible = not rack
    for input_id in RACK_ONLY:
        inputs.itemById(input_id).isVisible = rack


def _rack_params(inputs) -> gm.RackParams:
    return gm.RackParams(
        module=ci.module_mm(inputs),
        pressure_angle=ci.pressure_rad(inputs),
        backlash=ci.backlash_mm(inputs),
        height_factor=ci.height_factor(inputs),
    )


def _rack_side(inputs) -> int:
    return gm.RIGHT if inputs.itemById(FLIP).value else gm.LEFT


def _rack_offset_mm(inputs) -> float:
    return inputs.itemById(OFFSET).value * 10.0   # cm → mm


def _rack_body_mm(inputs) -> float:
    return inputs.itemById(BODY).value * 10.0     # cm → mm


def _gear_type(inputs) -> str:
    item = inputs.itemById(GEAR_TYPE).selectedItem
    return gm.INTERNAL if item and item.name == TYPE_NAMES[gm.INTERNAL] else gm.EXTERNAL


def _params(inputs) -> gm.GearParams:
    return gm.GearParams(
        module=ci.module_mm(inputs),
        teeth=inputs.itemById(TEETH).value,
        pressure_angle=ci.pressure_rad(inputs),
        backlash=ci.backlash_mm(inputs),
        gear_type=_gear_type(inputs),
        height_factor=ci.height_factor(inputs),
    )


def _partner_record(inputs) -> Optional[gm.GearRecord]:
    """The Mesh with partner's record. None for a rack, which has no Mesh with (yet)."""
    if _selected_line(inputs) is not None:
        return None
    partner = _selected_circle(inputs, MESH)
    return drawing.read_gear(partner) if partner is not None else None


def _suggest_teeth(inputs) -> None:
    circle = _selected_circle(inputs, PITCH)
    module = ci.module_mm(inputs)
    if circle and module > 0:
        inputs.itemById(TEETH).value = gm.clamp_teeth(gm.suggest_teeth(_original_diameter(circle), module))


def _sync_height(inputs) -> None:
    """Lock the tooth height to the Mesh with partner's, or unlock it, and refresh the note."""
    record = _partner_record(inputs)
    if record is not None:
        ci.set_height(inputs, record.params.height_factor)
    ci.lock_height(inputs, record is not None)
    _update_height_note(inputs)


def _update_height_note(inputs) -> None:
    if _selected_line(inputs) is not None:
        try:
            note = ci.rack_height_note(_rack_params(inputs))
        except Exception:
            note = ''
        _set_text(inputs, ci.HEIGHT_NOTE, f'<span style="color:#707070">{note}</span>' if note else '')
        return
    try:
        params = _params(inputs)
    except Exception:
        params = None
    record = _partner_record(inputs)
    note = ''
    if params is not None and params.module > 0 and params.teeth >= gm.MIN_TEETH:
        partner = record.params if record is not None else None
        if partner is not None and (partner.internal and params.internal):
            partner = None
        note = ci.height_note(params, partner)
    _set_text(inputs, ci.HEIGHT_NOTE, f'<span style="color:#707070">{note}</span>' if note else '')


# ---------------------------------------------------------------------------
# Checks and info text
# ---------------------------------------------------------------------------

def _pre_check(inputs) -> gm.Check:
    """Checks that don't need the sketch modified: everything validateInputs can know."""
    check = gm.Check()
    if _selected_line(inputs) is not None:
        return _pre_check_rack(inputs)
    circle = _selected_circle(inputs, PITCH)
    if circle is None:
        check.errors.append('Select a circle or a line.')
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
        check.infos.append('Extrudes and revolves made from this gear are re-pointed to the new profile on Update.')
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


def _pre_check_rack(inputs) -> gm.Check:
    """_pre_check for a line: the rack checks that don't need the sketch modified."""
    check = gm.Check()
    line = _selected_line(inputs)
    error = rack_drawing.in_plane_error(line)
    if error:
        check.errors.append(error)
        return check
    resize = inputs.itemById(RESIZE_LINE).value
    check.extend(gm.check_rack(_rack_params(inputs), _original_length(line), resize,
                               _rack_offset_mm(inputs), _rack_body_mm(inputs)))
    if check.errors:
        return check
    if line == _edit_line:
        if rack_drawing.read_rack(line) is None:
            check.errors.append("This line isn't a rack made by this tool.")
            return check
        check.infos.append('Extrudes and revolves made from this rack are re-pointed to the new profile on Update.')
    elif rack_drawing.read_rack(line) is not None:
        check.errors.append('This line is already a rack. Pick a different line.')
        return check
    elif rack_drawing.owner_for_entity(line) is not None:
        check.errors.append('That line is part of a gear made by this tool.')
        return check
    if resize:
        error = rack_drawing.line_resize_check(line)
        if error:
            check.errors.append(error)
        warning = rack_drawing.length_expression_warning(line)
        if warning:
            check.warnings.append(warning)
    return check


def _rack_summary(inputs, rack: Optional[gm.RackProfile]) -> str:
    """'Line 48.00 mm → 50.27 mm (+2.27 mm), 8 teeth, pitch 6.28 mm', from the drawn rack if there is one."""
    line = _selected_line(inputs)
    params = _rack_params(inputs)
    if params.module <= 0:
        return ''
    l0 = _original_length(line)
    if rack is not None:
        length, teeth = rack.length, len(rack.tooth_centers)
    else:
        resize = inputs.itemById(RESIZE_LINE).value
        length = gm.rack_teeth_for_length(l0, params) * params.pitch if resize else l0
        teeth = len(gm.rack_tooth_centers(length, params, _rack_offset_mm(inputs)))
    change = f'{l0:.2f} mm → {length:.2f} mm ({length - l0:+.2f} mm)' if abs(length - l0) > 5e-3 else f'{l0:.2f} mm'
    return f'Line {change}, {teeth} teeth, pitch {params.pitch:.2f} mm'


def _info_html(inputs, errors: list, warnings: list, infos: list, radii: Optional[gm.Radii],
               rack: Optional[gm.RackProfile] = None) -> str:
    lines = []
    circle = _selected_circle(inputs, PITCH)
    if _selected_line(inputs) is not None:
        summary = _rack_summary(inputs, rack)
        if summary:
            lines.append(summary)
    module = ci.module_mm(inputs)
    if circle is not None and module > 0:
        params = _params(inputs)
        d0 = _original_diameter(circle)
        pitch_d = 2 * params.pitch_radius
        lines.append(f'{d0:.2f} mm → {pitch_d:.2f} mm ({pitch_d - d0:+.2f} mm), {params.teeth} teeth')
        if params.teeth >= gm.MIN_TEETH:
            r = radii if radii is not None else gm.gear_radii(params)
            lines.append(f'Tip ⌀ {2 * r.tip:.2f} mm, root ⌀ {2 * r.root:.2f} mm')
    lines += list(dict.fromkeys(infos))
    for e in errors:
        lines.append(f'<span style="color:#d03030">✖ {e}</span>')
    for w in dict.fromkeys(warnings):
        lines.append(f'<span style="color:#c08000">⚠ {w}</span>')
    return '<br>'.join(lines)


def _set_text(inputs, input_id: str, html: str) -> None:
    if _last_text.get(input_id) != html:
        _last_text[input_id] = html
        inputs.itemById(input_id).formattedText = html


def _set_info(inputs, errors: list, warnings: list, infos: list = (), radii: Optional[gm.Radii] = None,
              rack: Optional[gm.RackProfile] = None) -> None:
    _set_text(inputs, INFO, _info_html(inputs, errors, warnings, list(infos), radii, rack))


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _run(inputs, finalize: bool) -> drawing.GearResult:
    check = _pre_check(inputs)
    if check.errors:
        result = drawing.GearResult(errors=check.errors, warnings=check.warnings, infos=check.infos)
    elif _selected_line(inputs) is not None:
        result = rack_drawing.make_rack(
            _selected_line(inputs),
            _rack_params(inputs),
            side=_rack_side(inputs),
            offset=_rack_offset_mm(inputs),
            body=_rack_body_mm(inputs),
            resize=inputs.itemById(RESIZE_LINE).value,
            finalize=finalize,
            edit=_edit_line is not None,
        )
        # make_rack repeats check_rack's warnings; keep one copy of each.
        result.warnings = list(dict.fromkeys(check.warnings + result.warnings))
        result.infos = check.infos + result.infos
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
        result.infos = check.infos + result.infos
    radii = result.profile.radii if result.profile is not None else None
    _set_info(inputs, result.errors, result.warnings, result.infos, radii, result.rack)
    return result


def command_execute(args: adsk.core.CommandEventArgs):
    futil.log(f'{CMD_NAME} Command Execute Event')
    inputs = args.command.commandInputs
    result = _run(inputs, finalize=True)
    if not result.ok:
        args.executeFailed = True
        args.executeFailedMessage = '\n'.join(result.errors)
        return
    if _edit_circle is not None or _edit_line is not None:
        what = 'gear' if _edit_circle is not None else 'rack'
        if drawing.repoint_restored:
            futil.log_to_file('Settings restored after re-pointing: ' + '; '.join(drawing.repoint_restored))
        if drawing.repoint_errors:
            futil.log_to_file(f'Re-pointing features after editing a {what}: ' + '; '.join(drawing.repoint_errors))
        if result.not_repointed:
            ui.messageBox(f'The {what} was updated, but these features couldn\'t be re-pointed to its new profile: '
                          f'{", ".join(result.not_repointed)}.\n\nEdit each one and reselect its profile.',
                          CMD_NAME)
        return  # editing one gear shouldn't change the defaults for new gears
    values = ci.remembered_values(inputs)
    if _selected_line(inputs) is not None:
        values['rack_body_mm'] = _rack_body_mm(inputs)
    else:
        values['gear_type'] = _gear_type(inputs)
    settings.save(values)


def command_preview(args: adsk.core.CommandEventArgs):
    _run(args.command.commandInputs, finalize=False)
    args.isValidResult = False


def command_input_changed(args: adsk.core.InputChangedEventArgs):
    changed = args.input
    inputs = args.inputs
    futil.log(f'{CMD_NAME} Input Changed Event fired from a change to {changed.id}')
    ci.on_changed(inputs, changed.id)
    if changed.id == PITCH:
        line = _selected_line(inputs)
        if line is not None:
            _original_length(line)
        _show_inputs_for(inputs)
    planned = False
    if changed.id == PITCH and _edit_circle is None:
        circle = _selected_circle(inputs, PITCH)
        planned = circle is not None and _apply_plan(inputs, circle)
    # In edit mode the pitch circle is locked and the stored tooth count wins; only a module
    # change re-suggests. A planned circle's tooth count comes from its plan.
    if changed.id in (ci.MODULE, ci.MODULE_CUSTOM) or (changed.id == PITCH and _edit_circle is None and not planned):
        _suggest_teeth(inputs)
    if changed.id == PITCH and _selected_circle(inputs, PITCH) is not None:
        inputs.itemById(MESH).hasFocus = True
    if changed.id in (MESH, PITCH):
        _sync_height(inputs)
    elif changed.id in (ci.MODULE, ci.MODULE_CUSTOM, ci.PRESSURE, ci.HEIGHT, ci.HEIGHT_CUSTOM, TEETH,
                        GEAR_TYPE, ci.BACKLASH):
        _update_height_note(inputs)


def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    check = _pre_check(args.inputs)
    args.areInputsValid = check.ok
    if not check.ok:
        _set_info(args.inputs, check.errors, check.warnings)


def command_pre_select(args: adsk.core.SelectionEventArgs):
    """Pitch accepts circles that aren't gears yet and lines that aren't racks or part of a gear or
    rack outline; Mesh with accepts only gears."""
    if args.activeInput is None or args.activeInput.id not in (PITCH, MESH):
        return
    line = adsk.fusion.SketchLine.cast(args.selection.entity)
    if line is not None:
        # Cheap checks first: preSelect fires on every hover.
        if args.activeInput.id == MESH:
            args.isSelectable = False
        elif line.attributes.itemByName(drawing.ATTR_GROUP, drawing.PART_NAME) is not None:
            args.isSelectable = False
        elif rack_drawing.read_rack(line) is not None:
            args.isSelectable = line == _edit_line
        else:
            args.isSelectable = rack_drawing.rack_for_entity(line) is None
        return
    circle = adsk.fusion.SketchCircle.cast(args.selection.entity)
    is_gear = circle is not None and drawing.read_gear(circle) is not None
    if args.activeInput.id == MESH:
        args.isSelectable = is_gear and circle != _edit_circle
    else:
        args.isSelectable = not is_gear


def command_destroy(args: adsk.core.CommandEventArgs):
    futil.log(f'{CMD_NAME} Command Destroy Event')
    global local_handlers, _edit_circle, _edit_line
    local_handlers = []
    _last_text.clear()
    _edit_circle = None
    _edit_line = None
    _original_diameters.clear()
    _original_lengths.clear()
