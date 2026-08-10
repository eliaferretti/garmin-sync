#!/usr/bin/env python3
"""
FIT -> coaching CSV.

Extracts only the channels that matter for training analysis, strips GPS,
and downsamples so the file stays small enough to be useful.

USAGE (standalone)
------------------
    pip install fitdecode
    python fit_to_csv.py ACTIVITY.fit
    python fit_to_csv.py ACTIVITY.fit --interval 1

Writes <name>_records.csv (time series) and <name>_summary.csv (one row).

USAGE (as a library, e.g. from garmin_sync.py)
----------------------------------------------
    result = fit_to_csv.build_csvs(fit_bytes, stem="12345678")
    result["records_csv"]              # str, ready to upload
    fit_to_csv.summary_text(result)    # human-readable recap

Auto interval: 1s for sessions under 90 min (interval work needs the
resolution), 5s for anything longer (a 4h ride at 1s is 14,000 rows of
mostly nothing).

GPS is never written. Lat/lon are dropped at parse time.
"""

import argparse
import csv
import io
import sys
from pathlib import Path

try:
    import fitdecode
except ImportError:
    fitdecode = None


class FitConversionError(Exception):
    """Raised when a FIT file cannot be turned into coaching CSVs."""


# HR zones, confirmed by device testing.
ZONES = [
    ("Z1", 114, 138),
    ("Z2", 139, 154),
    ("Z3", 155, 166),
    ("Z4", 167, 173),
    ("Z5", 174, 999),
]

# Everything else in the FIT record message is discarded.
WANTED = {
    "timestamp": "timestamp",
    "heart_rate": "hr",
    "speed": "speed_ms",
    "enhanced_speed": "speed_ms",
    "cadence": "cadence",
    "power": "power_w",
    "altitude": "altitude_m",
    "enhanced_altitude": "altitude_m",
    "distance": "distance_m",
    "temperature": "temp_c",
}

RECORD_COLUMNS = [
    "elapsed_s", "hr", "speed_kmh", "cadence", "power_w",
    "altitude_m", "distance_km",
]


def parse_fit(fileish):
    """Parse a FIT file: a path, a binary file object, or raw bytes."""
    if fitdecode is None:
        raise FitConversionError("Missing dependency. Run: pip install fitdecode")

    records, session = [], {}
    with fitdecode.FitReader(fileish) as fit:
        for frame in fit:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue

            if frame.name == "record":
                row = {}
                for field in frame.fields:
                    key = WANTED.get(field.name)
                    # Silently drops position_lat / position_long.
                    if key and field.value is not None:
                        row[key] = field.value
                if row.get("timestamp"):
                    records.append(row)

            elif frame.name == "session":
                for field in frame.fields:
                    if field.value is not None:
                        session[field.name] = field.value

    return records, session


def summarise(records, session, sport):
    hrs = [r["hr"] for r in records if r.get("hr")]
    pwr = [r["power_w"] for r in records if r.get("power_w")]
    cad = [r["cadence"] for r in records if r.get("cadence")]

    out = {
        "sport": sport,
        "start": session.get("start_time", records[0]["timestamp"] if records else ""),
        "moving_time_s": session.get("total_timer_time", ""),
        "elapsed_time_s": session.get("total_elapsed_time", ""),
        "distance_km": round(session["total_distance"] / 1000, 2)
                       if session.get("total_distance") else "",
        "elev_gain_m": session.get("total_ascent", ""),
        "calories": session.get("total_calories", ""),
        "avg_hr": round(sum(hrs) / len(hrs)) if hrs else "",
        "max_hr": max(hrs) if hrs else "",
        "avg_power_w": round(sum(pwr) / len(pwr)) if pwr else "",
        "max_power_w": max(pwr) if pwr else "",
        "avg_cadence": round(sum(cad) / len(cad)) if cad else "",
    }

    # Seconds per HR zone. Assumes ~1 Hz sampling in the raw record stream,
    # which is what Garmin writes for anything that isn't smart-recording.
    for name, lo, hi in ZONES:
        out[f"{name}_secs"] = sum(1 for h in hrs if lo <= h <= hi)
    out["below_Z1_secs"] = sum(1 for h in hrs if h < ZONES[0][1])

    # Cardiac drift: avg HR of the second half vs the first, at steady
    # effort. A large positive number on a long session means the aerobic
    # system was working progressively harder to hold the same output.
    if len(hrs) > 600:
        half = len(hrs) // 2
        first, second = hrs[:half], hrs[half:]
        out["hr_drift_bpm"] = round(sum(second) / len(second)
                                    - sum(first) / len(first), 1)
    else:
        out["hr_drift_bpm"] = ""

    return out


