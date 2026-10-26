# RD2HA Home Assistant Add-on

Runs a RainDirector / Tanklevels portal scraper as a Home Assistant OS add-on and publishes decoded readings back into Home Assistant via MQTT discovery.

## Install

1. Open Home Assistant.
2. Go to **Settings -> Add-ons -> Add-on Store**.
3. Open the top-right menu and choose **Repositories**.
4. Add the RD2HA add-on repository URL.
5. Install **RD2HA**.

## Configuration

Set the add-on options with your own values:

- `tanklevels_email`
- `tanklevels_password`
- `mqtt_username`
- `mqtt_password`

For Home Assistant OS with the Mosquitto broker add-on, this default is usually correct:

```yaml
mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_bridge_availability_topic: raindirector/bridge/availability
mqtt_connect_timeout_seconds: "15"
```

Set `mqtt_client_id` and `mqtt_bridge_availability_topic` to unique values if
you run more than one RD2HA installation with overlapping device IDs.

### Single Device

For one tank-level device, configure:

```yaml
tanklevels_device_url: https://tanklevels.co.uk/devices/CHANGE_ME
device_name: Tank
device_id: tank
device_type: tank_level
mqtt_base_topic: raindirector/tank
```

Supported `device_type` values:

- `tank_level`
- `rain_director`

Use a stable `device_id`, because Home Assistant uses it in MQTT discovery unique IDs. If you change it later, Home Assistant may create new entities.

### Multiple Devices

For more than one Tanklevels page, populate `devices`. On a fresh multi-device setup where `tanklevels_device_url` is still the placeholder, `devices` defines the complete device list.

For upgrades from a single-device setup, RD2HA keeps the configured single-device tank active automatically when `tanklevels_device_url` is a real Tanklevels URL. It only skips that legacy device when a `devices` entry already uses the same `id`, URL, or `base_topic`.

```yaml
devices:
  - id: tank
    name: Tank
    url: https://tanklevels.co.uk/devices/CHANGE_ME
    type: tank_level
    base_topic: raindirector/tank
  - id: rain_director
    name: Rain Director
    url: https://tanklevels.co.uk/devices/CHANGE_ME
    type: rain_director
    base_topic: raindirector/rain_director
```

Each device needs a unique `id` and `base_topic`. If `base_topic` is omitted, RD2HA uses `raindirector/{id}`.

### Render Stability

RD2HA waits until a rendered page parses successfully and the parsed values stop changing before it publishes MQTT state. This avoids grabbing a partially populated device view.

```yaml
page_min_ready_seconds: "12"
page_stable_seconds: "1.5"
page_stable_sample_interval_ms: 500
```

`page_min_ready_seconds` prevents RD2HA from accepting early complete-looking values while portal counters are still waiting to populate. Increase `page_stable_seconds` if a device page still changes after the minimum ready window. `page_stable_sample_interval_ms` controls how often RD2HA samples the page while waiting.

RD2HA retains accepted readings even when they are stale or a later portal read
fails. `max_reading_age_seconds` (default `3600`) controls the Data stale
diagnostic. `max_future_skew_seconds` (default `300`) rejects excessively future
timestamps. The bridge MQTT Last Will makes all entities unavailable after a
crash or broker connection loss.

### Auth State

The add-on stores the Playwright login session at:

```text
/config/storage_state.json
```

That path maps to the add-on-specific `addon_config` folder and survives restarts.

## Run

Start the add-on and watch the logs. A successful cycle includes:

```text
step=mqtt_discovery published=true
step=mqtt_state published=true device_id=...
step=device_cycle_complete device_id=...
```

The add-on publishes Home Assistant MQTT discovery configs for:

- `tank_level`: tank level, tank temperature, and last update.
- `rain_director`: header tank level, rainwater tank air temperature, total mains water usage, total rainwater usage, and last update.

### Automatic session recovery

RD2HA detects login redirects while waiting for readings. If a saved session
requires login or repeatedly fails to load a complete device reading, it tries
one fresh login automatically. It saves the replacement session after a
successful page read. A fresh-session failure is reported normally, without
looping. Stale readings do not trigger reauthentication.


## Data freshness and diagnostics (0.4.0)

