# RD2HA Home Assistant Add-on

Runs a RainDirector / Tanklevels portal scraper as a Home Assistant OS add-on and publishes decoded readings back into Home Assistant via MQTT discovery.

## Install

1. Open Home Assistant.
2. Go to **Settings -> Add-ons -> Add-on Store**.
3. Open the top-right menu and choose **Repositories**.
4. Add this repository URL:

   ```text
   https://github.com/benpotts88/RD2HA-addon
   ```

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
```

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

For more than one Tanklevels page, populate `devices`. When `devices` is not empty, it overrides the single-device `tanklevels_device_url`, `device_name`, `device_id`, `device_type`, and `mqtt_base_topic` fields.

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
