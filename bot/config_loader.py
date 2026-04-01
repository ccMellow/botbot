"""
config_loader.py
Laster config.yaml én gang og cacher resultatet.
Alle andre moduler henter konfigurasjon herfra.
"""

import os
import yaml

_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
_cache: dict | None = None


def get_config() -> dict:
    """Returner innholdet av config.yaml (cachet etter første lesing)."""
    global _cache
    if _cache is None:
        if not os.path.exists(_CONFIG_FILE):
            raise FileNotFoundError(
                f"config.yaml ikke funnet: {_CONFIG_FILE}\n"
                "Kopier config.yaml til prosjektmappen og fyll inn verdier."
            )
        with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
            _cache = yaml.safe_load(f)
    return _cache


def active_strategy_cfg() -> dict:
    """Returner konfigurasjonsdiktet for den aktive strategien."""
    cfg = get_config()
    name = cfg["strategy"]["active"]
    strategies = cfg.get("strategies", {})
    if name not in strategies:
        raise ValueError(
            f"Ukjent strategi '{name}' i config.yaml. "
            f"Gyldige verdier: {list(strategies.keys())}"
        )
    return strategies[name]
