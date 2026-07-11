from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import Config
from .models import PortalDevice, PortalReading
from .mqtt_publish import MqttPublishError, MqttPublisher
from .scraper import scrape_device_reading

LOGGER = logging.getLogger(__name__)
MQTT_RETRY_MAX_SECONDS = 60


class StaleReadingError(ValueError):
    pass


@dataclass(frozen=True)
class CycleResult:
    successful_devices: int
    failed_device_ids: tuple[str, ...]

    def __bool__(self) -> bool:
        return not self.failed_device_ids


class RetryBackoff:
    def __init__(self, *, maximum_seconds: float = MQTT_RETRY_MAX_SECONDS) -> None:
        self.maximum_seconds = maximum_seconds
        self.attempt = 0

    def next_delay(self) -> float:
        base = min(2**self.attempt, self.maximum_seconds)
        self.attempt += 1
        return random.uniform(base * 0.8, base * 1.2)

    def reset(self) -> None:
        self.attempt = 0


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def read_device_reading(config: Config, device: PortalDevice) -> PortalReading:
    return scrape_device_reading(config, device)


def validate_reading_freshness(
    config: Config,
    device: PortalDevice,
    reading: PortalReading,
    *,
    now: datetime | None = None,
) -> None:
    if reading.last_update.tzinfo is None:
        raise StaleReadingError(
            f"Reading timestamp is timezone-naive for {device.device_id}"
        )
    observed_at = now or datetime.now(timezone.utc)
    age_seconds = (observed_at - reading.last_update).total_seconds()
    if age_seconds > config.max_reading_age_seconds:
        raise StaleReadingError(
            f"Reading for {device.device_id} is {age_seconds:.0f}s old; "
            f"maximum is {config.max_reading_age_seconds}s"
        )
    if age_seconds < -config.max_future_skew_seconds:
        raise StaleReadingError(
            f"Reading for {device.device_id} is {-age_seconds:.0f}s in the future; "
            f"maximum skew is {config.max_future_skew_seconds}s"
        )


def run_cycle(config: Config, publisher: MqttPublisher) -> CycleResult:
    successful_devices = 0
    failed_device_ids: list[str] = []
    for device in config.devices:
        try:
            reading = read_device_reading(config, device)
            validate_reading_freshness(config, device, reading)
        except Exception as exc:
            failed_device_ids.append(device.device_id)
            LOGGER.exception(
                "step=device_read_failed device_id=%s device_type=%s error_type=%s",
                device.device_id,
                device.device_type,
                exc.__class__.__name__,
            )
            try:
                publisher.publish_availability(device, False)
            except MqttPublishError as mqtt_exc:
                raise MqttPublishError(
                    f"Failed to mark {device.device_id} offline after read failure"
                ) from mqtt_exc
            continue

        publisher.publish_state(device, reading)
        publisher.publish_availability(device, True)
        successful_devices += 1
        LOGGER.info(
            "step=device_cycle_complete device_id=%s device_type=%s raw_last_update=%s",
            device.device_id,
            device.device_type,
            reading.raw_last_update,
        )

    result = CycleResult(successful_devices, tuple(failed_device_ids))
    LOGGER.info(
        "step=cycle_complete success=%s successful_devices=%s failed_devices=%s",
        bool(result),
        result.successful_devices,
        len(result.failed_device_ids),
    )
    return result


def run_poll_loop(
    config: Config,
    publisher: MqttPublisher,
    *,
    on_ready: Callable[[], None] | None = None,
) -> None:
    publisher.publish_startup_availability()
    publisher.publish_discovery()
    if on_ready is not None:
        on_ready()
    while True:
        run_cycle(config, publisher)
        time.sleep(config.poll_interval_seconds)


def run_forever(config: Config) -> None:
    backoff = RetryBackoff()
    while True:
        try:
            with MqttPublisher(config) as publisher:
                run_poll_loop(config, publisher, on_ready=backoff.reset)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            retry_seconds = backoff.next_delay()
            LOGGER.exception(
                "step=service_error error_type=%s retry_seconds=%.2f",
                exc.__class__.__name__,
                retry_seconds,
            )
            time.sleep(retry_seconds)


def main() -> None:
    configure_logging()
    try:
        config = Config.from_env()
    except ValueError as exc:
        LOGGER.critical("step=config_invalid error=%s", exc)
        raise SystemExit(2) from exc
    try:
        run_forever(config)
    except KeyboardInterrupt:
        LOGGER.info("step=shutdown requested=true")


if __name__ == "__main__":
    main()
