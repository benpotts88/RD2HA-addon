from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import PortalDevice, PortalReading


class PortalParseError(ValueError):
    """Raised when the portal text cannot be parsed into a device reading."""


class TankParseError(PortalParseError):
    """Raised when the portal text cannot be parsed into a tank reading."""


_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

_LEVEL_PATTERN = r"(?P<value>\d+(?:\.\d+)?)\s*%"
_TEMPERATURE_PATTERN = r"(?P<value>-?\d+(?:\.\d+)?)\s*°\s*C"
_FULL_LEVEL_RE = re.compile(rf"^\s*{_LEVEL_PATTERN}\s*$", re.IGNORECASE)
_FULL_TEMPERATURE_RE = re.compile(rf"^\s*{_TEMPERATURE_PATTERN}\s*$", re.IGNORECASE)
_LABELED_LEVEL_RE = re.compile(
    rf"\b(?:current\s+)?level\b\s*:?\s*{_LEVEL_PATTERN}",
    re.IGNORECASE,
)
_LABELED_TEMPERATURE_RE = re.compile(
    rf"\btemperature\b\s*:?\s*{_TEMPERATURE_PATTERN}",
    re.IGNORECASE,
)


def parse_tank_reading(
    text: str,
    *,
    timezone_name: str = "Europe/London",
    device_name: str | None = None,
    scraped_at: datetime | None = None,
) -> PortalReading:
    lines = _non_empty_lines(text)
    level_percent, temperature_c = _extract_measurements(lines, device_name)
    tank_name = _extract_required_line_value(text, r"^Tank:\s*(?P<value>.+?)\s*$", "tank")
    location = _extract_required_line_value(
        text,
        r"^Location:\s*(?P<value>[^|\n]+)",
        "location",
    )
    raw_last_update = _extract_required_line_value(
        text,
        r"^Last update received:\s*(?P<value>.+?)\s*$",
        "last update",
    )

    timezone = _timezone(timezone_name)
    last_update = _parse_portal_datetime(raw_last_update, timezone)
    return PortalReading(
        values={
            "level_percent": level_percent,
            "temperature_c": temperature_c,
            "tank_name": tank_name,
            "location": location,
        },
        last_update=last_update,
        raw_last_update=raw_last_update,
        scraped_at=_normalize_scraped_at(scraped_at, timezone),
    )


def parse_rain_director_reading(
    text: str,
    *,
    timezone_name: str = "Europe/London",
    scraped_at: datetime | None = None,
) -> PortalReading:
    header_tank_percent = _extract_required_float(
        text,
        rf"^Header\s+Tank\s*:?\s*{_LEVEL_PATTERN}\s*$",
        "header tank percentage",
    )
    rainwater_tank_air_temp_c = _extract_required_float(
        text,
        rf"^Rainwater\s+Tank\s+Air\s+Temp\.?\s*:?\s*{_TEMPERATURE_PATTERN}\s*$",
        "rainwater tank air temperature",
    )
    mains_water_usage_l = _extract_required_volume_litres(
        text,
        r"^Total\s+mains\s+water\s+usage\s*:?\s*(?P<value>[\d,]+(?:\.\d+)?)\s*L\s*$",
        "total mains water usage",
    )
    rainwater_usage_l = _extract_required_volume_litres(
        text,
        r"^Total\s+rainwater\s+usage\s*:?\s*(?P<value>[\d,]+(?:\.\d+)?)\s*L\s*$",
        "total rainwater usage",
    )
    raw_last_update = _extract_required_line_value(
        text,
        r"^Last update received:\s*(?P<value>.+?)\s*$",
        "last update",
    )

    timezone = _timezone(timezone_name)
    last_update = _parse_portal_datetime(raw_last_update, timezone)
    return PortalReading(
        values={
            "header_tank_percent": header_tank_percent,
            "rainwater_tank_air_temp_c": rainwater_tank_air_temp_c,
            "mains_water_usage_l": mains_water_usage_l,
            "rainwater_usage_l": rainwater_usage_l,
        },
        last_update=last_update,
        raw_last_update=raw_last_update,
        scraped_at=_normalize_scraped_at(scraped_at, timezone),
    )


def parse_device_reading(
    text: str,
    device: PortalDevice,
    *,
    timezone_name: str = "Europe/London",
    scraped_at: datetime | None = None,
) -> PortalReading:
    if device.device_type == "tank_level":
        return parse_tank_reading(
            text,
            timezone_name=timezone_name,
            device_name=device.device_name,
            scraped_at=scraped_at,
        )
    if device.device_type == "rain_director":
        return parse_rain_director_reading(
            text,
            timezone_name=timezone_name,
            scraped_at=scraped_at,
        )
    raise PortalParseError(f"Unsupported device type: {device.device_type!r}")


def _non_empty_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _extract_measurements(lines: list[str], device_name: str | None) -> tuple[float, float]:
    window = _measurement_window(lines, device_name)
    sensor_window = _sensor_window(window)

    labeled_pair = _find_labeled_measurement_pair(sensor_window)
    if labeled_pair:
        return labeled_pair

    adjacent_pair = _find_adjacent_measurement_pair(sensor_window)
    if adjacent_pair:
        return adjacent_pair

    unique_pair = _find_unique_measurement_pair(sensor_window)
    if unique_pair:
        return unique_pair

    _raise_measurement_error(sensor_window)


