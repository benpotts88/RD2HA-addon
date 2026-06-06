# RD2HA Add-on Repository

Public Home Assistant OS add-on repository for RD2HA.

RD2HA logs in to configured Tanklevels device pages, reads rendered values with Playwright, and publishes them to Home Assistant via MQTT discovery.

## Install In Home Assistant OS

1. Open Home Assistant.
2. Go to **Settings -> Add-ons -> Add-on Store**.
3. Open the top-right menu and choose **Repositories**.
4. Add this repository URL:

   ```text
   https://github.com/benpotts88/RD2HA-addon
   ```

5. Install **RD2HA**.
6. Configure the add-on options with your own Tanklevels and MQTT details.
7. Start the add-on.

The add-on supports:

- `tank_level` device pages.
- `rain_director` device pages.
- Multiple devices through the `devices` list in the add-on configuration.

## Privacy

This repository intentionally contains only generic placeholders. It does not include device URLs, locations, credentials, tank names, postcodes, saved browser state, or Home Assistant-specific secrets.
