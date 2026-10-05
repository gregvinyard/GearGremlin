"""Remembered settings: files written by older versions still load."""
import importlib.util
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'tests', 'out')


def out_file(name):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    if os.path.exists(path):
        os.remove(path)
    return path


def load_settings(path):
    spec = importlib.util.spec_from_file_location(
        'gg_settings', os.path.join(ROOT, 'commands', 'gearProfile', 'settings.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.SETTINGS_FILE = str(path)
    return module


def test_old_settings_file_without_rack_keys_loads():
    path = out_file('settings_old.json')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(json.dumps({'module_mm': 2, 'pressure_angle_deg': 25.0, 'gear_type': 'internal'}))
    settings = load_settings(path)
    loaded = settings.load()
    assert loaded['module_mm'] == 2.0 and loaded['pressure_angle_deg'] == 25.0
    assert loaded['rack_body_mm'] == -1.0


def test_rack_body_is_remembered():
    path = out_file('settings_new.json')
    settings = load_settings(path)
    settings.save({'rack_body_mm': 7.5})
    with open(path, encoding='utf-8') as f:
        assert json.load(f)['rack_body_mm'] == 7.5
    assert load_settings(path).load()['rack_body_mm'] == 7.5