def _measurement_window(lines: list[str], device_name: str | None) -> list[str]:
    start_index = 0
    if device_name:
        normalized_device_name = device_name.casefold()
        for index, line in enumerate(lines):
            if line.casefold() == normalized_device_name:
                start_index = index + 1
                break

    end_index = len(lines)
    for index in range(start_index, len(lines)):
        if lines[index].casefold().startswith("tank:"):
            end_index = index
            break

    return lines[start_index:end_index]


def _sensor_window(lines: list[str]) -> list[str]:
    for index, line in enumerate(lines):
        if "water level sensor" in line.casefold():
            return lines[index + 1 :]
    return lines


def _find_adjacent_measurement_pair(lines: list[str]) -> tuple[float, float] | None:
    pairs: list[tuple[float, float]] = []
    for index in range(len(lines) - 1):
        level_match = _FULL_LEVEL_RE.match(lines[index])
        temperature_match = _FULL_TEMPERATURE_RE.match(lines[index + 1])
        if level_match and temperature_match:
            pairs.append(
                (
                    float(level_match.group("value")),
                    float(temperature_match.group("value")),
                )
            )
    if len(pairs) == 1:
        return pairs[0]
    if len(pairs) > 1:
        raise TankParseError("Could not identify current level/temperature reading")
    return None


def _find_labeled_measurement_pair(lines: list[str]) -> tuple[float, float] | None:
    level_percent = _first_labeled_number(lines, _LABELED_LEVEL_RE)
    temperature_c = _first_labeled_number(lines, _LABELED_TEMPERATURE_RE)
    if level_percent is None or temperature_c is None:
        return None
    return level_percent, temperature_c


def _find_unique_measurement_pair(lines: list[str]) -> tuple[float, float] | None:
    level_values = [
        float(match.group("value"))
        for line in lines
        if (match := _FULL_LEVEL_RE.match(line))
    ]
    temperature_values = [
        float(match.group("value"))
        for line in lines
        if (match := _FULL_TEMPERATURE_RE.match(line))
    ]
    if len(level_values) == 1 and len(temperature_values) == 1:
        return level_values[0], temperature_values[0]
    return None


def _first_labeled_number(lines: list[str], pattern: re.Pattern[str]) -> float | None:
    for line in lines:
        match = pattern.search(line)
        if match:
            return float(match.group("value"))
    return None


def _raise_measurement_error(lines: list[str]) -> None:
    has_level = any(_FULL_LEVEL_RE.match(line) or _LABELED_LEVEL_RE.search(line) for line in lines)
    has_temperature = any(
        _FULL_TEMPERATURE_RE.match(line) or _LABELED_TEMPERATURE_RE.search(line)
        for line in lines
    )
    if not has_level:
        raise TankParseError("Missing required field: level percentage")
    if not has_temperature:
        raise TankParseError("Missing required field: temperature")
    raise TankParseError("Could not identify current level/temperature reading")


def _extract_required_line_value(text: str, pattern: str, label: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if not match:
        raise TankParseError(f"Missing required field: {label}")
    return match.group("value").strip()


def _extract_required_float(text: str, pattern: str, label: str) -> float:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if not match:
        raise TankParseError(f"Missing required field: {label}")
    return float(match.group("value"))


def _extract_required_volume_litres(text: str, pattern: str, label: str) -> int | float:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if not match:
        raise TankParseError(f"Missing required field: {label}")
    return _parse_number(match.group("value"))


def _parse_number(raw_value: str) -> int | float:
    normalized = raw_value.replace(",", "")
    if "." in normalized:
        return float(normalized)
    return int(normalized)


def _parse_portal_datetime(raw_value: str, timezone: ZoneInfo) -> datetime:
    match = re.search(
        r"(?P<day>\d{1,2})\s+"
        r"(?P<month>[A-Za-z]+)\s+"
        r"(?P<year>\d{4})\s+"
        r"(?P<hour>\d{1,2}):(?P<minute>\d{2})",
        raw_value,
    )
    if not match:
        raise TankParseError(f"Could not parse last update datetime: {raw_value!r}")

    month_name = match.group("month").casefold()
    if month_name not in _MONTHS:
        raise TankParseError(f"Unknown month in last update datetime: {raw_value!r}")

    return datetime(
        year=int(match.group("year")),
        month=_MONTHS[month_name],
        day=int(match.group("day")),
        hour=int(match.group("hour")),
        minute=int(match.group("minute")),
        tzinfo=timezone,
    )


def _timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise TankParseError(f"Unknown timezone: {timezone_name!r}") from exc


def _normalize_scraped_at(scraped_at: datetime | None, timezone: ZoneInfo) -> datetime:
    if scraped_at is None:
        return datetime.now(timezone)
    if scraped_at.tzinfo is None:
        return scraped_at.replace(tzinfo=timezone)
    return scraped_at
