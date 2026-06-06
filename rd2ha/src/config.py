from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .models import PortalDevice

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is present in normal runtime
    load_dotenv = None

_ENV_TO_ADDON_OPTION = {
    "TANKLEVELS_EMAIL": "tanklevels_email",
    "TANKLEVELS_PASSWORD": "tanklevels_password",
    "TANKLEVELS_DEVICE_URL": "tanklevels_device_url",
    "DEVICE_TYPE": "device_type",
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

_DEVICE_REQUIRED_FIELDS = {
    "id": ("id", "device_id"),
    "name": ("name", "device_name"),
    "url": ("url", "device_url", "tanklevels_device_url"),
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


def _raw_devices_config() -> object:
    raw_env_value = os.getenv("TANKLEVELS_DEVICES_JSON")
    if raw_env_value is not None:
        raw_env_value = raw_env_value.strip()
        if not raw_env_value:
            return []
        try:
            return json.loads(raw_env_value)
        except json.JSONDecodeError as exc:
            raise ValueError("TANKLEVELS_DEVICES_JSON must be valid JSON") from exc

    return _addon_options().get("devices", [])


def _parse_devices(raw_devices: object) -> tuple[PortalDevice, ...]:
    if raw_devices in (None, "", []):
        return ()
    if isinstance(raw_devices, str):
        try:
            raw_devices = json.loads(raw_devices)
        except json.JSONDecodeError as exc:
            raise ValueError("devices must be a JSON list when supplied as a string") from exc
    if not isinstance(raw_devices, list):
        raise ValueError("devices must be a list")

    devices: list[PortalDevice] = []
    for index, raw_device in enumerate(raw_devices):
        if not isinstance(raw_device, dict):
            raise ValueError(f"devices[{index}] must be an object")

        device_id = _required_device_value(raw_device, index, "id")
        device_name = _required_device_value(raw_device, index, "name")
        device_url = _required_device_value(raw_device, index, "url")
        device_type = str(raw_device.get("type") or raw_device.get("device_type") or "tank_level")
        mqtt_base_topic = str(
            raw_device.get("base_topic")
            or raw_device.get("mqtt_base_topic")
            or f"raindirector/{device_id}"
        )

        devices.append(
            PortalDevice(
                device_id=device_id,
                device_name=device_name,
                device_url=device_url,
                device_type=device_type,
                mqtt_base_topic=mqtt_base_topic,
            )
        )

    return tuple(devices)


def _required_device_value(raw_device: dict[str, object], index: int, key: str) -> str:
    for field_name in _DEVICE_REQUIRED_FIELDS[key]:
        value = raw_device.get(field_name)
        if value is not None and str(value).strip():
            return str(value).strip()
    expected = " or ".join(_DEVICE_REQUIRED_FIELDS[key])
    raise ValueError(f"devices[{index}] missing required field: {expected}")


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
    device_type: str
    timezone: str
    storage_state_path: Path
    devices: tuple[PortalDevice, ...] = ()
    login_url: str = "https://tanklevels.co.uk/login"
    playwright_timeout_ms: int = 45_000

    def __post_init__(self) -> None:
        if not self.devices:
            object.__setattr__(
                self,
                "devices",
                (
                    PortalDevice(
                        device_id=self.device_id,
                        device_name=self.device_name,
                        device_url=self.tanklevels_device_url,
                        device_type=self.device_type,
                        mqtt_base_topic=self.mqtt_base_topic,
                    ),
                ),
            )
        self._validate_unique_devices()

    @classmethod
    def from_env(cls) -> "Config":
        if load_dotenv is not None:
            load_dotenv()

        tanklevels_device_url = _env(
            "TANKLEVELS_DEVICE_URL",
            "https://tanklevels.co.uk/devices/CHANGE_ME",
        )
        mqtt_base_topic = _env("MQTT_BASE_TOPIC", "raindirector/tank")
        device_name = _env("DEVICE_NAME", "Tank")
        device_id = _env("DEVICE_ID", "tank")
        device_type = _env("DEVICE_TYPE", "tank_level")

        return cls(
            tanklevels_email=_env("TANKLEVELS_EMAIL"),
            tanklevels_password=_env("TANKLEVELS_PASSWORD"),
            tanklevels_device_url=tanklevels_device_url,
            mqtt_host=_env("MQTT_HOST", "core-mosquitto"),
            mqtt_port=_env_int("MQTT_PORT", 1883),
            mqtt_username=_env("MQTT_USERNAME"),
            mqtt_password=_env("MQTT_PASSWORD"),
            poll_interval_seconds=_env_int("POLL_INTERVAL_SECONDS", 900),
            playwright_headless=_env_bool("PLAYWRIGHT_HEADLESS", True),
            mqtt_base_topic=mqtt_base_topic,
            ha_discovery_prefix=_env("HA_DISCOVERY_PREFIX", "homeassistant"),
            device_name=device_name,
            device_id=device_id,
            device_type=device_type,
            timezone=_env("TIMEZONE", "Europe/London"),
            storage_state_path=Path(
                _env("PLAYWRIGHT_STORAGE_STATE", "/config/storage_state.json")
            ),
            devices=_parse_devices(_raw_devices_config()),
            playwright_timeout_ms=_env_int("PLAYWRIGHT_TIMEOUT_MS", 45_000),
        )

    @property
    def state_topic(self) -> str:
        return self.devices[0].state_topic

    @property
    def availability_topic(self) -> str:
        return self.devices[0].availability_topic

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

    def _validate_unique_devices(self) -> None:
        seen_ids: set[str] = set()
        seen_topics: set[str] = set()
        for device in self.devices:
            if device.device_id in seen_ids:
                raise ValueError(f"Duplicate device id: {device.device_id!r}")
            if device.mqtt_base_topic in seen_topics:
                raise ValueError(f"Duplicate MQTT base topic: {device.mqtt_base_topic!r}")
            seen_ids.add(device.device_id)
            seen_topics.add(device.mqtt_base_topic)
