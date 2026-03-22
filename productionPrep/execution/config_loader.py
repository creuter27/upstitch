"""
Unified config loader for productionPrep.

Loads config/config.yaml (common settings) and merges the platform-specific
override on top:
  - macOS / Linux  →  config/config-mac.yaml
  - Windows        →  config/config-win.yaml

Keys in the platform file override those in config.yaml.
Nested dicts are merged recursively (so you only need to set the keys that
differ per platform; everything else is inherited from config.yaml).
"""

import sys
from pathlib import Path

import yaml

_CONFIG_DIR = Path(__file__).parent.parent / "config"


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Override wins on conflicts."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict:
    """Return the merged config for the current platform."""
    base = _load_yaml(_CONFIG_DIR / "config.yaml")
    platform_file = "config-win.yaml" if sys.platform == "win32" else "config-mac.yaml"
    override = _load_yaml(_CONFIG_DIR / platform_file)
    return _deep_merge(base, override)
