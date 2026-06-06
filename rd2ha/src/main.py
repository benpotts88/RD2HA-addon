from __future__ import annotations

import logging
import time

from .config import Config
from .models import TankReading
from .mqtt_publish import MqttPublisher
from .parser import parse_tank_reading
from .scraper import scrape_device_text

LOGGER = logging.getLogger(__name__)
MQTT_RETRY_MAX_SECONDS = 60


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def read_tank_reading(config: Config) -> TankReading:
    text = scrape_device_text(config)
    return parse_tank_reading(
        text,
        timezone_name=config.timezone,
        device_name=config.device_name,
    )


def run_cycle(config: Config, publisher: MqttPublisher) -> bool:
    try:
        reading = read_tank_reading(config)
        publisher.publish_state(reading)
        publisher.publish_availability(True)
    except Exception as exc:
        LOGGER.exception(
            "step=cycle_failed error_type=%s",
            exc.__class__.__name__,
        )
        publisher.publish_availability(False)
        return False

    LOGGER.info(
        "step=cycle_complete level_percent=%s temperature_c=%s raw_last_update=%s",
        reading.level_percent,
        reading.temperature_c,
        reading.raw_last_update,
    )
    return True


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
