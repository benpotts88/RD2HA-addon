from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TankReading:
    level_percent: float
    temperature_c: float
    tank_name: str
    location: str
    last_update: datetime
    raw_last_update: str
    scraped_at: datetime
    source: str = "tanklevels_portal"

    def to_payload(self) -> dict[str, object]:
        return {
            "level_percent": self.level_percent,
            "temperature_c": self.temperature_c,
            "tank_name": self.tank_name,
            "location": self.location,
            "last_update": self.last_update.isoformat(timespec="seconds"),
            "raw_last_update": self.raw_last_update,
            "scraped_at": self.scraped_at.isoformat(timespec="seconds"),
            "source": self.source,
        }