def summary_csv(summary):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(summary))
    w.writeheader()
    w.writerow(summary)
    return buf.getvalue()


def _records_csv(records, interval):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(RECORD_COLUMNS)

    t0 = records[0]["timestamp"]
    kept = 0

    for i, r in enumerate(records):
        if i % interval:
            continue
        w.writerow([
            int((r["timestamp"] - t0).total_seconds()),
            r.get("hr", ""),
            round(r["speed_ms"] * 3.6, 2) if r.get("speed_ms") else "",
            r.get("cadence", ""),
            r.get("power_w", ""),
            round(r["altitude_m"], 1) if r.get("altitude_m") else "",
            round(r["distance_m"] / 1000, 3) if r.get("distance_m") else "",
        ])
        kept += 1

    return buf.getvalue(), kept


def build_csvs(fileish, stem="activity", interval=0):
    """Convert a FIT file to a coaching CSV, entirely in memory."""
    records, session = parse_fit(fileish)
    if not records:
        raise FitConversionError("No record data found in that FIT file.")

    sport = str(session.get("sport", "unknown"))

    # The summary uses the full-resolution stream; downsampling only
    # affects the records file.
    summary = summarise(records, session, sport)

    interval = interval or (1 if len(records) < 5400 else 5)
    records_text, kept = _records_csv(records, interval)

    return {
        "records_csv": records_text,
        "records_name": f"{stem}_records.csv",
        "summary": summary,
        "sport": sport,
        "interval": interval,
        "record_count": len(records),
        "rows_kept": kept,
    }


def _hms(seconds):
    seconds = int(seconds)
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def summary_text(result):
    """Human-readable recap of an activity, for a chat message."""
    s = result["summary"]
    duration = s["moving_time_s"] or result["record_count"]

    lines = [f"{result['sport']} - {_hms(duration)}"]

    if s["start"]:
        lines.append(str(s["start"]))

    totals = []
    if s["distance_km"] != "":
        totals.append(f"{s['distance_km']} km")
    if s["elev_gain_m"] != "":
        totals.append(f"{s['elev_gain_m']} m gain")
    if s["calories"] != "":
        totals.append(f"{s['calories']} kcal")
    if totals:
        lines.append(" | ".join(totals))

    if s["avg_hr"] != "":
        hr = f"HR avg {s['avg_hr']} / max {s['max_hr']}"
        if s["hr_drift_bpm"] != "":
            hr += f" (drift {s['hr_drift_bpm']:+} bpm)"
        lines.append(hr)

    if s["avg_power_w"] != "":
        lines.append(f"Power avg {s['avg_power_w']} W / max {s['max_power_w']} W")

    if s["avg_cadence"] != "":
        lines.append(f"Cadence avg {s['avg_cadence']}")

    zones = " | ".join(
        f"{name} {s[f'{name}_secs'] // 60}m" for name, _lo, _hi in ZONES
    )
    lines.append(f"Zones: {zones}")

    lines.append(f"{result['rows_kept']} rows @ {result['interval']}s, GPS stripped.")

    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="Convert a FIT file to coaching CSVs.")
    p.add_argument("fitfile")
    p.add_argument("--interval", type=int, default=0,
                   help="Sampling interval in seconds (0 = auto)")
    p.add_argument("--outdir", default=".")
    args = p.parse_args()

    src = Path(args.fitfile)
    if not src.exists():
        sys.exit(f"Not found: {src}")

    try:
        result = build_csvs(str(src), stem=src.stem, interval=args.interval)
    except FitConversionError as e:
        sys.exit(str(e))

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    rpath = outdir / result["records_name"]
    rpath.write_text(result["records_csv"], newline="", encoding="utf-8")

    spath = outdir / f"{src.stem}_summary.csv"
    spath.write_text(summary_csv(result["summary"]), newline="", encoding="utf-8")

    print(f"{rpath}   ({result['rows_kept']} rows @ {result['interval']}s)")
    print(f"{spath}   (1 row)")
    print()
    print(summary_text(result))


if __name__ == "__main__":
    main()
