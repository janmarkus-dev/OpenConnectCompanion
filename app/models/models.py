from __future__ import annotations
from datetime import datetime
from typing import Optional
from sqlalchemy import Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON
from .. import db


class FileAsset(db.Model):
    __tablename__ = "file_assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(unique=True, index=True)
    device_serial: Mapped[Optional[str]]
    file_type: Mapped[str]  # e.g., 'fit'
    imported_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    raw_size: Mapped[int] = mapped_column(default=0)
    sha256: Mapped[str] = mapped_column(unique=True)
    parsed_json: Mapped[Optional[dict]] = mapped_column(JSON)

    activities: Mapped[list[Activity]] = relationship("Activity", back_populates="source_file")

    __table_args__ = (
        Index("ix_file_assets_sha256", "sha256"),
    )


class Activity(db.Model):
    __tablename__ = "activities"
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[Optional[str]]  # run, ride, swim, etc.
    start_time: Mapped[Optional[datetime]] = mapped_column(index=True)
    duration_s: Mapped[Optional[float]]
    distance_m: Mapped[Optional[float]]
    avg_hr: Mapped[Optional[float]]
    avg_power: Mapped[Optional[float]]
    avg_cadence: Mapped[Optional[float]]
    tss: Mapped[Optional[float]]  # training stress score (approx)

    source_file_id: Mapped[int] = mapped_column(db.ForeignKey("file_assets.id"), index=True)
    source_file: Mapped[FileAsset] = relationship("FileAsset", back_populates="activities")

    # geo sampling and signals stored in parsed_json of file, accessible via API

    __table_args__ = (
        Index("ix_activities_start_time", "start_time"),
    )


class HealthMetric(db.Model):
    __tablename__ = "health_metrics"
    id: Mapped[int] = mapped_column(primary_key=True)
    metric_date: Mapped[datetime] = mapped_column(index=True)
    resting_hr: Mapped[Optional[float]]
    body_battery: Mapped[Optional[float]]
    stress_level: Mapped[Optional[float]]

    source_file_id: Mapped[int] = mapped_column(db.ForeignKey("file_assets.id"), index=True)
    source_file: Mapped[FileAsset] = relationship("FileAsset")

    __table_args__ = (
        UniqueConstraint("metric_date", "source_file_id", name="uq_health_date_source"),
    )
