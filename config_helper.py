"""Shared configuration helpers for Aymannoti."""

from pathlib import Path

import yaml

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.yaml"

VERSION = "1.3"

DEFAULT_CONFIG = {
    "tiktok": {"cookies_file": ""},
    "instagram": {
        "cookies_file": "",
        "username": "",   # optional — used by setup-cookies credential login
        "password": "",   # optional — use setup-cookies to generate a cookie file instead
    },
    "polling": {
        "interval_minutes": 3,
        "delay_between_requests": 1,
        "concurrent_requests": 5,           # TikTok parallel workers
        "instagram_concurrent_requests": 1,  # Instagram: sequential to avoid blocks
    },
    "discord": {"bot_name": "Aymannoti"},
    "dashboard": {"host": "0.0.0.0", "port": 8080},
    "groups": [],
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or dict(DEFAULT_CONFIG)


def save_config(config: dict):
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
