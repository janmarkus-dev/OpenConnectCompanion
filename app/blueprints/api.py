from __future__ import annotations
from flask import Blueprint, jsonify, request, current_app
from ..models.models import Activity, FileAsset, HealthMetric
from .. import db

bp = Blueprint("api", __name__)


@bp.get("/activities")
def list_activities():
    q = Activity.query.order_by(Activity.start_time.desc()).limit(200)
    items = []
    for a in q:
        items.append({
            "id": a.id,
            "type": a.type,
            "start_time": a.start_time.isoformat() if a.start_time else None,
            "duration_s": a.duration_s,
            "distance_m": a.distance_m,
            "avg_hr": a.avg_hr,
            "avg_power": a.avg_power,
            "avg_cadence": a.avg_cadence,
            "tss": a.tss,
        })
    return jsonify(items)


@bp.get("/activities/<int:activity_id>")
def get_activity(activity_id: int):
    a = Activity.query.get_or_404(activity_id)
    asset = FileAsset.query.get(a.source_file_id)
    data = asset.parsed_json if asset else None
    return jsonify({
        "activity": {
            "id": a.id,
            "type": a.type,
            "start_time": a.start_time.isoformat() if a.start_time else None,
            "duration_s": a.duration_s,
            "distance_m": a.distance_m,
            "avg_hr": a.avg_hr,
            "avg_power": a.avg_power,
            "avg_cadence": a.avg_cadence,
            "tss": a.tss,
        },
        "data": data,
    })


@bp.get("/trends/tss")
def tss_trend():
    rows = Activity.query.with_entities(Activity.start_time, Activity.tss).order_by(Activity.start_time).all()
    return jsonify([[r[0].isoformat() if r[0] else None, r[1]] for r in rows])


@bp.get("/trends/best20minpower")
def best_20min_power():
    # MVP: approximate best 20min power = session avg_power if duration >= 20min
    rows = Activity.query.with_entities(Activity.start_time, Activity.avg_power, Activity.duration_s).order_by(Activity.start_time).all()
    series = []
    for st, avg_p, dur in rows:
        if avg_p and dur and dur >= 20*60:
            series.append([st.isoformat() if st else None, avg_p])
    return jsonify(series)


@bp.get("/trends/pr_distance")
def pr_distance():
    # MVP: use session total distance as "PR" for the day
    rows = Activity.query.with_entities(Activity.start_time, Activity.distance_m).order_by(Activity.start_time).all()
    out = []
    best = 0.0
    for st, dist in rows:
        d = (dist or 0.0)
        best = max(best, d)
        out.append([st.isoformat() if st else None, best/1000.0])
    return jsonify(out)


@bp.get("/health")
def health_metrics():
    rows = HealthMetric.query.order_by(HealthMetric.metric_date).all()
    out = []
    for r in rows:
        out.append({
            "date": r.metric_date.isoformat() if r.metric_date else None,
            "resting_hr": r.resting_hr,
            "body_battery": r.body_battery,
            "stress_level": r.stress_level,
        })
    return jsonify(out)


@bp.post("/upload")
def upload_fit():
    if "file" not in request.files:
        return jsonify({"error": "no file"}), 400
    f = request.files["file"]
    if not f.filename.lower().endswith(".fit"):
        return jsonify({"error": "only .fit"}), 400
    temp_path = current_app.config["UPLOAD_FOLDER"] + "/" + f.filename
    f.save(temp_path)

    from ..services.importer import import_fit_file
    asset = import_fit_file(temp_path, current_app.config["DATA_DIR"], device_serial="upload")
    return jsonify({"imported": bool(asset)})


@bp.post("/scan")
def trigger_scan():
    from ..services.importer import scan_and_import_job
    scan_and_import_job()
    return jsonify({"status": "ok"})
