from __future__ import annotations
import hashlib
from datetime import datetime, timedelta
from typing import Any, Dict, Tuple, Optional

try:
    from fitparse import FitFile
except Exception:  # pragma: no cover - handle missing import in lint
    FitFile = None


class ParsedFit:
    def __init__(self, json_data: Dict[str, Any], meta: Dict[str, Any]):
        self.json = json_data
        self.meta = meta


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_fit(path: str) -> ParsedFit:
    if FitFile is None:
        raise RuntimeError("fitparse not installed")

    fitfile = FitFile(path)
    fitfile.parse()

    streams: Dict[str, list] = {"timestamp": []}
    hr, power, speed, cadence, lat, lon, alt = [], [], [], [], [], [], []

    activity_meta: Dict[str, Any] = {
        "type": None,
        "start_time": None,
        "duration_s": None,
        "distance_m": None,
        "avg_hr": None,
        "avg_power": None,
        "avg_cadence": None,
        "tss": None,
    }
    health: Dict[str, Any] = {"resting_hr": None, "body_battery": None, "stress_level": None}

    # Iterate messages
    for record in fitfile.get_messages():
        name = record.name
        values = {d.name: d.value for d in record}

        if name == "lap":
            # Could aggregate per lap
            pass
        elif name == "record":
            ts = values.get("timestamp")
            if ts:
                streams.setdefault("timestamp", []).append(ts.isoformat())
            hr.append(values.get("heart_rate"))
            power.append(values.get("power"))
            speed.append(values.get("speed"))
            cadence.append(values.get("cadence"))
            lat.append(values.get("position_lat"))
            lon.append(values.get("position_long"))
            alt.append(values.get("altitude"))
        elif name == "session":
            activity_meta["type"] = values.get("sport")
            st = values.get("start_time")
            if st:
                activity_meta["start_time"] = st.isoformat()
            activity_meta["duration_s"] = values.get("total_timer_time")
            activity_meta["distance_m"] = values.get("total_distance")
            activity_meta["avg_hr"] = values.get("avg_heart_rate")
            activity_meta["avg_power"] = values.get("avg_power")
            activity_meta["avg_cadence"] = values.get("avg_cadence")
        elif name in ("monitoring", "stress_level", "sleep", "sleep_level"):
            # Best-effort extraction if present
            rh = values.get("resting_heart_rate") or values.get("resting_hr")
            if rh is not None:
                health["resting_hr"] = rh
            bb = values.get("body_battery") or values.get("body_battery_level")
            if bb is not None:
                health["body_battery"] = bb
            sl = values.get("stress_level") or values.get("stress")
            if sl is not None:
                health["stress_level"] = sl

    # Calculate basic TSS approximation for cycling if power stream present
    if power and any(p is not None for p in power):
        # Very naive: NP ~ avg power, IF ~ avg/FTP where FTP fixed 250W, TSS=IF^2*duration_hours*100
        avg_power_vals = [p for p in power if p is not None]
        if avg_power_vals:
            avg = sum(avg_power_vals) / len(avg_power_vals)
            duration_s = activity_meta.get("duration_s") or (len(avg_power_vals) * 1)
            IF = (avg / 250.0)
            tss = (IF ** 2) * (duration_s / 3600.0) * 100.0
            activity_meta["avg_power"] = avg
            activity_meta["tss"] = tss

    streams["heart_rate"] = hr
    streams["power"] = power
    streams["speed"] = speed
    streams["cadence"] = cadence
    streams["lat"] = lat
    streams["lon"] = lon
    streams["alt"] = alt

    return ParsedFit({"streams": streams, "meta": activity_meta, "health": health}, activity_meta)
