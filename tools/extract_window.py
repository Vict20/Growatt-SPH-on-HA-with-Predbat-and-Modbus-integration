import sys

log, start, end = sys.argv[1:4]   # e.g. "2026-10-13 01:25" "2026-10-13 02:05"
keys = (
    "Charge settings",
    "Wrote charge",
    "Wrote discharge",
    "Filtered charge windows",
    "Export windows filtered",
    "Disabled charge window",
    "limit",
    "target",
)
with open(log, errors="ignore") as f, open("pb_window.txt", "w", encoding="utf-8") as out:
    for line in f:
        if start <= line[:16] <= end and any(k in line for k in keys):
            out.write(line)