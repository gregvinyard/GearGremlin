"""Dialog inputs shared by the gear command and the planetary helper: module, pressure angle,
tooth height factor, and backlash. Both commands read and write the same remembered settings."""
import math
from typing import Optional

import adsk.core

from ... import gearmath as gm

MODULE = 'module'
MODULE_CUSTOM = 'module_custom'
PRESSURE = 'pressure_angle'
HEIGHT = 'tooth_height'
HEIGHT_CUSTOM = 'tooth_height_custom'
HEIGHT_NOTE = 'tooth_height_note'
BACKLASH = 'backlash'

CUSTOM = 'Custom'
HEIGHT_LABELS = {f'{name} ({k:g})': k for name, k in gm.HEIGHT_FACTORS.items()}


def _tip(cmd_input, text: str) -> None:
    cmd_input.tooltip = text


def add_module(inputs: adsk.core.CommandInputs, remembered: dict) -> None:
    module = inputs.addDropDownCommandInput(MODULE, 'Module', adsk.core.DropDownStyles.TextListDropDownStyle)
    custom = remembered['module_custom'] or remembered['module_mm'] not in gm.STANDARD_MODULES_MM
    for m in gm.STANDARD_MODULES_MM:
        module.listItems.add(f'{m:g} mm', not custom and m == remembered['module_mm'])
    module.listItems.add(CUSTOM, custom)
    _tip(module, 'Tooth size: pitch diameter ÷ tooth count. Gears must share a module to mesh.')
    value = inputs.addValueInput(MODULE_CUSTOM, 'Custom module', 'mm',
                                 adsk.core.ValueInput.createByString(f'{remembered["module_mm"]} mm'))
    value.isVisible = custom
    _tip(value, 'Tooth size: pitch diameter ÷ tooth count. Gears must share a module to mesh.')


def add_pressure(inputs: adsk.core.CommandInputs, remembered: dict) -> None:
    pressure = inputs.addDropDownCommandInput(PRESSURE, 'Pressure angle', adsk.core.DropDownStyles.TextListDropDownStyle)
    for deg in gm.PRESSURE_ANGLES_DEG:
        pressure.listItems.add(f'{deg:g}°', deg == remembered['pressure_angle_deg'])
    _tip(pressure, 'Slope of the tooth faces. 20° is standard. Meshing gears must match.')


def add_height(inputs: adsk.core.CommandInputs, remembered: dict) -> None:
    k = remembered['height_factor']
    custom = remembered['height_custom'] or k not in HEIGHT_LABELS.values()
    height = inputs.addDropDownCommandInput(HEIGHT, 'Tooth height', adsk.core.DropDownStyles.TextListDropDownStyle)
    for label, value in HEIGHT_LABELS.items():
        height.listItems.add(label, not custom and value == k)
    height.listItems.add(CUSTOM, custom)
    _tip(height, 'How long the teeth are, relative to the module; Stub teeth are shorter and stronger but '
                 'overlap less.')
    value = inputs.addValueInput(HEIGHT_CUSTOM, 'Custom height', '', adsk.core.ValueInput.createByReal(k))
    value.isVisible = custom
    _tip(value, 'Tooth height factor: addendum = factor × module, dedendum = (factor + 0.25) × module.')
    inputs.addTextBoxCommandInput(HEIGHT_NOTE, '', '', 2, True)


def add_backlash(inputs: adsk.core.CommandInputs, remembered: dict) -> None:
    backlash = inputs.addValueInput(BACKLASH, 'Backlash', 'mm',
                                    adsk.core.ValueInput.createByString(f'{remembered["backlash_mm"]} mm'))
    _tip(backlash, 'Play between meshing teeth, split between the two gears. 3D-printed gears usually need '
                   'about 0.1–0.2 mm.')


def module_mm(inputs) -> float:
    item = inputs.itemById(MODULE).selectedItem
    if item is None or item.name == CUSTOM:
        return inputs.itemById(MODULE_CUSTOM).value * 10.0   # cm → mm
    return float(item.name.split()[0])


def module_is_custom(inputs) -> bool:
    item = inputs.itemById(MODULE).selectedItem
    return item is not None and item.name == CUSTOM


