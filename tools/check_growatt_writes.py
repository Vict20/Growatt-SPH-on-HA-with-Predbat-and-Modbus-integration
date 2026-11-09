#!/usr/bin/env python3
"""
check_growatt_writes.py

Parses a Home Assistant log file and produces a clean, human-readable
summary of every Growatt Modbus atomic register write, decoded back to
real start/end times and enable state, alongside any errors or warnings
worth flagging (Exception Code 4 rejections, script failures, reversion
warnings, malformed data, etc).

Built to verify that HA automations driving TOU period writes (e.g. the
disable-then-enable overlap-avoidance sequence described in
battery-scheduling.md) are actually landing cleanly on the inverter,
without manually grepping a multi-megabyte debug log by hand each time.

------------------------------------------------------------------------
USAGE
------------------------------------------------------------------------
    python3 check_growatt_writes.py path/to/home-assistant.log
    python3 check_growatt_writes.py path/to/home-assistant.log --list-other

Requires only the Python standard library - no dependencies to install.
Works against any HA log that contains growatt_modbus debug output
(custom_components.growatt_modbus: debug must be enabled at the time
the log was captured, or the [WRITE_MULTI] lines this script looks for
will not exist in the file).

------------------------------------------------------------------------
OUTPUT
------------------------------------------------------------------------
1. GROWATT REGISTER WRITE TIMELINE
   One line per atomic write attempt, in chronological order:
       <timestamp>  <slot name>  <start> -> <end>  [ENABLED/disabled]

   If a write was refused by the inverter (Exception Code 4, or any
   other device-side rejection) within a couple of seconds of the
   attempt, that line is marked:
       *** REJECTED BY INVERTER - DID NOT LAND (...) ***
   A rejected atomic triple never partially applies - the registers
   stay exactly as they were before the attempt - so a marked line
   means "this intended change never reached the inverter", not "this
   happened but something went wrong afterwards". Without this
   cross-check, a rejected write looks identical to a successful one
   in the timeline, which has previously masked a genuine missed
   schedule update - see the SUMMARY line for a landed/rejected count.

2. ERRORS / WARNINGS OF INTEREST
   Any log line matching known failure patterns - Modbus exception
   responses, write errors, automation script crashes, or register
   reversion warnings - so these don't need to be hunted for separately.

3. SUMMARY
   Total write count and total error count for the file.

Cross-checking tip: comparing this timeline against Predbat's own log
(grep for "write_and_poll_value: Wrote" in the Predbat log) confirms
that every write Predbat intended actually reached the inverter, and
that nothing landed on the registers without a corresponding intent -
not just that the run was error-free.

------------------------------------------------------------------------
ADDED: SOC / HOLD AND OTHER NON-TRIPLE WRITES
------------------------------------------------------------------------
The timeline above only matches 3-register atomic writes (start, end,
enable). A second section, "OTHER REGISTER WRITES", now lists any
write that is NOT a 3-register triple: single-register writes
("[WRITE] Request to write register N with value V", e.g. the charge-stop
SOC at 1091 = entity growatt_battery_charge_stopped_soc, or the
discharge-stop SOC at 1071) and any other [WRITE_MULTI] length, labelled from REG_LABELS and checked for rejection in the same
way. These are NOT counted in the "write attempt(s)" total, so the
cross-check against Predbat's slot writes is unchanged.

If the integration logs single-register writes in some other format, they
will not appear there. Two helpers cover that:
    --list-other   prints every distinct growatt_modbus log line mentioning
                   a write that the script did not decode, so the real
                   format can be seen and a pattern added.
    Lines that mention a known SOC register (see SOC_REGISTERS) but were not
    decoded are listed under "UNDECODED LINES MENTIONING SOC REGISTERS".

Slot labels now also include the confirmed new slots (Battery First 4-6,
Grid First 4-6; layout 1018+3n and 1027+3n, integration 2.0.6-b12+) and
the existing slots 1080/1083 (Grid First 1-2) and 1100/1103 (Battery
First 1-2).

------------------------------------------------------------------------
ASSUMPTIONS - READ BEFORE ADAPTING FOR ANOTHER MODEL/SLOT LAYOUT
------------------------------------------------------------------------
This script was written against one specific installation:

    Inverter:    Growatt SPH (3-6kW)
    Integration: 0xAHA/Growatt_ModbusTCP, local Modbus TCP
    Slots used:  Grid First Period 3 (registers 1086-1088)
                 Battery First Period 3 (registers 1106-1108)

The `slot_name()` function hard-codes these two register bases (1086,
1106) purely for friendly labelling in the output - it does not affect
which writes get picked up. The write-detection regex itself matches
any `[WRITE_MULTI] Request to write 3 registers starting at N: [a, b, c]`
line regardless of register address, so writes to other slots (e.g. MOD
GEN4's 9-period layout, or WIT's VPP registers) will still be captured
and printed - they'll just show as "reg <N>" rather than a named slot
until `slot_name()` is extended with the relevant addresses for that
model.

To adapt for a different inverter/slot layout: add the relevant
register base(s) and a descriptive label to the dict in `slot_name()`.
No other changes should be needed.

Time decoding assumes the standard hour*256+minute register encoding
used across the SPH TOU registers - confirm this matches before relying
on the decoded output for a different register family.

------------------------------------------------------------------------
Contributed by a user of the Growatt SPH / Growatt_ModbusTCP integration
after a week of testing the disable-then-enable overlap-avoidance
automation pattern under real overnight Predbat-driven load. Shared as
a starting point, not a finished general-purpose tool - the useful part
is the approach (decode + cross-check against intent), not the specific
register list.
------------------------------------------------------------------------
"""

