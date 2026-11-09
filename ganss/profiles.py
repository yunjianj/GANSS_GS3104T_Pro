"""Persistent user keymap profiles; the factory profile is never stored."""
import json
import os
from pathlib import Path
import tempfile

from .protocol import parse_profile

DEFAULT_NAME = '默认（正常键位）'


def config_path():
    base = os.environ.get('XDG_CONFIG_HOME')
    return (Path(base) if base and os.path.isabs(base) else Path.home() / '.config') / 'ganss-gs3104t' / 'profiles.json'


def validate(profiles):
    if not isinstance(profiles, dict):
        raise ValueError('规则集文件格式错误')
    for name, profile in profiles.items():
        if not isinstance(name, str) or not name.strip() or name == DEFAULT_NAME:
            raise ValueError('规则集名称无效或与默认规则集冲突')
        parse_profile(profile)
    return profiles


def load(path=None):
    path = Path(path) if path is not None else config_path()
    try:
        with path.open(encoding='utf-8') as stream:
            return validate(json.load(stream))
    except FileNotFoundError:
        return {}


def save(profiles, path=None):
    validate(profiles)
    path = Path(path) if path is not None else config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.profiles-', delete=False) as stream:
            temporary = stream.name
            json.dump(profiles, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
