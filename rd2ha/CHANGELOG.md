# 0.4.0

- Keep accepted measurements visible when portal data is stale or a later read fails.
- Add Data stale, Portal connection, and Timestamp problem diagnostics per device.
- Restore accepted readings from a private local cache after restarts.
- Update freshness every minute independently of slow portal reads.
- Restore a complete MQTT snapshot before marking the bridge online on reconnect.
- Preserve existing measurement identities, topics, units, and session recovery.

Automations that previously used unavailable measurements to detect stale data
must now check Data stale. Back up before updating; see README for verification
and rollback steps.

# 0.3.1

- Recover automatically when a saved Tanklevels session no longer loads readings.
- Detect login redirects throughout device-page loading.
- Save a replacement session only after a successful page read.
- Bound recovery to one fresh login per device attempt; preserve stale-reading checks.
