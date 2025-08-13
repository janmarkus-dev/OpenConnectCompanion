from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
import os

# Global extensions

db = SQLAlchemy()
migrate = Migrate()
scheduler = BackgroundScheduler()


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)

    # Config
    in_container = os.path.exists("/.dockerenv") or os.environ.get("RUNNING_IN_DOCKER") == "1"
    default_data_dir = "/data" if in_container else os.path.abspath(os.path.join(os.getcwd(), "data"))
    default_upload_dir = os.path.join(default_data_dir, "uploads")

    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev"),
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", "sqlite:///occ.db"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        DATA_DIR=os.environ.get("DATA_DIR", default_data_dir),
        UPLOAD_FOLDER=os.environ.get("UPLOAD_FOLDER", default_upload_dir),
        OFFLINE=os.environ.get("OFFLINE", "0"),
        SCHEDULER_API_ENABLED=False,
    )
    if test_config:
        app.config.update(test_config)

    # Ensure data dirs
    os.makedirs(app.config["DATA_DIR"], exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    # Extensions
    CORS(app)
    db.init_app(app)
    migrate.init_app(app, db)

    # Blueprints
    from .blueprints.web import bp as web_bp
    from .blueprints.api import bp as api_bp
    app.register_blueprint(web_bp)
    app.register_blueprint(api_bp, url_prefix="/api")

    # Models import for migrations
    from . import models  # noqa: F401

    # Ensure database tables exist (MVP without migrations)
    with app.app_context():
        db.create_all()

    # Background jobs (device scan/import) with app context
    from .services.importer import scan_and_import_job

    def scheduled_device_scan():
        with app.app_context():
            scan_and_import_job()

    if not scheduler.running:
        scheduler.start(paused=False)
        scheduler.add_job(scheduled_device_scan, "interval", minutes=5, id="device_scan_import", replace_existing=True)

    @app.teardown_appcontext
    def shutdown_session(exception=None):
        try:
            if scheduler.running and not scheduler.get_jobs():
                pass
        except Exception:
            pass

    @app.context_processor
    def inject_settings():
        return {"SETTINGS": {"OFFLINE": app.config.get("OFFLINE")}}

    return app
