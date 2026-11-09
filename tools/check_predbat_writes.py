#!/usr/bin/env python3
"""
check_predbat_writes.py

Companion to check_growatt_writes.py. Parses a Predbat log file and
produces a chronological summary of every charge/discharge time value
Predbat wrote to its configured entities, plus any genuine write failures.

check_growatt_writes.py shows what actually landed on the inverter's
registers; this script shows what Predbat itself decided. Comparing the two
timelines over the same period confirms nothing was missed or invented
between Predbat's decision and the physical register.

------------------------------------------------------------------------
USAGE
------------------------------------------------------------------------
    python3 check_predbat_writes.py path/to/predbat.log
    python3 check_predbat_writes.py path/to/predbat.log --list-fields
    python3 check_predbat_writes.py path/to/predbat.log --soc-fields a,b,c

Standard library only. Use Predbat's own log (the add-on log), not the
Home Assistant core log.

BATTERY SOC / HOLD WRITES (added)
    A second section lists writes to the SOC fields (default: charge_limit,
    reserve, discharge_target_soc). A charge_limit written at or near the
    current SOC is how Predbat implements a "hold". Only CHANGES are listed
    (a repeat of the same value is counted, not printed), because Predbat
    rewrites some of these every cycle. The existing timeline, the
    "write intention(s)" count and the cross-check against the register log
    are unchanged: SOC writes are NOT included in that count.

    Mapping confirmed on a real log (29-30 Oct 2026, GROWATTSPH):
        charge_limit -> register 1091 (charge stop SOC)
        reserve      -> register 1071 (discharge stop SOC)
    All 52 changes matched the Growatt log one-to-one, same order, same
    values. Predbat's own timestamp is 5-14 s later than the register write.
    --list-fields  prints every field Predbat logged a "Wrote ... successfully"
                   for (with counts and last value). Use it first if the SOC
                   section is empty, to see what the field is really called.
    --soc-fields   overrides the SOC field names (comma separated).

------------------------------------------------------------------------
LOG FORMATS RECOGNISED
------------------------------------------------------------------------
1. Predbat 9.3.7+ (entities such as input_datetime helpers):
       "Inverter 0 Wrote discharge_start_time to 17:00:00 successfully"
   Written once per change to the real configured entity. Tracked fields:
   charge_start_time, charge_end_time, discharge_start_time,
   discharge_end_time.

2. Older write_and_poll format (kept so older logs still parse):
       "Inverter 0 write_and_poll_value: Wrote HH:MM:SS to <field>,
        successfully now HH:MM:SS"

IGNORED ON PURPOSE
   idle_start_time / idle_end_time. Predbat computes a "demand (idle)"
   window (the gap outside charge/export) and logs it as
   write_and_poll_value lines. These are not the charge/discharge helper
   writes, and they churn every cycle, so they are not listed here.

------------------------------------------------------------------------
CROSS-CHECKING AGAINST THE REGISTER LOG
------------------------------------------------------------------------
- Each Predbat write here should be followed by a register write roughly
  8-17s later (your automation settle delays).
- If Predbat revises the same field several times in quick succession, only
  the last value before the settle delay expires should reach a register.
- Predbat "disables" a window by writing 23:59:00 to both start and end.
  Those appear here as 23:59:00 writes and as "[disabled]" in the register
  log. Extra disable-first writes in the register log have no Predbat
  counterpart; that is expected.
------------------------------------------------------------------------
"""

import re
import sys


TRACKED_FIELDS = (
    "charge_start_time",
    "charge_end_time",
    "discharge_start_time",
    "discharge_end_time",
)

# SOC / hold fields. Edit here, or pass --soc-fields. Run --list-fields on a
# real log to see which of these Predbat actually writes on your setup.
SOC_FIELDS = (
    "charge_limit",
    "reserve",
    "discharge_target_soc",
)

# Predbat 9.3.7+ format.
NEW_WRITE = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2} [\d:.]+): Inverter (?P<inv>\d+) Wrote "
    r"(?P<field>\w+) to (?P<value>[\d:.]+) successfully"
)

# Older write_and_poll format.
OLD_WRITE = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2} [\d:.]+): Inverter (?P<inv>\d+) write_and_poll_value: "
    r"Wrote (?P<value>[\d:.]+) to (?P<field>\w+), successfully now"
)

