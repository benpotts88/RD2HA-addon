"""Coordinator-owned device state. Scrape workers return results only."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging

from .cache import ReadingCache
from .config import Config
from .freshness import assess_timestamp
from .models import PortalDevice, PortalReading
from .mqtt_publish import MqttPublishError, MqttPublisher
from .parser import TimestampParseError
from .scraper import AuthExpiredError, LoginError, DevicePageError, PageLoadError

LOGGER = logging.getLogger(__name__)


@dataclass
class DeviceState:
    reading: PortalReading | None = None
    portal_connected: bool | None = None
    timestamp_problem: bool | None = None
    last_attempt_at: datetime | None = None
    last_successful_scrape_at: datetime | None = None
    error_code: str | None = None
    last_status: dict[str, object] | None = None

    def status(self, config: Config, now: datetime) -> dict[str, object]:
        reading = self.reading
        assessment = assess_timestamp(reading.last_update if reading else None, now,
            config.max_reading_age_seconds, config.max_future_skew_seconds)
        # An accepted timestamp can appear future-dated after a wall-clock correction.
        # Never report it fresh in that case; a later valid scrape clears the problem.
        timestamp_problem = self.timestamp_problem
        if reading and assessment.error_code:
            self.timestamp_problem = timestamp_problem = True
            self.error_code = assessment.error_code
        return {
            "data_stale": assessment.stale,
            "portal_connected": self.portal_connected,
            "timestamp_problem": timestamp_problem,
            "last_update": reading.last_update.isoformat(timespec="seconds") if reading else None,
            "last_attempt_at": iso(self.last_attempt_at),
            "last_successful_scrape_at": iso(self.last_successful_scrape_at),
            "reading_age_seconds": assessment.age_seconds,
            "max_reading_age_seconds": config.max_reading_age_seconds,
            "error_code": self.error_code,
        }


def iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds") if value else None


def portal_error_code(error: Exception) -> str:
    if isinstance(error, (LoginError, AuthExpiredError)):
        return "authentication"
    if isinstance(error, PageLoadError):
        return "page_loading"
    if isinstance(error, DevicePageError):
        return "incomplete_page"
    return "page_loading"


class DeviceRuntime:
    def __init__(self, config: Config, *, now: datetime | None = None) -> None:
        self.config = config
        self.cache = ReadingCache(config)
        readings = self.cache.load(now)
        self.states = {
            device.device_id: DeviceState(
                reading=readings.get(device.device_id),
                last_successful_scrape_at=(readings[device.device_id].scraped_at
                                          if device.device_id in readings else None),
            ) for device in config.devices
        }

    def complete(self, device: PortalDevice, result: PortalReading | Exception, now: datetime) -> str:
        state = self.states[device.device_id]
        state.last_attempt_at = now
        if isinstance(result, Exception):
            if isinstance(result, TimestampParseError):
                state.portal_connected = True
                state.timestamp_problem = True
                state.error_code = "invalid_timestamp"
                outcome = "timestamp_failure"
            else:
                state.portal_connected = False
                state.error_code = portal_error_code(result)
                outcome = "portal_failure"
            LOGGER.error("step=device_read_failed device_id=%s error_code=%s error_type=%s",
                         device.device_id, state.error_code, type(result).__name__,
                         exc_info=(type(result), result, result.__traceback__))
            return outcome
        state.portal_connected = True
        assessment = assess_timestamp(result.last_update, now,
            self.config.max_reading_age_seconds, self.config.max_future_skew_seconds)
        state.timestamp_problem = assessment.error_code is not None
        state.error_code = assessment.error_code
        if assessment.error_code:
            LOGGER.warning("step=timestamp_rejected device_id=%s error_code=%s",
                           device.device_id, assessment.error_code)
            return "timestamp_failure"
        state.reading = result
        state.last_successful_scrape_at = now
        self.cache.save({key: item.reading for key, item in self.states.items() if item.reading is not None})
        LOGGER.info("step=device_cycle_complete device_id=%s stale=%s", device.device_id, assessment.stale)
        return "stale" if assessment.stale else "fresh"

    def publish_device(self, publisher: MqttPublisher, device: PortalDevice, now: datetime) -> None:
        state = self.states[device.device_id]
        if state.reading is not None:
            publisher.publish_state(device, state.reading)
        publisher.publish_reading_availability(device, state.reading is not None)
        self.publish_status(publisher, device, now, force=True)

    def publish_status(self, publisher: MqttPublisher, device: PortalDevice, now: datetime, *, force: bool = False) -> None:
        state = self.states[device.device_id]
        payload = state.status(self.config, now)
        previous = state.last_status
        if force or previous != payload:
            publisher.publish_status(device, payload)
            state.last_status = payload
            if previous is None or previous["data_stale"] != payload["data_stale"]:
                LOGGER.info("step=freshness_changed device_id=%s stale=%s", device.device_id, payload["data_stale"])

    def snapshot(self, publisher: MqttPublisher, now: datetime) -> int:
        generation = publisher.connection_generation
        publisher.publish_bridge_availability(False)
        publisher.publish_discovery()
        for device in self.config.devices:
            self.publish_device(publisher, device, now)
        if not publisher.connected or publisher.connection_generation != generation:
            raise MqttPublishError("Connection changed during snapshot")
        publisher.publish_bridge_availability(True)
        LOGGER.info("step=mqtt_snapshot_restored devices=%s", len(self.config.devices))
        return generation
