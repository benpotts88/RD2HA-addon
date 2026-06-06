from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping


SUPPORTED_DEVICE_TYPES = frozenset({"tank_level", "rain_director"})


@dataclass(frozen=True)
class PortalDevice:
    device_id: str
    device_name: str
    device_url: str
    device_type: str
    mqtt_base_topic: str

    def __post_init__(self) -> None:
        trimmed_values = {
            "device_id": self.device_id.strip(),
            "device_name": self.device_name.strip(),
            "device_url": self.device_url.strip(),
            "device_type": self.device_type.strip(),
            "mqtt_base_topic": self.mqtt_base_topic.strip(),
        }
        for field_name, value in trimmed_values.items():
            object.__setattr__(self, field_name, value)

        missing = [
            field_name
            for field_name in ("device_id", "device_name", "device_url", "device_type")
            if not trimmed_values[field_name]
        ]
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"Device config missing required field(s): {joined}")

        if self.device_type not in SUPPORTED_DEVICE_TYPES:
            supported = ", ".join(sorted(SUPPORTED_DEVICE_TYPES))
            raise ValueError(
                f"Unsupported device type {self.device_type!r}; expected one of: {supported}"
            )

        if not self.mqtt_base_topic:
            object.__setattr__(self, "mqtt_base_topic", f"raindirector/{self.device_id}")

    @property
    def state_topic(self) -> str:
        return f"{self.mqtt_base_topic}/state"

    @property
    def availability_topic(self) -> str:
        return f"{self.mqtt_base_topic}/availability"


@dataclass(frozen=True)
class PortalReading:
    values: Mapping[str, object]
    last_update: datetime
    raw_last_update: str
    scraped_at: datetime
    source: str = "tanklevels_portal"

    def __getattr__(self, name: str) -> object:
        if name in self.values:
            return self.values[name]
        raise AttributeError(name)

    def to_payload(self) -> dict[str, object]:
        return {
            **dict(self.values),
            "last_update": self.last_update.isoformat(timespec="seconds"),
            "raw_last_update": self.raw_last_update,
            "scraped_at": self.scraped_at.isoformat(timespec="seconds"),
            "source": self.source,
        }

    def stable_fingerprint(self) -> tuple[tuple[str, object], ...]:
        return (
            *tuple((key, self.values[key]) for key in sorted(self.values)),
            ("last_update", self.last_update),
            ("raw_last_update", self.raw_last_update),
        )


TankReading = PortalReading
