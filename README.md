# RainPoint Smart+ — Home Assistant integration

HA custom component for **RainPoint Smart+** irrigation hardware (hub
`HWG023WRF` + 2-zone timer `HTV213FRF`/`HTV214FRF` + rain sensor
`HCS012ARF`). Uses the HomGar cloud REST API — no MQTT client, no Bluetooth.

Built on our fork of [Remboooo/homgarapi](https://github.com/Remboooo/homgarapi)
extended with:
- `refresh_token` (avoids re-logins that kick the account off its phones)
- `control_zone(hub, sub_addr, port, mode, duration)` for per-zone control
- Newer hex-TLV (`paramVersion>=16`) status decoder

## Exposed entities

Per home the integration creates:

- **Switch**: one per timer port (`switch.rainpoint_timer_<port>`). Turning on
  runs the port for the configured default duration (see Options); turning
  off cancels immediately.
- **Sensor**: per port — last cycle water usage (L), active duration, RF RSSI.
- **Sensor**: per hub — status / last seen.
- **Sensor**: per rain sensor — hourly / 24 h / 7 d rainfall (mm).
- **Binary sensor**: per zone — `running`.

## Install (manual / HACS custom repo)

1. Copy `custom_components/rainpoint/` into your HA `config/custom_components/`.
2. Restart HA.
3. Settings → Devices & Services → Add Integration → "RainPoint Smart+".
4. Use a **burner HomGar account that has been invited as a member** of your
   home — do NOT use your main account (each login kicks the previous
   session off its phone).

## Known limitations

- HTTP polling only (default 30 s idle, 5 s when a zone is running). No
  push — the app's push channel is MQTT at Aliyun IoT, cert-pinned.
- Durations under 60 s are accepted by the server but the official app
  enforces 60 s minimum to protect pump/valve hardware; we do the same.
- Only tested against the hardware in the dev's home (1× HWG023WRF + 1×
  HTV213FRF + 1× HCS012ARF). Other models should work if supported by the
  underlying `homgarapi` fork.
