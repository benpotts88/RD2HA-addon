from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is present in normal runtime
    load_dotenv = None
_ENV_TO_ADDON_OPTION = {
    "TANKLEVELS_EMAIL": "tanklevels_email",
    "TANKLEVELS_PASSWORD": "tanklevels_password",
    "TANKLEVELS_DEVICE_URL": "tanklevels_device_url",
    "MQTT_HOST": "mqtt_host",
    "MQTT_PORT": "mqtt_port",
    "MQTT_USERNAME": "mqtt_username",
    "MQTT_PASSWORD": "mqtt_password",
    "POLL_INTERVAL_SECONDS": "poll_interval_seconds",
    "PLAYWRIGHT_HEADLESS": "playwright_headless",
    "MQTT_BASE_TOPIC": "mqtt_base_topic",
    "HA_DISCOVERY_PREFIX": "ha_discovery_prefix",
    "DEVICE_NAME": "device_name",
    "DEVICE_ID": "device_id",
    "TIMEZONE": "timezone",
    "PLAYWRIGHT_TIMEOUT_MS": "playwright_timeout_ms",
}


def _addon_options() -> dict[str, object]:
    options_path = Path(os.getenv("HASSIO_OPTIONS_PATH", "/data/options.json"))
    if not options_path.exists():
        return {}
    try:
        raw_options = json.loads(options_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not parse add-on options JSON at {options_path}") from exc
    if not isinstance(raw_options, dict):
        raise ValueError(f"Add-on options JSON at {options_path} must be an object")
    return raw_options


def _env(name: str, default: str = "") -> str:
    raw_env_value = os.getenv(name)
    if raw_env_value is not None:
        return raw_env_value.strip()

    option_name = _ENV_TO_ADDON_OPTION.get(name)
    options = _addon_options()
    if option_name and option_name in options and options[option_name] is not None:
        return str(options[option_name]).strip()

    return default.strip()


def _env_int(name: str, default: int) -> int:
    raw_value = _env(name, str(default))
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw_value!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw_value = _env(name, str(default)).lower()
    if raw_value in {"1", "true", "yes", "y", "on"}:
        return True
    if raw_value in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean, got {raw_value!r}")


@dataclass(frozen=True)
class Config:
    tanklevels_email: str
    tanklevels_password: str
    tanklevels_device_url: str
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: str
    poll_interval_seconds: int
    playwright_headless: bool
    mqtt_base_topic: str
    ha_discovery_prefix: str
    device_name: str
    device_id: str
    timezone: str
    storage_state_path: Path
    login_url: str = "https://tanklevels.co.uk/login"
    playwright_timeout_ms: int = 45_000

    @classmethod
    def from_env(cls) -> "Config":
        if load_dotenv is not None:
            load_dotenv()

        return cls(
            tanklevels_email=_env("TANKLEVELS_EMAIL"),
            tanklevels_password=_env("TANKLEVELS_PASSWORD"),
            tanklevels_device_url=_env(
                "TANKLEVELS_DEVICE_URL",
                "https://tanklevels.co.uk/devices/CHANGE_ME",
            ),
            mqtt_host=_env("MQTT_HOST", "core-mosquitto"),
            mqtt_port=_env_int("MQTT_PORT", 1883),
            mqtt_username=_env("MQTT_USERNAME"),
            mqtt_password=_env("MQTT_PASSWORD"),
            poll_interval_seconds=_env_int("POLL_INTERVAL_SECONDS", 900),
            playwright_headless=_env_bool("PLAYWRIGHT_HEADLESS", True),
            mqtt_base_topic=_env("MQTT_BASE_TOPIC", "raindirector/tank"),
            ha_discovery_prefix=_env("HA_DISCOVERY_PREFIX", "homeassistant"),
            device_name=_env("DEVICE_NAME", "Tank"),
            device_id=_env("DEVICE_ID", "tank"),
            timezone=_env("TIMEZONE", "Europe/London"),
            storage_state_path=Path(
                _env("PLAYWRIGHT_STORAGE_STATE", "/config/storage_state.json")
            ),
            playwright_timeout_ms=_env_int("PLAYWRIGHT_TIMEOUT_MS", 45_000),
        )

    @property
    def state_topic(self) -> str:
        return f"{self.mqtt_base_topic}/state"

    @property
    def availability_topic(self) -> str:
        return f"{self.mqtt_base_topic}/availability"

    def require_tanklevels_credentials(self) -> None:
        missing = [
            name
            for name, value in (
                ("TANKLEVELS_EMAIL", self.tanklevels_email),
                ("TANKLEVELS_PASSWORD", self.tanklevels_password),
            )
            if not value
        ]
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"Missing required Tanklevels credential(s): {joined}")
