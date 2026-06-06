# RD2HA Home Assistant Add-on

Runs a RainDirector / Tanklevels portal scraper as a Home Assistant OS add-on and publishes decoded tank readings back into Home Assistant via MQTT discovery.

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
- `tanklevels_device_url`
- `mqtt_username`
- `mqtt_password`
- `device_name`
- `device_id`

`device_name` is used for Home Assistant MQTT discovery display names. It does not need to exactly match the Tanklevels page title.

For Home Assistant OS with the Mosquitto broker add-on, this default is usually correct:

```yaml
mqtt_host: core-mosquitto
mqtt_port: 1883
```

Use a stable `device_id`, because Home Assistant uses it in MQTT discovery unique IDs. If you change it later, Home Assistant may create new entities.

The add-on stores the Playwright login session at:

```text
/config/storage_state.json
```

That path maps to the add-on-specific `addon_config` folder and survives restarts.

## Run

Start the add-on and watch the logs. A successful cycle includes:

```text
step=mqtt_discovery published=true
step=mqtt_state published=true
step=cycle_complete ...
```

The add-on publishes Home Assistant MQTT discovery configs for:

- Tank level
- Tank temperature
- Last update timestamp
