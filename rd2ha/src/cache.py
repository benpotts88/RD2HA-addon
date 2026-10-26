"""Private, atomic persistence of accepted readings, independent of login state."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import tempfile
from collections.abc import Mapping

from .config import Config
from .freshness import assess_timestamp
from .models import PortalDevice, PortalReading

LOGGER = logging.getLogger(__name__)
NUMERIC_FIELDS = {
    "tank_level": ("level_percent", "temperature_c"),
    "rain_director": ("header_tank_percent", "rainwater_tank_air_temp_c",
                      "mains_water_usage_l", "rainwater_usage_l"),
}


def identity(device: PortalDevice) -> dict[str, str]:
    return {"device_type": device.device_type, "base_topic": device.mqtt_base_topic,
            "url_hash": hashlib.sha256(device.device_url.encode()).hexdigest()}


def decode_reading(payload: object, device: PortalDevice) -> PortalReading:
    if not isinstance(payload, dict):
        raise ValueError("invalid reading")
    for key in NUMERIC_FIELDS[device.device_type]:
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("invalid measurement")
    if device.device_type == "tank_level":
        for key in ("tank_name", "location"):
            if not isinstance(payload.get(key), str) or not payload[key].strip():
                raise ValueError("invalid tank metadata")
    for key in ("raw_last_update", "source"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise ValueError("invalid metadata")
    last_update = datetime.fromisoformat(payload["last_update"])
    scraped_at = datetime.fromisoformat(payload["scraped_at"])
    if last_update.utcoffset() is None or scraped_at.utcoffset() is None:
        raise ValueError("naive timestamp")
    metadata = {"last_update", "scraped_at", "raw_last_update", "source"}
    return PortalReading({k: v for k, v in payload.items() if k not in metadata},
                         last_update, payload["raw_last_update"], scraped_at, payload["source"])


class ReadingCache:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.path = config.storage_state_path.parent / "readings_cache.json"

    def load(self, now: datetime | None = None) -> dict[str, PortalReading]:
        restored: dict[str, PortalReading] = {}
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(document, dict) or document.get("version") != 1:
                raise ValueError("unsupported cache")
            entries = document["devices"]
            if not isinstance(entries, dict):
                raise ValueError("invalid entries")
        except FileNotFoundError:
            return restored
        except (OSError, ValueError, KeyError, TypeError):
            LOGGER.warning("step=cache_load_failed")
            return restored
        observed_at = now or datetime.now(timezone.utc)
        for device in self.config.devices:
            entry = entries.get(device.device_id)
            if entry is None:
                continue
            try:
                if not isinstance(entry, dict) or entry.get("identity") != identity(device):
                    raise ValueError("identity mismatch")
                reading = decode_reading(entry["reading"], device)
                assessment = assess_timestamp(reading.last_update, observed_at,
                    self.config.max_reading_age_seconds, self.config.max_future_skew_seconds)
                if assessment.error_code:
                    raise ValueError("invalid timestamp")
            except (ValueError, TypeError, KeyError, OverflowError):
                LOGGER.warning("step=cache_entry_ignored device_id=%s", device.device_id)
                continue
            restored[device.device_id] = reading
            LOGGER.info("step=cache_restored device_id=%s stale=%s", device.device_id, assessment.stale)
        return restored

    def save(self, readings: Mapping[str, PortalReading]) -> None:
        document = {"version": 1, "devices": {
            device.device_id: {"identity": identity(device), "reading": readings[device.device_id].to_payload()}
            for device in self.config.devices if device.device_id in readings
        }}
        temporary: str | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".readings_cache.", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(document, stream, allow_nan=False)
            os.replace(temporary, self.path)
        except (OSError, ValueError, TypeError):
            LOGGER.warning("step=cache_write_failed")
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    LOGGER.warning("step=cache_cleanup_failed")