# Genuine failures on the tracked fields only. Routine "No entity_id for
# scheduled_charge_enable / inverter_mode" warnings are expected for this
# inverter profile and deliberately not flagged.
ERROR = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2} [\d:.]+):.*"
    r"(?:Failed to write|write failed|write_and_poll_value:.*Failed"
    r"|No entity_id for (?:charge|discharge)_(?:start|end)_time)",
    re.IGNORECASE,
)


def main(log_path, soc_fields=SOC_FIELDS, list_fields=False):
    writes = []
    soc_writes = []
    field_seen = {}
    errors = []

    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = NEW_WRITE.search(line) or OLD_WRITE.search(line)
                if m:
                    d = m.groupdict()
                    seen = field_seen.setdefault(d["field"], [0, None])
                    seen[0] += 1
                    seen[1] = d["value"]
                    if d["field"] in TRACKED_FIELDS:
                        writes.append(d)
                        continue
                    if d["field"] in soc_fields:
                        soc_writes.append(d)
                        continue
                if ERROR.search(line):
                    errors.append(line.strip())
    except FileNotFoundError:
        print(f"File not found: {log_path}")
        sys.exit(1)

    if list_fields:
        print("=" * 70)
        print("FIELDS PREDBAT LOGGED A SUCCESSFUL WRITE FOR")
        print("=" * 70)
        if not field_seen:
            print("None found. Check this is Predbat's own log.")
        for name in sorted(field_seen):
            count, last = field_seen[name]
            tag = ""
            if name in TRACKED_FIELDS:
                tag = "  [time field, in the timeline]"
            elif name in soc_fields:
                tag = "  [SOC field, in the SOC section]"
            print(f"{name:28s} {count:5d} write(s), last value {last}{tag}")
        print()

    print("=" * 70)
    print("PREDBAT WRITE INTENTION TIMELINE")
    print("=" * 70)

    if not writes:
        print("No tracked write intentions found in this log.")
        print("(If you expected some, check the log covers the period and that")
        print(" lines like 'Inverter 0 Wrote discharge_start_time to ...' exist.)")
    else:
        for w in writes:
            tag = "  [disable]" if w["value"] == "23:59:00" else ""
            print(f"{w['ts']}  {w['field']:22s} ->  {w['value']}{tag}")

    print()
    print("=" * 70)
    print("BATTERY SOC / HOLD WRITES (changes only)")
    print("=" * 70)

    changes = 0
    repeats = 0
    if not soc_writes:
        print("None found for: " + ", ".join(soc_fields))
        print("(Run with --list-fields to see which fields Predbat does write.)")
    else:
        last = {}
        for w in soc_writes:
            key = (w["inv"], w["field"])
            if last.get(key) == w["value"]:
                repeats += 1
                continue
            before = last.get(key)
            last[key] = w["value"]
            changes += 1
            arrow = f"{before} -> {w['value']}" if before is not None else f"(first seen) {w['value']}"
            print(f"{w['ts']}  inv {w['inv']}  {w['field']:22s} {arrow}")
        print(f"({repeats} repeat write(s) of an unchanged value not listed)")

    print()
    print("=" * 70)
    print(f"ERRORS / WARNINGS OF INTEREST ({len(errors)} found)")
    print("=" * 70)

    if not errors:
        print("None found - clean run.")
    else:
        for e in errors:
            print(e)

    print()
    print("=" * 70)
    print(f"SUMMARY: {len(writes)} write intention(s), {len(errors)} error/warning line(s)")
    print(f"         (SOC/hold: {changes} change(s), {repeats} unchanged repeat(s) - not in the count above)")
    print("=" * 70)


if __name__ == "__main__":
    args = sys.argv[1:]
    list_fields = "--list-fields" in args
    soc_fields = SOC_FIELDS
    if "--soc-fields" in args:
        i = args.index("--soc-fields")
        try:
            soc_fields = tuple(x.strip() for x in args[i + 1].split(",") if x.strip())
        except IndexError:
            print("--soc-fields needs a comma separated list")
            sys.exit(1)
        del args[i:i + 2]
    args = [a for a in args if a != "--list-fields"]
    if len(args) != 1:
        print("Usage: python3 check_predbat_writes.py <path-to-predbat-log> "
              "[--list-fields] [--soc-fields a,b,c]")
        sys.exit(1)
    main(args[0], soc_fields, list_fields)
