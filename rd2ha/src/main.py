from __future__ import annotations

import logging
import time

from .config import Config
from .models import PortalDevice, PortalReading
from .mqtt_publish import MqttPublisher
from .scraper import scrape_device_reading

LOGGER = logging.getLogger(__name__)
MQTT_RETRY_MAX_SECONDS = 60


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def read_device_reading(config: Config, device: PortalDevice) -> PortalReading:
    return scrape_device_reading(config, device)


def run_cycle(config: Config, publisher: MqttPublisher) -> bool:
    cycle_ok = True
    for device in config.devices:
        try:
            reading = read_device_reading(config, device)
            publisher.publish_state(device, reading)
            publisher.publish_availability(device, True)
        except Exception as exc:
            cycle_ok = False
            LOGGER.exception(
                "step=device_cycle_failed device_id=%s device_type=%s error_type=%s",
                device.device_id,
                device.device_type,
                exc.__class__.__name__,
            )
            publisher.publish_availability(device, False)
            continue

        LOGGER.info(
            "step=device_cycle_complete device_id=%s device_type=%s raw_last_update=%s",
            device.device_id,
            device.device_type,
            reading.raw_last_update,
        )

    LOGGER.info("step=cycle_complete success=%s devices=%s", cycle_ok, len(config.devices))
    return cycle_ok


def run_poll_loop(config: Config, publisher: MqttPublisher) -> None:
    publisher.publish_discovery()
    while True:
        run_cycle(config, publisher)
        time.sleep(config.poll_interval_seconds)


def run_forever(config: Config) -> None:
    while True:
        try:
            with MqttPublisher(config) as publisher:
                run_poll_loop(config, publisher)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            retry_seconds = min(config.poll_interval_seconds, MQTT_RETRY_MAX_SECONDS)
            LOGGER.exception(
                "step=service_error error_type=%s retry_seconds=%s",
                exc.__class__.__name__,
                retry_seconds,
            )
            time.sleep(retry_seconds)


def main() -> None:
    configure_logging()
    config = Config.from_env()
    try:
        run_forever(config)
    except KeyboardInterrupt:
        LOGGER.info("step=shutdown requested=true")


if __name__ == "__main__":
    main()
