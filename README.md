# Growatt SPH on Home Assistant with Predbat and local Modbus

A working setup for running a **Growatt SPH** hybrid inverter from
**[Predbat](https://github.com/springfall2008/batpred)** in Home Assistant,
controlled locally over **Modbus TCP**, with no Growatt cloud in the control
path.

This has run day to day since 10 October 2026 with every register write landing
(0 rejected across hundreds of writes). It's shared as a worked example, not as
a finished product. Read it, understand it, and adapt it to your system.

> ⚠️ **You're writing to your inverter's registers.** A wrong value can stop
> charging, cause unwanted export, or leave a slot enabled that you didn't
> intend. Test on a harmless time window first and watch the result in the
> Growatt app. No warranty.

## The system this was built on

| Part | Detail |
|---|---|
| Inverter | Growatt SPH3600 (UK), two PV strings |
| Battery | 2 × Growatt GBLI6532 (6.5 kWh each, about 12 kWh usable) |
| Link | Elfin EW11 RS485 to Wi-Fi adapter (Modbus TCP, port 8899) on the inverter's RS485 port |
| HA | Home Assistant OS on an x86 mini PC |
| Integration | [0xAHA/Growatt_ModbusTCP](https://github.com/0xAHA/Growatt_ModbusTCP) via HACS (2.0.7-b8 at the time of writing) |
| Optimiser | Predbat add-on, `inverter_type: GROWATTSPH` |
| Tariff | Octopus Intelligent import, Agile export |
| Forecast | Solcast (hobbyist account) |

## How control works

Predbat doesn't write the inverter's time slots directly. The SPH firmware
rejects any write that would make two slots overlap. If Predbat changed start
and end as separate writes, there would be moments where a slot was half
updated and overlapped another, so the write would be refused.

```
Predbat ──writes──> input_datetime helpers (4)
                         │  state change
                         ▼
             window-writer automation (settle delay, mode: restart)
                         │  one atomic write
                         ▼
     growatt_modbus.write_registers  [start, end, enable]
                         │
                         ▼
     Grid First 3 (1086-1088) = export  ·  Battery First 3 (1106-1108) = charge
```

- Predbat writes its charge window to `input_datetime.predbat_bf3_charge_start/_end`
  and its export window to `input_datetime.predbat_gf3_discharge_start/_end`.
- Each automation waits a few seconds so Predbat can finish changing both ends.
  The export (GF3) automation waits 8 s and the charge (BF3) automation 16 s, so
  the two never write at the same time. Each then commits the window as **one**
  `[start, end, enable]` write.
- If the new window really overlaps the opposing slot, the automation first
  disables the opposing slot with `[0, 0, 0]`, waits 2 s, then writes.
- If the windows only overlap by Predbat's few-minute margin (≤ 5 min), the
  end of the new window is trimmed instead, so the other slot stays as it is.
- Start equal to end (Predbat's way of saying "no window") is written as
  disabled. A disable is skipped if the slot already reads Disabled.
- Predbat's SOC targets go straight to number entities: `charge_limit` to
  register 1091 (charge stop SOC) and `reserve` to register 1071 (discharge stop SOC).
- Charge and discharge rates go through W→% bridge automations.

See [docs/register-map.md](docs/register-map.md) for the register layout,
including the six extra time slots that newer firmware enables.

## What's in this repo

| Path | What it is |
|---|---|
| `homeassistant/helpers.yaml` | The input_datetime and input_number helpers, signed power template sensors, recorder excludes and logger settings. Usable as a package. |
| `homeassistant/automations/predbat_window_writers.yaml` | **The core**: GF3 export and BF3 charge window writers. |
| `homeassistant/automations/rate_bridges.yaml` | Predbat W rates to the inverter's % rate entities. |
| `homeassistant/automations/clock_sync.yaml` | Weekly inverter clock sync. |
| `homeassistant/automations/notifications.yaml` | Low battery, Modbus lost/restored, Predbat status to phone. |
| `homeassistant/automations/diagnostics_dusk_capture.yaml` | Optional read-only register capture to the Logbook. |
| `predbat/apps.yaml` | Predbat config for GROWATTSPH with the helpers above (secrets removed). |
| `esphome/growatt-monitor.yaml` | Optional desk display on an ESP32 "Cheap Yellow Display". |
| `tools/check_growatt_writes.py` | Reads the HA log and lists every register write, landed or rejected. |
| `tools/check_predbat_writes.py` | Reads Predbat's log and lists what Predbat decided, to cross-check with the above. |
| `tools/extract_window.py` | Cuts a time window out of the Predbat log. |
| `docs/register-map.md` | TOU slot registers, time encoding, rules. |
| `docs/ew11-setup.md` | Elfin EW11 serial and socket settings. |

## Setup outline

1. **Modbus link.** Fit an RS485 to Modbus TCP adapter to the inverter's RS485
   port. This build uses an **Elfin EW11** on Wi-Fi, with the integration
   connecting to port 8899. Its full settings are in
   [docs/ew11-setup.md](docs/ew11-setup.md). Give the adapter a fixed IP (a DHCP
   reservation is fine) and check you can read registers before going further.
2. **Integration.** Install `0xAHA/Growatt_ModbusTCP` from HACS and add your
   inverter. Leave VPP control authority **off** and protocol variant on **Auto**.
   Check your entity names in Developer Tools → States. This repo uses the
   integration's `sensor.growatt_<group>_<name>` pattern.
3. **Helpers.** Add `homeassistant/helpers.yaml` as a package (or create the
   same helpers in the UI). Restart HA.
4. **Automations.** Copy the automations in, and replace
   `YOUR_GROWATT_DEVICE_ID` with your inverter's device ID (Settings → Devices →
   your inverter, the ID in the URL) and `notify.mobile_app_your_phone` with
   your phone's notify service.
5. **Test before Predbat.** Set a helper pair by hand to a harmless window, in
   **one** `input_datetime.set_datetime` call per helper. Check the automation
   trace, the HA log (`[WRITE_MULTI]` and `[WRITE VERIFY]` lines) and the
   Growatt app. Then set start = end to disable it again.
6. **Predbat.** Install the add-on, copy `predbat/apps.yaml` across, fill in
   your keys (`!secret`), Octopus account, Solcast site and battery size, then
   start it in read-only mode first and check the plan before letting it control.
7. **Verify.** After a night, download the HA log and run
   `python tools/check_growatt_writes.py home-assistant.log`, then
   `python tools/check_predbat_writes.py predbat.log`. As a rule, each Predbat
   decision should match one register write a few seconds later, plus the
   disable-first writes. Expect 0 rejected.

## Gotchas

- **Predbat never clears a window at its end.** Stale slots stay enabled until
  the next change. That's harmless in normal use, because the next write from
  the opposing automation clears any overlap.
- **Predbat changes its mind.** Marginal exports and charge holds can switch on
  and off every 5-minute cycle. Each change is a real register write. That's
  fine as long as none are rejected.
- **Don't edit `number.growatt_battery_discharge_stopped_soc` or
  `..._charge_stopped_soc` by hand.** Predbat rewrites them every cycle.
- **Set helpers in one call.** Writing an identical value doesn't trigger
  the automation.
- **Entity IDs:** a stray leading space in an entity ID silently breaks
  conditions.
- **Debug log is big** (about 4,000 lines an hour). The HA log resets on every
  Core restart, so download it before restarting if you want to check writes.
- After an HA Core restart, check `predbat.status` and restart the Predbat
  add-on if it isn't healthy.
- Leave `charge_time_format: "S"` alone unless you know what it changes.
- **Keep secrets out of anything you post.** That means serial numbers (register
  scans contain them), the inverter's IP, Octopus account number, Solcast site
  ID and HA device IDs.

## Credits

- [Predbat](https://github.com/springfall2008/batpred) by springfall2008 and contributors.
- [Growatt_ModbusTCP](https://github.com/0xAHA/Growatt_ModbusTCP) by 0xAHA, who
  fixed the new-slot register layout after this setup reported it.
- ESPHome display based on
  [CYD-ESPHome-HA-Monitor](https://github.com/element-software/CYD-ESPHome-HA-Monitor).
