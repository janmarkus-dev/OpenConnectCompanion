from __future__ import annotations
import os
import shutil
from datetime import datetime
from typing import Optional

from .. import db
from ..models.models import FileAsset, Activity, HealthMetric
from .fit_parser import parse_fit, sha256_file
from .device_scanner import list_mass_storage_mounts, find_fit_files

DATA_SUBDIR = "imports"


def import_fit_file(src_path: str, data_dir: str, device_serial: Optional[str] = None) -> Optional[FileAsset]:
    os.makedirs(os.path.join(data_dir, DATA_SUBDIR), exist_ok=True)

    # Deduplicate by SHA256
    file_hash = sha256_file(src_path)
    existing = FileAsset.query.filter_by(sha256=file_hash).first()
    if existing:
        return None

    # Copy file
    filename = os.path.basename(src_path)
    dst_path = os.path.join(data_dir, DATA_SUBDIR, f"{file_hash[:8]}_{filename}")
    shutil.copy2(src_path, dst_path)

    # Parse
    parsed = parse_fit(dst_path)

    asset = FileAsset(
        path=dst_path,
        device_serial=device_serial,
        file_type="fit",
        raw_size=os.path.getsize(dst_path),
        sha256=file_hash,
        parsed_json=parsed.json,
    )
    db.session.add(asset)
    db.session.flush()

    # Create activity summary
    meta = parsed.meta
    activity = Activity(
        type=meta.get("type"),
        start_time=datetime.fromisoformat(meta["start_time"]) if meta.get("start_time") else None,
        duration_s=meta.get("duration_s"),
        distance_m=meta.get("distance_m"),
        avg_hr=meta.get("avg_hr"),
        avg_power=meta.get("avg_power"),
        avg_cadence=meta.get("avg_cadence"),
        tss=meta.get("tss"),
        source_file_id=asset.id,
    )
    db.session.add(activity)
    # Health metrics if available
    health = parsed.json.get("health") if parsed and parsed.json else None
    if health and any(health.get(k) is not None for k in ("resting_hr", "body_battery", "stress_level")):
        hm = HealthMetric(
            metric_date=activity.start_time or datetime.utcnow(),
            resting_hr=health.get("resting_hr"),
            body_battery=health.get("body_battery"),
            stress_level=health.get("stress_level"),
            source_file_id=asset.id,
        )
        db.session.add(hm)
    db.session.commit()
    return asset


def scan_and_import_job():
    from flask import current_app
    data_dir = current_app.config.get("DATA_DIR", "/data")
    mounts = list_mass_storage_mounts()
    for m in mounts:
        fits = find_fit_files(m)
        for f in fits:
            try:
                import_fit_file(f, data_dir, device_serial=m.serial)
            except Exception as e:
                current_app.logger.exception(f"Failed to import {f}: {e}")