def pressure_deg(inputs) -> float:
    item = inputs.itemById(PRESSURE).selectedItem
    return float(item.name.rstrip('°')) if item else 20.0


def pressure_rad(inputs) -> float:
    return math.radians(pressure_deg(inputs))


def height_factor(inputs) -> float:
    item = inputs.itemById(HEIGHT).selectedItem
    if item is None or item.name == CUSTOM:
        return round(inputs.itemById(HEIGHT_CUSTOM).value, 6)
    return HEIGHT_LABELS[item.name]


def height_is_custom(inputs) -> bool:
    item = inputs.itemById(HEIGHT).selectedItem
    return item is not None and item.name == CUSTOM


def backlash_mm(inputs) -> float:
    return inputs.itemById(BACKLASH).value * 10.0   # cm → mm


def set_height(inputs, k: float) -> None:
    """Select the preset matching k, or Custom with k."""
    dropdown = inputs.itemById(HEIGHT)
    label = next((lbl for lbl, v in HEIGHT_LABELS.items() if abs(v - k) < 1e-9), CUSTOM)
    for i in range(dropdown.listItems.count):
        item = dropdown.listItems.item(i)
        item.isSelected = item.name == label
    if label == CUSTOM:
        inputs.itemById(HEIGHT_CUSTOM).value = k
    inputs.itemById(HEIGHT_CUSTOM).isVisible = label == CUSTOM


def set_module(inputs, m: float) -> None:
    dropdown = inputs.itemById(MODULE)
    label = f'{m:g} mm' if m in gm.STANDARD_MODULES_MM else CUSTOM
    for i in range(dropdown.listItems.count):
        item = dropdown.listItems.item(i)
        item.isSelected = item.name == label
    if label == CUSTOM:
        inputs.itemById(MODULE_CUSTOM).value = m / 10.0
    inputs.itemById(MODULE_CUSTOM).isVisible = label == CUSTOM


def set_pressure(inputs, deg: float) -> None:
    dropdown = inputs.itemById(PRESSURE)
    for i in range(dropdown.listItems.count):
        item = dropdown.listItems.item(i)
        item.isSelected = abs(float(item.name.rstrip('°')) - deg) < 1e-6


def lock_height(inputs, locked: bool) -> None:
    inputs.itemById(HEIGHT).isEnabled = not locked
    inputs.itemById(HEIGHT_CUSTOM).isEnabled = not locked


def on_changed(inputs, changed_id: str) -> None:
    """Show or hide the Custom value inputs."""
    if changed_id == MODULE:
        inputs.itemById(MODULE_CUSTOM).isVisible = module_is_custom(inputs)
    if changed_id == HEIGHT:
        inputs.itemById(HEIGHT_CUSTOM).isVisible = height_is_custom(inputs)


def height_note(params: Optional[gm.GearParams], partner: Optional[gm.GearParams] = None,
                partners_for_range: Optional[list] = None) -> str:
    """The note under the tooth height input: valid range, and the lock when a partner sets it."""
    if params is None or params.module <= 0 or params.teeth < gm.MIN_TEETH:
        return ''
    if partner is not None:
        lo, hi = gm.factor_range(params, partner)
        k = partner.height_factor
        text = f"Locked to the partner's factor ({k:g}) because meshing gears must match. "
        if lo is None:
            return text + 'No tooth height reaches a contact ratio of 1.2 for this pair.'
        cr = gm.pair_contact_ratio(params.with_factor(k), partner)
        return text + f'Valid for this pair: {lo:.2f}–{hi:.2f}. Contact ratio at {k:g}: {cr:.2f}.'
    lo, hi = gm.factor_range(params)
    for other in partners_for_range or []:
        hi = min(hi, gm.factor_max(other.with_factor(1.0)))
    return f"Valid: {lo:.2f}–{hi:.2f}. Minimum can't be fully checked without a Mesh with partner."


def remembered_values(inputs) -> dict:
    """The shared settings as they stand in the dialog."""
    return {
        'module_mm': module_mm(inputs),
        'module_custom': module_is_custom(inputs),
        'pressure_angle_deg': pressure_deg(inputs),
        'height_factor': height_factor(inputs),
        'height_custom': height_is_custom(inputs),
        'backlash_mm': backlash_mm(inputs),
    }
