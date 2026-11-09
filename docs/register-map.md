# Growatt SPH time-of-use register map

Holding registers, as found on a UK SPH3600 after Growatt's remote firmware
update of 22 Oct 2026. Check on your own inverter before writing anything.

## Time encoding

Times are packed as `hour * 256 + minute`.

| Time | Value |
|---|---|
| 00:00 | 0 |
| 05:30 | 1310 |
| 23:30 | 5918 |
| 23:59 | 5947 |

Each slot is three consecutive registers: `[start, end, enable]` (enable 1 = on, 0 = off).

## Slots

| Slot | Start | End | Enable | Notes |
|---|---|---|---|---|
| Grid First 1 | 1080 | 1081 | 1082 | |
| Grid First 2 | 1083 | 1084 | 1085 | |
| Grid First 3 | 1086 | 1087 | 1088 | Predbat export window (this repo) |
| Battery First 1 | 1100 | 1101 | 1102 | |
| Battery First 2 | 1103 | 1104 | 1105 | |
| Battery First 3 | 1106 | 1107 | 1108 | Predbat charge window (this repo) |
| Battery First 4 | 1018 | 1019 | 1020 | new slots, need recent firmware |
| Battery First 5 | 1021 | 1022 | 1023 | |
| Battery First 6 | 1024 | 1025 | 1026 | |
| Grid First 4 | 1027 | 1028 | 1029 | |
| Grid First 5 | 1030 | 1031 | 1032 | |
| Grid First 6 | 1033 | 1034 | 1035 | |

- Older documents (Growatt Modbus protocol V1.39, SPH-HUB manual) put the new
  slots at **1017+3n** and **1026+3n**. On this firmware they are
  **1018+3n** and **1027+3n**. H1017 reads 0 and is unused.
- Confirmed by register scan: BF4 enable = 1020 and GF4 enable = 1029. The rest
  follow the pattern.

## Other registers used

| Register | Meaning | Written by |
|---|---|---|
| 1070 | Discharge power rate (%) | rate bridge |
| 1071 | Discharge stop SOC (%) | Predbat `reserve` |
| 1090 | Charge power rate (%) | rate bridge |
| 1091 | Charge stop SOC (%) | Predbat `charge_limit` / `target_soc` |
| 45-50 | Clock: year, month, day, hour, minute, second | weekly clock sync |

A Predbat "hold" shows as 1071 or 1091 set to the current SOC.

## Rules learned the hard way

- **Always write a full `[start, end, enable]` triple** in one
  `growatt_modbus.write_registers` call. Never write a single slot register.
- The firmware **rejects overlapping slots** with Modbus `exception_code=4`. A
  rejected triple doesn't partly apply. The registers stay as they were.
- Don't touch **H1008** (VPP / control authority). Leave VPP control authority
  off and the integration's protocol variant on Auto.
- Register scans contain the **serial number** (H23-H27, 30001-30005 and the
  32-bit value at H22/30000). Redact before sharing.
