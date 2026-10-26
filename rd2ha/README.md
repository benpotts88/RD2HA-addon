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

RD2HA retains the latest state, but only marks a device online when the portal
timestamp is fresh. Configure `max_reading_age_seconds` (default `3600`) and
`max_future_skew_seconds` (default `300`) to control that check. A retained
bridge MQTT Last Will also marks all entities unavailable after a crash or
broker connection loss.

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