import re
import sys
from datetime import datetime


# How close together a write request and a rejection warning must be
# (in seconds) to be treated as "this write was rejected", rather than
# an unrelated rejection elsewhere in the log for the same register.
REJECTION_WINDOW_SECONDS = 2.0


def reg_to_time(value):
    """Convert a raw register value (hour*256 + minute) to HH:MM."""
    try:
        value = int(value)
        hour = value // 256
        minute = value % 256
        return f"{hour:02d}:{minute:02d}"
    except (ValueError, TypeError):
        return f"?({value})"


SLOT_NAMES = {
    1080: "GF1", 1083: "GF2", 1086: "GF3 (discharge)",
    1100: "BF1", 1103: "BF2", 1106: "BF3 (charge)",
    # New slots, working since the 22 Oct 2026 firmware update
    # (RAAa919486 / ZCBa-0086). Layout 1018+3n / 1027+3n.
    1018: "BF4", 1021: "BF5", 1024: "BF6",
    1027: "GF4", 1030: "GF5", 1033: "GF6",
}

# Single registers shared by all slots (SPH).
REG_LABELS = {
    1070: "discharge power rate (%)",
    1071: "discharge stop SOC (%)",
    1090: "charge power rate (%)",
    1091: "charge stop SOC (%)",
}
SOC_REGISTERS = (1071, 1091)


def slot_name(register):
    return SLOT_NAMES.get(int(register), f"reg {register}")


def parse_ts(ts_str):
    """Parse a HA log timestamp ('YYYY-MM-DD HH:MM:SS.ffffff') to datetime."""
    return datetime.strptime(ts_str.strip(), "%Y-%m-%d %H:%M:%S.%f")


