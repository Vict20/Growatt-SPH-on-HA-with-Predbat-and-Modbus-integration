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

## Background: the problem this solves

On a time-of-use tariff such as Octopus Intelligent or Agile, a home battery
earns its keep by **charging when import is cheap and exporting or covering the
house when it's expensive**. Doing that well means deciding, every few minutes,
when to charge, when to export and how full the battery should be. That decision
depends on prices, the solar forecast, house load and car charging. **Predbat**
makes that decision. The hard part with a Growatt SPH is getting the decision
**into the inverter reliably**.

The SPH doesn't take a simple "charge now" command. It runs from **time-of-use
slots** stored in its own registers:

- **Battery First** slots charge the battery (from the grid if allowed).
- **Grid First** slots discharge the battery to the house and grid (export).
- Outside any slot it runs in **Load First** (self-consumption).

Each slot is three registers: `[start time, end time, enable]`. The firmware
**rejects any write that would leave two slots overlapping** (Modbus exception
code 4). Normally Predbat updates a window as separate writes (start, then end).
Halfway through, the slot can briefly overlap another one, and the write is
refused. The schedule then silently isn't what Predbat planned.

So this setup puts a small layer between Predbat and the inverter:

1. Predbat writes its plan into **Home Assistant helpers**, never to the
   inverter's time registers directly.
2. **Automations** wait for Predbat to finish, then commit each window as **one
   atomic three-register write**. They clear a clashing slot first, but only
   when the windows really overlap.

That layer is the main thing this repo provides.

### Why local Modbus rather than the Growatt cloud

This system started on the Growatt cloud integration (`growatt_server`) and
moved to local Modbus. The cloud integration is no longer used. The reasons:

- **Speed.** Local reads take seconds, and writes land and can be read back
  straight away. Cloud data lags by minutes and writes go via Growatt's servers.
- **Reliability.** Control keeps working when the internet or Growatt's servers
  don't. A battery schedule shouldn't depend on someone else's server.
- **Control.** Local Modbus exposes the actual registers: every TOU slot, SOC
  limits, charge rates and the clock. Writes can be verified against the
  integration's log.

## The system this was built on

| Part | Detail |
|---|---|
| Inverter | Growatt SPH3600 (UK), two PV strings |
| Battery | 2 × Growatt GBLI6532 (6.5 kWh each, about 12 kWh usable) |
| Link | Elfin EW11 RS485 to Wi-Fi adapter (Modbus TCP, port 8899) on the inverter's RS485 port |
| HA | Home Assistant OS on an x86 mini PC |
| Optimiser | Predbat add-on, `inverter_type: GROWATTSPH` |
| Tariff | Octopus Intelligent import, Agile export |
| Forecast | Solcast (hobbyist account) |
| EVs | Ohme charger, plus a second EV on its own feed metered by an emonPi |

## What each part does, and why

### Integrations and add-ons