Measurements keep the last accepted portal values during stale-data conditions
and portal failures. Existing entity IDs, units, and history are preserved.
Each device gains three enabled diagnostic binary sensors:

| Entity | Meaning |
| --- | --- |
| Data stale | Problem when the accepted device timestamp is more than `max_reading_age_seconds` old (default one hour). Unknown until an accepted reading exists. |
| Portal connection | Outcome of the latest completed device read; connected even when the portal reading is stale. Unknown until the first attempt completes after startup. |
| Timestamp problem | Problem when a page supplies an invalid or excessively future timestamp. Invalid readings never replace accepted values. Unknown until a timestamp can be assessed after startup. |

A portal failure preserves the previous timestamp-problem assessment. A valid
subsequent scrape clears it. The existing Last Update entity always represents
the physical device's timestamp, never the time RD2HA scraped the page.
Freshness is checked every 60 seconds, including while a slow scrape is in
progress. Polling remains sequential with the configured interval between rounds.
All entities become unavailable while the RD2HA bridge is offline.

Diagnostics use retained JSON at `<base_topic>/status`, with fields
`data_stale`, `portal_connected`, `timestamp_problem`, `last_update`,
`last_attempt_at`, `last_successful_scrape_at`, `reading_age_seconds`,
`max_reading_age_seconds`, and `error_code`. Boolean nulls mean unknown.
Error codes are `authentication`, `page_loading`, `incomplete_page`,
`invalid_timestamp`, or `future_timestamp`; raw exception details are not sent
to MQTT. The timestamps, age, threshold, and error code are diagnostic attributes.
The existing `<base_topic>/availability` now indicates that an accepted reading
exists; it no longer represents freshness or the last portal attempt.

### Restart cache

Accepted readings are stored atomically with private file permissions in
`readings_cache.json`, beside the saved login file. This is
`/config/readings_cache.json` inside the Home Assistant add-on, or
`data/readings_cache.json` with the default local/Docker configuration.
The file may include tank location data; it is ignored by Git.

On restart, cached values are restored and freshness is recalculated before the
bridge is marked online. Portal and timestamp diagnostics initially show unknown
until a new read completes. Without a usable cache, measurements remain
unavailable until the first accepted reading; stale retained broker messages do
not bypass this check. Changing a device URL, type, ID, or MQTT base topic
invalidates its cached entry. Renaming its display name does not. Resetting the
login session leaves this cache intact. Cache failures are logged and do not
prevent live publishing.

### Automations and upgrade checks

**Behaviour change:** automations that used measurement `unavailable` to detect
staleness must now check Data stale. Before using a measurement to control a pump
or valve, require a value, Data stale off, and Timestamp problem off. Unknown
diagnostic states should fail these conditions. Example condition block (replace
the entity IDs with those actually assigned by Home Assistant):

```yaml
conditions:
  - condition: template
    value_template: >-
      {{ has_value('sensor.outdoor_tank_level')
         and is_state('binary_sensor.outdoor_tank_data_stale', 'off')
         and is_state('binary_sensor.outdoor_tank_timestamp_problem', 'off') }}
```

Take an add-on backup before upgrading. After installing 0.4.0, verify:

1. Existing measurement entities and history remain intact.
2. A stale tank shows its last values and Data stale on.
3. A recent tank shows Data stale off; successful reads show Portal connection connected.
4. Restarting the add-on restores cached readings before scraping completes.
5. Stopping the add-on makes all its entities unavailable.

### Rollback to 0.3.1

Restore the pre-upgrade add-on backup. The older version returns to marking
stale readings unavailable. Preserve `readings_cache.json` for a later upgrade
if restoring a backup would overwrite the add-on configuration directory.

Stop RD2HA before removing the new diagnostic discovery messages. In Home
Assistant's MQTT publish tool, publish an empty payload with **retain enabled**
to each of these three topics for each configured device (substitute the actual
discovery prefix and device ID):

```text
homeassistant/binary_sensor/<device_id>_data_stale/config
homeassistant/binary_sensor/<device_id>_portal_connection/config
homeassistant/binary_sensor/<device_id>_timestamp_problem/config
```

This removes only the new diagnostic entities. Do not clear existing measurement
configurations or state topics. Then start the older add-on.
