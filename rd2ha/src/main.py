from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
import logging
import random
import signal
import threading
import time

from .config import Config
from .models import PortalDevice, PortalReading
from .mqtt_publish import MqttPublishError, MqttPublisher
from .runtime import DeviceRuntime
from .scraper import scrape_device_reading

LOGGER = logging.getLogger(__name__)
MQTT_RETRY_MAX_SECONDS = 60
FRESHNESS_INTERVAL_SECONDS = 60


class RetryBackoff:
    def __init__(self, *, maximum_seconds: float = MQTT_RETRY_MAX_SECONDS) -> None:
        self.maximum_seconds = maximum_seconds
        self.attempt = 0

    def next_delay(self) -> float:
        base = min(2 ** min(self.attempt, 16), self.maximum_seconds)
        self.attempt += 1
        return random.uniform(base * 0.8, base * 1.2)

    def reset(self) -> None:
        self.attempt = 0


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")


def read_device_reading(config: Config, device: PortalDevice) -> PortalReading:
    return scrape_device_reading(config, device)


class Coordinator:
    """One owner for state and MQTT; one long-lived worker for blocking scraping."""
    def __init__(self, config: Config, runtime: DeviceRuntime, executor: ThreadPoolExecutor,
                 *, monotonic: Callable[[], float] = time.monotonic,
                 utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.config = config
        self.runtime = runtime
        self.executor = executor
        self.monotonic = monotonic
        self.utcnow = utcnow
        self.future: Future[PortalReading] | None = None
        self.device_index = 0
        self.next_round = 0.0
        self.next_freshness = 0.0
        self.outcomes: list[str] = []
        self.snapshot_generation: int | None = None
        self.snapshot_publisher: MqttPublisher | None = None
        self.dirty_devices: set[str] = set()

    def step(self, publisher: MqttPublisher) -> None:
        now, tick = self.utcnow(), self.monotonic()
        # Consume scrape results even during a broker outage, retaining state on disk.
        if self.future is not None and self.future.done():
            device = self.config.devices[self.device_index]
            try:
                result = self.future.result()
            except Exception as exc:
                result = exc
            self.outcomes.append(self.runtime.complete(device, result, now))
            self.dirty_devices.add(device.device_id)
            self.future = None
            self.device_index += 1
            if self.device_index == len(self.config.devices):
                LOGGER.info("step=cycle_complete fresh=%s stale=%s portal_failures=%s timestamp_failures=%s",
                            self.outcomes.count("fresh"), self.outcomes.count("stale"),
                            self.outcomes.count("portal_failure"), self.outcomes.count("timestamp_failure"))
                self.outcomes.clear()
                self.device_index = 0
                self.next_round = tick + self.config.poll_interval_seconds

        if publisher.connected:
            if self.snapshot_publisher is not publisher or self.snapshot_generation != publisher.connection_generation:
                self.snapshot_generation = self.runtime.snapshot(publisher, now)
                self.snapshot_publisher = publisher
                self.dirty_devices.clear()
            for device in self.config.devices:
                if device.device_id in self.dirty_devices:
                    self.runtime.publish_device(publisher, device, now)
                    self.dirty_devices.remove(device.device_id)
            if tick >= self.next_freshness:
                for device in self.config.devices:
                    self.runtime.publish_status(publisher, device, now)
                self.next_freshness = tick + FRESHNESS_INTERVAL_SECONDS

            if self.future is None and tick >= self.next_round:
                device = self.config.devices[self.device_index]
                self.future = self.executor.submit(read_device_reading, self.config, device)


def run_forever(config: Config, stop: threading.Event | None = None) -> None:
    stop = stop if stop is not None else threading.Event()
    runtime = DeviceRuntime(config)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="portal-scrape")
    coordinator = Coordinator(config, runtime, executor)
    backoff = RetryBackoff()
    try:
        while not stop.is_set():
            try:
                with MqttPublisher(config) as publisher:
                    while not stop.is_set():
                        coordinator.step(publisher)
                        if publisher.connected:
                            backoff.reset()
                        stop.wait(0.25)
            except MqttPublishError as exc:
                delay = backoff.next_delay()
                LOGGER.warning("step=mqtt_retry error_type=%s retry_seconds=%.2f", type(exc).__name__, delay)
                stop.wait(delay)
    finally:
        # Publisher's context exits (bridge offline) before waiting for the worker.
        executor.shutdown(wait=True, cancel_futures=True)


def main() -> None:
    configure_logging()
    try:
        config = Config.from_env()
    except ValueError as exc:
        LOGGER.critical("step=config_invalid error=%s", exc)
        raise SystemExit(2) from exc
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    run_forever(config, stop)
    LOGGER.info("step=shutdown requested=true")


if __name__ == "__main__":
    main()
