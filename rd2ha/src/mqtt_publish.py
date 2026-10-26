from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import logging
import threading
from typing import Any

import paho.mqtt.client as mqtt

from .config import Config
from .models import PortalDevice, PortalReading

LOGGER = logging.getLogger(__name__)


class MqttPublishError(RuntimeError):
    pass


class MqttConnectError(MqttPublishError):
    pass


@dataclass(frozen=True)
class SensorDiscovery:
    object_id: str
    name_suffix: str
    value_template: str
    device_class: str
    unit_of_measurement: str | None = None
    state_class: str | None = None


TANK_LEVEL_SENSORS = (
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

RAIN_DIRECTOR_SENSORS = (
    SensorDiscovery(
        object_id="header_tank",
        name_suffix="Header Tank",
        value_template="{{ value_json.header_tank_percent }}",
        unit_of_measurement="%",
        device_class="moisture",
        state_class="measurement",
    ),
    SensorDiscovery(
        object_id="rainwater_tank_air_temp",
        name_suffix="Rainwater Tank Air Temp",
        value_template="{{ value_json.rainwater_tank_air_temp_c }}",
        unit_of_measurement="°C",
        device_class="temperature",
        state_class="measurement",
    ),
    SensorDiscovery(
        object_id="mains_water_usage",
        name_suffix="Mains Water Usage",
        value_template="{{ value_json.mains_water_usage_l }}",
        unit_of_measurement="L",
        device_class="water",
        state_class="total_increasing",
    ),
    SensorDiscovery(
        object_id="rainwater_usage",
        name_suffix="Rainwater Usage",
        value_template="{{ value_json.rainwater_usage_l }}",
        unit_of_measurement="L",
        device_class="water",
        state_class="total_increasing",
    ),
    SensorDiscovery(
        object_id="last_update",
        name_suffix="Last Update",
        value_template="{{ value_json.last_update }}",
        device_class="timestamp",
    ),
)

SENSORS_BY_DEVICE_TYPE = {
    "tank_level": TANK_LEVEL_SENSORS,
    "rain_director": RAIN_DIRECTOR_SENSORS,
}

MODEL_BY_DEVICE_TYPE = {
    "tank_level": "Tank acculevel",
    "rain_director": "Rain director",
}


class MqttPublisher:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._connection_event = threading.Event()
        self._connection_error: str | None = None
        self._connected = False
        self._loop_started = False
        self.connection_generation = 0
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=config.mqtt_client_id,
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.will_set(
            config.mqtt_bridge_availability_topic,
            payload="offline",
            qos=1,
            retain=True,
        )
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        if config.mqtt_username or config.mqtt_password:
            self.client.username_pw_set(
                username=config.mqtt_username,
                password=config.mqtt_password,
            )

    def __enter__(self) -> "MqttPublisher":
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            self.publish_shutdown_availability()
        except MqttPublishError:
            LOGGER.exception("step=mqtt_shutdown_availability_failed")
        finally:
            self.disconnect()

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        del client, userdata, flags, properties
        if reason_code == 0:
            self._connection_error = None
            self.connection_generation += 1
            self._connected = True
            LOGGER.info("step=mqtt_connected accepted=true")
        else:
            self._connected = False
            self._connection_error = str(reason_code)
            LOGGER.error("step=mqtt_connected accepted=false reason=%s", reason_code)
        self._connection_event.set()

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        del client, userdata, flags, properties
        self._connected = False
        if reason_code != 0:
            LOGGER.warning("step=mqtt_disconnected unexpected=true reason=%s", reason_code)

    def connect(self) -> None:
        LOGGER.info(
            "step=mqtt_connect host=%s port=%s",
            self.config.mqtt_host,
            self.config.mqtt_port,
        )
        self._connection_event.clear()
        self._connection_error = None
        try:
            result = self.client.connect(
                self.config.mqtt_host,
                self.config.mqtt_port,
                keepalive=60,
            )
        except OSError as exc:
            raise MqttConnectError(f"MQTT socket connection failed: {exc}") from exc
        if result != mqtt.MQTT_ERR_SUCCESS:
            raise MqttConnectError(f"MQTT connect failed with result code {result}")

        loop_result = self.client.loop_start()
        if loop_result != mqtt.MQTT_ERR_SUCCESS:
            self.client.disconnect()
            raise MqttConnectError(
                f"MQTT network loop failed with result code {loop_result}"
            )
        self._loop_started = True
        if not self._connection_event.wait(self.config.mqtt_connect_timeout_seconds):
            self.disconnect()
            raise MqttConnectError("Timed out waiting for MQTT broker acceptance")
        if not self._connected:
            reason = self._connection_error or "unknown reason"
            self.disconnect()
            raise MqttConnectError(f"MQTT broker rejected connection: {reason}")

    def disconnect(self) -> None:
        if self._loop_started:
            try:
                self.client.disconnect()
            except Exception:
                LOGGER.exception("step=mqtt_disconnect_failed")
            try:
                self.client.loop_stop()
            except Exception:
                LOGGER.exception("step=mqtt_loop_stop_failed")
            self._loop_started = False
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def publish_bridge_availability(self, online: bool) -> None:
        self._publish(self.config.mqtt_bridge_availability_topic,
                      "online" if online else "offline", retain=True)

    def publish_shutdown_availability(self) -> None:
        if self.connected:
            self.publish_bridge_availability(False)

    def publish_discovery(self) -> None:
        for topic, payload in self.discovery_payloads().items():
            self._publish(topic, payload, retain=True)
        LOGGER.info("step=mqtt_discovery published=true devices=%s", len(self.config.devices))

    def publish_state(self, device: PortalDevice, reading: PortalReading) -> None:
        self._publish(device.state_topic, reading.to_payload(), retain=True)
        LOGGER.info("step=mqtt_state published=true device_id=%s", device.device_id)

    def publish_reading_availability(self, device: PortalDevice, online: bool) -> None:
        self._publish(
            device.availability_topic,
            "online" if online else "offline",
            retain=True,
        )

    def publish_status(self, device: PortalDevice, payload: Mapping[str, object]) -> None:
        self._publish(device.status_topic, payload, retain=True)

    def _publish(self, topic: str, payload: Mapping[str, object] | str, *, retain: bool) -> None:
        encoded_payload = (
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            if isinstance(payload, Mapping)
            else payload
        )
        if not self.connected:
            raise MqttPublishError("MQTT is disconnected")
        generation = self.connection_generation
        info = self.client.publish(topic, encoded_payload, qos=1, retain=retain)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise MqttPublishError(f"MQTT publish failed for {topic}: result code {info.rc}")
        try:
            info.wait_for_publish(timeout=15)
        except (RuntimeError, ValueError) as exc:
            raise MqttPublishError(f"MQTT publish failed for {topic}: {exc}") from exc
        if not self.connected or self.connection_generation != generation:
            raise MqttPublishError("MQTT connection changed during publication")
        if not info.is_published():
            raise MqttPublishError(f"MQTT publish timed out for {topic}")

    def discovery_payloads(self) -> dict[str, dict[str, object]]:
        payloads: dict[str, dict[str, object]] = {}
        for device in self.config.devices:
            payloads.update(self.discovery_payloads_for_device(device))
        return payloads

    def discovery_payloads_for_device(
        self,
        device: PortalDevice,
    ) -> dict[str, dict[str, object]]:
        device_metadata = {
            "identifiers": [device.device_id],
            "name": device.device_name,
            "manufacturer": "Black Box Controls",
            "model": MODEL_BY_DEVICE_TYPE[device.device_type],
        }

        payloads = {
            self._discovery_topic(device, sensor.object_id): self._sensor_payload(
                sensor,
                device,
                device_metadata,
            )
            for sensor in SENSORS_BY_DEVICE_TYPE[device.device_type]
        }

        for suffix, name, field, device_class in (
            ("data_stale", "Data stale", "data_stale", "problem"),
            ("portal_connection", "Portal connection", "portal_connected", "connectivity"),
            ("timestamp_problem", "Timestamp problem", "timestamp_problem", "problem"),
        ):
            topic = f"{self.config.ha_discovery_prefix}/binary_sensor/{device.device_id}_{suffix}/config"
            payloads[topic] = {
                "name": self._entity_name(device, name),
                "unique_id": f"{device.device_id}_{suffix}",
                "device": device_metadata,
                "device_class": device_class,
                "entity_category": "diagnostic",
                "enabled_by_default": True,
                "state_topic": device.status_topic,
                "value_template": ("{{ 'None' if value_json." + field
                                   + " is none else ('ON' if value_json." + field + " else 'OFF') }}"),
                "payload_on": "ON", "payload_off": "OFF",
                "availability_topic": self.config.mqtt_bridge_availability_topic,
                "payload_available": "online", "payload_not_available": "offline",
                "json_attributes_topic": device.status_topic,
                "json_attributes_template": (
                    "{{ dict(last_update=value_json.last_update, "
                    "last_attempt_at=value_json.last_attempt_at, "
                    "last_successful_scrape_at=value_json.last_successful_scrape_at, "
                    "reading_age_seconds=value_json.reading_age_seconds, "
                    "max_reading_age_seconds=value_json.max_reading_age_seconds, "
                    "error_code=value_json.error_code) | tojson }}"
                ),
                "qos": 1,
            }
        return payloads

    def _sensor_payload(
        self,
        sensor: SensorDiscovery,
        device: PortalDevice,
        device_metadata: dict[str, object],
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self._entity_name(device, sensor.name_suffix),
            "unique_id": f"{device.device_id}_{sensor.object_id}",
            "state_topic": device.state_topic,
            "availability": [
                {"topic": self.config.mqtt_bridge_availability_topic},
                {"topic": device.availability_topic},
            ],
            "availability_mode": "all",
            "payload_available": "online",
            "payload_not_available": "offline",
            "value_template": sensor.value_template,
            "device_class": sensor.device_class,
            "device": device_metadata,
        }
        if sensor.unit_of_measurement:
            payload["unit_of_measurement"] = sensor.unit_of_measurement
        if sensor.state_class:
            payload["state_class"] = sensor.state_class
        return payload

    def _entity_name(self, device: PortalDevice, suffix: str) -> str:
        return f"{device.device_name.replace(' - ', ' ')} {suffix}"

    def _discovery_topic(self, device: PortalDevice, object_id: str) -> str:
        return (
            f"{self.config.ha_discovery_prefix}/sensor/"
            f"{device.device_id}_{object_id}/config"
        )
