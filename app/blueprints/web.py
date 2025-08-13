from __future__ import annotations
from flask import Blueprint, render_template, request, redirect, url_for, flash
from ..models.models import Activity

bp = Blueprint("web", __name__)


@bp.route("/")
def index():
    activities = Activity.query.order_by(Activity.start_time.desc()).limit(200).all()
    return render_template("index.html", activities=activities)


@bp.route("/activity/<int:activity_id>")
def activity(activity_id: int):
    return render_template("activity.html", activity_id=activity_id)


@bp.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        file = request.files.get("file")
        if not file:
            flash("No file provided", "warning")
            return redirect(url_for("web.upload"))
        if not file.filename.lower().endswith(".fit"):
            flash("Only .fit files are supported", "warning")
            return redirect(url_for("web.upload"))
        from flask import current_app
        temp_path = current_app.config["UPLOAD_FOLDER"] + "/" + file.filename
        file.save(temp_path)
        from ..services.importer import import_fit_file
        from flask import current_app
        asset = import_fit_file(temp_path, current_app.config["DATA_DIR"], device_serial="upload")
        if asset:
            flash("Imported", "success")
        else:
            flash("Already imported", "info")
        return redirect(url_for("web.index"))

    return render_template("upload.html")


@bp.route("/trends")
def trends():
    return render_template("trends.html")


@bp.route("/health")
def health():
    return render_template("health.html")