def main(log_path, list_other=False):
    write_pattern = re.compile(
        r"(?P<ts>[\d-]+ [\d:.]+).*"
        r"\[WRITE_MULTI\] Request to write \d+ registers starting at (?P<reg>\d+): "
        r"\[(?P<start>-?\d+), (?P<end>-?\d+), (?P<enable>-?\d+)\]"
    )

    # Any other [WRITE_MULTI] request (not the 3-register slot triples).
    other_pattern = re.compile(
        r"(?P<ts>[\d-]+ [\d:.]+).*"
        r"\[WRITE_MULTI\] Request to write (?P<n>\d+) registers starting at (?P<reg>\d+): "
        r"\[(?P<vals>[^\]]*)\]"
    )

    # Single-register write (number entities such as charge_stopped_soc, 1091).
    single_pattern = re.compile(
        r"(?P<ts>[\d-]+ [\d:.]+).*"
        r"\[WRITE\] Request to write register (?P<reg>\d+) with value (?P<val>-?\d+)"
    )

    # Captures the register number from a device rejection, so it can be
    # matched back against the write attempt that triggered it.
    rejection_pattern = re.compile(
        r"(?P<ts>[\d-]+ [\d:.]+).*"
        r"write_registers\((?P<reg>\d+),.*refused by the device: "
        r"(?P<detail>ExceptionResponse\([^)]*\))"
    )

    # A single-register write failure names no register, so it is matched
    # to the most recent single-register write by time.
    single_error_pattern = re.compile(
        r"(?P<ts>[\d-]+ [\d:.]+).*"
        r"\[WRITE\] Inverter responded with error: (?P<detail>.*)"
    )

    error_pattern = re.compile(
        r"(?P<ts>[\d-]+ [\d:.]+).*"
        r"(ExceptionResponse|ModbusWriteError|Error executing script|"
        r"Write reversion detected|Invalid data|"
        r"\[WRITE\] Inverter responded with error|"
        r"\[WRITE VERIFY\] (?!Register \d+ verified))"
    )

    writes = []
    others = []
    soc_lines = []
    verified = 0
    skipped_same = 0
    undecoded = {}
    rejections = []
    errors = []

    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = write_pattern.search(line)
                if m:
                    writes.append(m.groupdict())
                    continue
                m = single_pattern.search(line)
                if m:
                    d = m.groupdict()
                    d["n"] = "1"
                    d["vals"] = d.pop("val")
                    others.append(d)
                    continue
                m = other_pattern.search(line)
                if m:
                    if m.group("n") != "3":
                        others.append(m.groupdict())
                    else:
                        # 3 registers but not integer triples; show raw.
                        undecoded.setdefault(line.strip(), 0)
                        undecoded[line.strip()] += 1
                    continue
                m = rejection_pattern.search(line)
                if m:
                    rejections.append(m.groupdict())
                m = single_error_pattern.search(line)
                if m:
                    d = m.groupdict()
                    d["reg"] = None
                    d["detail"] = d["detail"].strip()
                    rejections.append(d)
                if re.search(r"\[WRITE VERIFY\] Register \d+ verified", line):
                    verified += 1
                    continue
                if re.search(r"already reads .* skipping write to register", line):
                    skipped_same += 1
                    continue
                if re.search(r"\[WRITE\] Successfully wrote value", line):
                    continue
                m = error_pattern.search(line)
                if m:
                    errors.append(line.strip())
                elif "growatt_modbus" in line and re.search(r"writ|wrot|setting", line, re.IGNORECASE):
                    # Write-related line the patterns above did not decode.
                    key = re.sub(r"^[\d-]+ [\d:.]+\s*", "", line.strip())
                    key = re.sub(r"\d+", "N", key)
                    undecoded.setdefault(key, 0)
                    undecoded[key] += 1
                    for reg in SOC_REGISTERS:
                        if re.search(rf"\b{reg}\b", line):
                            soc_lines.append(line.strip())
                            break
    except FileNotFoundError:
        print(f"File not found: {log_path}")
        sys.exit(1)

    # Match each write against any rejection for the same register that
    # landed within REJECTION_WINDOW_SECONDS afterwards. A write with a
    # matching rejection never actually reached the inverter - the
    # atomic triple is refused as a whole, nothing partially applies -
    # so it's marked REJECTED rather than shown as if it succeeded.
    for w in writes:
        w["rejected"] = False
        w["rejection_detail"] = None
        try:
            w_time = parse_ts(w["ts"])
        except ValueError:
            continue
        for r in rejections:
            if r["reg"] is None or r["reg"] != w["reg"]:
                continue
            try:
                r_time = parse_ts(r["ts"])
            except ValueError:
                continue
            delta = (r_time - w_time).total_seconds()
            if 0 <= delta <= REJECTION_WINDOW_SECONDS:
                w["rejected"] = True
                w["rejection_detail"] = r["detail"]
                break

    for w in others:
        w["rejected"] = False
        w["rejection_detail"] = None
        try:
            w_time = parse_ts(w["ts"])
        except ValueError:
            continue
        for r in rejections:
            if r["reg"] is None:
                if w["n"] != "1":
                    continue
            elif r["reg"] != w["reg"]:
                continue
            try:
                r_time = parse_ts(r["ts"])
            except ValueError:
                continue
            if 0 <= (r_time - w_time).total_seconds() <= REJECTION_WINDOW_SECONDS:
                w["rejected"] = True
                w["rejection_detail"] = r["detail"]
                break

    print("=" * 70)
    print("GROWATT REGISTER WRITE TIMELINE")
    print("=" * 70)

    if not writes:
        print("No register writes found in this log.")
    else:
        for w in writes:
            reg = int(w["reg"])
            start_t = reg_to_time(w["start"])
            end_t = reg_to_time(w["end"])
            enable = "ENABLED" if w["enable"] == "1" else "disabled"
            if w["rejected"]:
                print(f"{w['ts']}  {slot_name(reg):16s}  "
                      f"{start_t} -> {end_t}   [{enable}]  "
                      f"*** REJECTED BY INVERTER - DID NOT LAND ({w['rejection_detail']}) ***")
            else:
                print(f"{w['ts']}  {slot_name(reg):16s}  "
                      f"{start_t} -> {end_t}   [{enable}]")

    print()
    print("=" * 70)
    print("OTHER REGISTER WRITES (not slot triples; SOC, rates; changes only)")
    print("=" * 70)
    if not others:
        print("None decoded.")
    else:
        # Registers such as 1071 (discharge stop SOC, used for a battery
        # hold) can be rewritten every Predbat cycle with the same value.
        # List a write only when the value changed, or when it was rejected;
        # unchanged repeats are counted below.
        last_val = {}
        repeats = 0
        for w in others:
            reg = int(w["reg"])
            vals = [v.strip() for v in w["vals"].split(",") if v.strip()]
            val_txt = ", ".join(vals)
            before = last_val.get(reg)
            if not w["rejected"]:
                last_val[reg] = val_txt
                if before == val_txt:
                    repeats += 1
                    continue
            label = REG_LABELS.get(reg, "")
            label = f"  ({label})" if label else ""
            change = f"{before} -> {val_txt}" if before is not None else f"(first seen) {val_txt}"
            tail = ""
            if w["rejected"]:
                tail = f"  *** REJECTED BY INVERTER - DID NOT LAND ({w['rejection_detail']}) ***"
            print(f"{w['ts']}  reg {reg:<5d} {change}{label}{tail}")
        print(f"({repeats} repeat write(s) of an unchanged value not listed)")
    print(f"(read-back verified: {verified} write(s); {skipped_same} duplicate write(s) "
          f"skipped by the integration because the register already held the value)")

    if soc_lines:
        print()
        print("=" * 70)
        print("UNDECODED LINES MENTIONING SOC REGISTERS (first 40)")
        print("=" * 70)
        for l in soc_lines[:40]:
            print(l)

    if list_other:
        print()
        print("=" * 70)
        print("DISTINCT WRITE-RELATED growatt_modbus LINES NOT DECODED (numbers shown as N)")
        print("=" * 70)
        if not undecoded:
            print("None.")
        for key, count in sorted(undecoded.items(), key=lambda kv: -kv[1]):
            print(f"{count:6d}  {key[:200]}")

    print()
    print("=" * 70)
    print(f"ERRORS / WARNINGS OF INTEREST ({len(errors)} found)")
    print("=" * 70)

    if not errors:
        print("None found - clean run.")
    else:
        for e in errors:
            print(e)

    rejected_count = sum(1 for w in writes if w["rejected"])
    landed_count = len(writes) - rejected_count

    print()
    print("=" * 70)
    print(f"SUMMARY: {len(writes)} write attempt(s) "
          f"({landed_count} landed, {rejected_count} rejected by inverter), "
          f"{len(errors)} error/warning line(s)")
    if others:
        o_rej = sum(1 for w in others if w["rejected"])
        print(f"         (other register writes, not in the count above: "
              f"{len(others)} attempt(s), {len(others) - o_rej} landed, {o_rej} rejected)")
    print("=" * 70)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--list-other"]
    if len(args) != 1:
        print("Usage: python3 check_growatt_writes.py <path-to-log-file> [--list-other]")
        sys.exit(1)
    main(args[0], list_other="--list-other" in sys.argv[1:])