| Component | Role | Why it's needed |
|---|---|---|
| [Growatt_ModbusTCP](https://github.com/0xAHA/Growatt_ModbusTCP) by 0xAHA (Marketplace, formerly HACS; 2.0.7-b8 at the time of writing) | Talks Modbus to the inverter through the EW11. Provides sensors (`sensor.growatt_*`), SOC and rate number entities, and the `growatt_modbus.write_registers` action. | It's the only part that reads and writes the inverter. The **multi-register write action** is what makes atomic slot writes possible. It reads registers back after writing and skips writes that wouldn't change anything. |
| [Predbat](https://github.com/springfall2008/batpred) add-on | Plans charge, export and hold every 5 minutes from prices, solar forecast, load history and car charging. | It's the brain. It has a `GROWATTSPH` inverter profile, but this setup routes its window writes through helpers (see above). |
| Solcast (inside Predbat, via API key) | Solar forecast. | Predbat needs the forecast to decide how much to buy overnight. Polled every 4 h to stay within the free API quota. |
| Octopus (inside Predbat, via API key and account) | Import and export rates, Intelligent dispatch slots, saving sessions. | Predbat plans around actual prices, including extra cheap slots when the car charges. The separate Octopus Energy HA integration is also installed here but is optional. |
| Ohme (HA integration, plus Predbat's own Ohme login) | Car charging power and plan. | Predbat needs to know when the car takes energy. Otherwise it misreads car charging as house load, or plans the battery against it. |
| emoncms / emonPi (optional) | Meters the second EV feed, which doesn't pass through the Growatt's load measurement. | That car is invisible to the inverter, so it's measured separately. |
| ESPHome (optional) | Runs the desk display. | Shows at a glance what the battery and Predbat are doing. |

### Helpers (`homeassistant/helpers.yaml`)

| Helper | Why |
|---|---|
| `input_datetime.predbat_bf3_charge_start/_end` | Predbat writes its **charge window** here instead of straight to the inverter, so it can be committed atomically. |
| `input_datetime.predbat_gf3_discharge_start/_end` | Same for the **export window**. |
| `input_number.predbat_growatt_charge_rate_w` (and discharge) | Predbat sets rates in **watts**. The inverter wants a **percentage**. These hold the watt value until a bridge automation converts it. |
| `sensor.growatt_battery_power_signed_local` | Predbat needs **one signed** battery power value (+ discharging / − charging). The integration reports charge and discharge power separately. |
| `sensor.growatt_grid_power_signed_local` | Same for grid (+ export / − import). |
| Recorder excludes | Predbat's plan entities have very large attributes that bloat the HA database. |
| `growatt_modbus: debug` logging | Writes the `[WRITE_MULTI]` and `[WRITE VERIFY]` lines that `tools/check_growatt_writes.py` reads to prove writes landed. |

### Automations (`homeassistant/automations/`)

| Automation | What it does | Why |
|---|---|---|
| **Predbat GF3 export** and **Predbat BF3 charge** (`predbat_window_writers.yaml`) | Watch the window helpers. After a settle delay (8 s export, 16 s charge), work out the register values, clear the other slot if the windows really overlap, then write `[start, end, enable]` in **one** call. | The core of the setup: it makes Predbat's plan land reliably (see the background above). The details are below. |
| **Charge / discharge rate bridges** (`rate_bridges.yaml`) | Convert Predbat's W value to the inverter's % rate entity. | The units differ (W vs %). 100% = 3000 W on this battery; change it for yours. On this system only the **charge** bridge is used. The discharge rate is left at max on the inverter, and Predbat's discharge rate goes to a helper nothing passes on (see `helpers.yaml`). |
| **Weekly clock sync** (`clock_sync.yaml`) | Sets the inverter clock from HA every Sunday at 06:00. | The TOU slots run on the **inverter's** clock. If it drifts, windows start and stop at the wrong time. |
| **Low battery / Modbus lost / Modbus restored** (`notifications.yaml`) | Phone alerts. | If the Modbus link drops, Predbat is working from stale data and can't control the inverter, and you'd otherwise not know. |
| **Predbat status notification** (`notifications.yaml`) | Phone alert on each Predbat status change. Shows "Demand" as "Free Running". | Lets you see what the battery is doing. It replaces Predbat's built-in status notification (turned off in Predbat) to get the clearer wording. It can be chatty. |
| **Dusk capture** (`diagnostics_dusk_capture.yaml`, optional) | Read-only register snapshot every 3 min, 16:20-18:05, to the Logbook. | A diagnostic used to investigate dusk behaviour. Not needed for normal running. |

### Why the window writers are built the way they are

- **One write of three registers, never single registers.** A slot updated one
  register at a time can briefly overlap another slot and be rejected, or be
  left half-written.
- **Settle delay with `mode: restart`.** Predbat writes start and end separately,
  and sometimes revises them within seconds. Restarting the delay on each change
  means only the final window is written.
- **Different delays (8 s and 16 s).** When Predbat switches from charging to
  exporting, both automations fire. Staggering them means the two writes never
  hit the inverter at the same time.
- **Re-check after the delay.** The helpers are read again after waiting, in case
  they changed during the wait.
- **Disable the other slot only on a real overlap.** If the new window genuinely
  overlaps the opposing slot, that slot is written `[0, 0, 0]` first, then 2 s
  later the new window. Otherwise the opposing slot is left alone, which avoids
  needless writes.
- **Trim small overlaps.** Predbat often adds a few minutes' margin, so a charge
  window can end a few minutes after the export window starts. If the overlap is
  ≤ 5 min, the end is trimmed to just before the other window starts, rather
  than cancelling the other slot.
- **Start = end means "off".** Predbat clears a window by giving it equal start
  and end. The automation writes it as disabled, and skips the write if the slot
  already reads Disabled.
- **Slot 3 of each type.** Predbat uses Grid First 3 (registers 1086-1088) and
  Battery First 3 (1106-1108). That leaves slots 1-2 free for manual use in the
  Growatt app if ever needed.

SOC targets don't need this treatment. They're single registers that can't
overlap anything, so Predbat writes them straight to the integration's number
entities: `charge_limit` to register 1091 (charge stop SOC) and `reserve` to
register 1071 (discharge stop SOC). A "hold" is either one set to the current SOC.

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

See [docs/register-map.md](docs/register-map.md) for the register layout,
including the six extra time slots that newer firmware enables.

## What's in this repo

| Path | What it is |
|---|---|
| `homeassistant/helpers.yaml` | Helpers, template sensors, recorder and logger settings. Usable as a package. |
| `homeassistant/automations/predbat_window_writers.yaml` | **The core**: GF3 export and BF3 charge window writers. |
| `homeassistant/automations/rate_bridges.yaml` | W to % rate bridges. |
| `homeassistant/automations/clock_sync.yaml` | Weekly inverter clock sync. |
| `homeassistant/automations/notifications.yaml` | Low battery, Modbus lost/restored, Predbat status to phone. |
| `homeassistant/automations/diagnostics_dusk_capture.yaml` | Optional read-only register capture. |
| `predbat/apps.yaml` | Predbat config for GROWATTSPH with the helpers above (secrets removed). |
| `esphome/growatt-monitor.yaml` | Optional desk display on an ESP32 "Cheap Yellow Display". |
| `tools/check_growatt_writes.py` | Reads the HA log and lists every register write, landed or rejected. |
| `tools/check_predbat_writes.py` | Reads Predbat's log and lists what Predbat decided, to cross-check with the above. |
| `tools/extract_window.py` | Cuts a time window out of the Predbat log. |
| `docs/register-map.md` | TOU slot registers, time encoding, rules. |
| `docs/ew11-setup.md` | Elfin EW11 serial and socket settings, wiring notes. |

## Setup outline

1. **Modbus link.** Fit an RS485 to Modbus TCP adapter to the inverter's RS485
   port. This build uses an **Elfin EW11** on Wi-Fi, with the integration
   connecting to port 8899 (not the usual Modbus port 502, which didn't work
   here). Its full settings are in [docs/ew11-setup.md](docs/ew11-setup.md).
   Give the adapter a fixed IP (a DHCP reservation is fine) and check you can
   read registers before going further.
2. **Integration.** Install `0xAHA/Growatt_ModbusTCP` from the Marketplace
   (formerly HACS) and add your inverter. Leave VPP control authority **off** and
   protocol variant on **Auto**. Check your entity names in Developer Tools →
   States. This repo uses the integration's `sensor.growatt_<group>_<name>` pattern.
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
