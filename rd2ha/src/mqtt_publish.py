from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import logging

import paho.mqtt.client as mqtt

from .config import Config
from .models import TankReading

LOGGER = logging.getLogger(__name__)


class MqttPublishError(RuntimeError):
    pass


@dataclass(frozen=True)
class SensorDiscovery:
    object_id: str
    name_suffix: str
    value_template: str
    device_class: str
    unit_of_measurement: str | None = None
    state_class: str | None = None


SENSOR_DISCOVERY = (
    SensorDiscovery(
        object_id="level",
        name_suffix="Level",
        value_template="{{ value_json.level_percent }}",
        unit_of_measurement="%",
        device_class="moisture",
        state_class="measurement",
    ),
    SensorDiscovery(
        object_id="temperature",
        name_suffix="Temperature",
        value_template="{{ value_json.temperature_c }}",
        unit_of_measurement="°C",
        device_class="temperature",
        state_class="measurement",
    ),
    SensorDiscovery(
        object_id="last_update",
        name_suffix="Last Update",
        value_template="{{ value_json.last_update }}",
        device_class="timestamp",
    ),
)


class MqttPublisher:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"{config.device_id}_raindirector_ha",
        )
        if config.mqtt_username or config.mqtt_password:
            self.client.username_pw_set(
                username=config.mqtt_username,
                password=config.mqtt_password,
            )

    def __enter__(self) -> "MqttPublisher":
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.disconnect()

    def connect(self) -> None:
        LOGGER.info(
            "step=mqtt_connect host=%s port=%s",
            self.config.mqtt_host,
            self.config.mqtt_port,
        )
        result = self.client.connect(self.config.mqtt_host, self.config.mqtt_port, keepalive=60)
        if result != mqtt.MQTT_ERR_SUCCESS:
            raise MqttPublishError(f"MQTT connect failed with result code {result}")
        self.client.loop_start()

    def disconnect(self) -> None:
        self.client.disconnect()
        self.client.loop_stop()

    def publish_discovery(self) -> None:
        for topic, payload in self.discovery_payloads().items():
            self._publish(topic, payload, retain=True)
        LOGGER.info("step=mqtt_discovery published=true")

    def publish_state(self, reading: TankReading) -> None:
        self._publish(self.config.state_topic, reading.to_payload(), retain=False)
        LOGGER.info("step=mqtt_state published=true")

    def publish_availability(self, online: bool) -> None:
        self._publish(
            self.config.availability_topic,
            "online" if online else "offline",
            retain=True,
        )

    def _publish(self, topic: str, payload: Mapping[str, object] | str, *, retain: bool) -> None:
        encoded_payload = (
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            if isinstance(payload, Mapping)
            else payload
        )
        info = self.client.publish(topic, encoded_payload, qos=1, retain=retain)
        info.wait_for_publish(timeout=15)
        if not info.is_published():
            raise MqttPublishError(f"MQTT publish timed out for {topic}")
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise MqttPublishError(f"MQTT publish failed for {topic}: result code {info.rc}")

    def discovery_payloads(self) -> dict[str, dict[str, object]]:
        device = {
            "identifiers": [self.config.device_id],
            "name": self.config.device_name,
            "manufacturer": "Black Box Controls",
            "model": "Tank acculevel",
        }

        return {
            self._discovery_topic(sensor.object_id): self._sensor_payload(sensor, device)
            for sensor in SENSOR_DISCOVERY
        }

    def _sensor_payload(
        self,
        sensor: SensorDiscovery,
        device: dict[str, object],
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self._entity_name(sensor.name_suffix),
            "unique_id": f"{self.config.device_id}_{sensor.object_id}",
            "state_topic": self.config.state_topic,
            "availability_topic": self.config.availability_topic,
            "value_template": sensor.value_template,
            "device_class": sensor.device_class,
            "device": device,
        }
        if sensor.unit_of_measurement:
            payload["unit_of_measurement"] = sensor.unit_of_measurement
        if sensor.state_class:
            payload["state_class"] = sensor.state_class
        return payload

    def _entity_name(self, suffix: str) -> str:
        return f"{self.config.device_name.replace(' - ', ' ')} {suffix}"

    def _discovery_topic(self, object_id: str) -> str:
        return (
            f"{self.config.ha_discovery_prefix}/sensor/"
            f"{self.config.device_id}_{object_id}/config"
        )
