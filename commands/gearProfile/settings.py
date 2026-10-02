"""Remembered dialog settings: in memory for the session, and in settings.json across sessions."""
import json
import os

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                             'settings.json')

DEFAULTS = {
    'module_mm': 1.0,
    'module_custom': False,
    'pressure_angle_deg': 20.0,
    'gear_type': 'external',
    'backlash_mm': 0.05,
    'height_factor': 1.0,
    'height_custom': False,
}

_current = None


def load() -> dict:
    global _current
    if _current is None:
        _current = dict(DEFAULTS)
        try:
            with open(SETTINGS_FILE, encoding='utf-8') as f:
                stored = json.load(f)
            for key, default in DEFAULTS.items():
                value = stored.get(key)
                if isinstance(default, float) and isinstance(value, (int, float)) and not isinstance(value, bool):
                    _current[key] = float(value)
                elif isinstance(value, type(default)):
                    _current[key] = value
        except (OSError, ValueError):
            pass
    return dict(_current)


def save(values: dict) -> None:
    """Store the given settings; keys not given keep their current value."""
    global _current
    current = load()
    _current = {key: values.get(key, current[key]) for key in DEFAULTS}
    for key, value in _current.items():
        if isinstance(value, float):
            _current[key] = round(value, 6)  # unit conversions leave float noise
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(_current, f, indent=2)
    except OSError:
        pass
