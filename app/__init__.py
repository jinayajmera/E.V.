"""Janmat online voting - Flask application factory."""
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from flask import Flask, render_template, session, request, url_for
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.exceptions import HTTPException
from mysql.connector import errors as dberr
from .config import Config
from . import db
from . import security

csrf = CSRFProtect()

_HTTP_TEXT = {
    400: ("Request could not be understood", "Something in the request was not valid. Please go back and try again."),
    401: ("Sign in required", "Please log in to continue."),
    403: ("Not allowed", "You do not have permission to open this page."),
    404: ("Page not found", "The page you are looking for does not exist or has moved."),
    405: ("Method not allowed", "This address cannot be used that way."),
    413: ("File too large", "The upload is too large. Photographs can be at most 3 MB."),
    429: ("Too many requests", "Please slow down and try again in a moment."),
}


def _setup_logging(app):
    try:
        app.config["LOG_DIR"].mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(app.config["LOG_DIR"] / "app.log", maxBytes=1_000_000, backupCount=3,
                                      encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        handler.setLevel(logging.INFO)
        app.logger.addHandler(handler)
        app.logger.setLevel(logging.INFO)
    except OSError:
        pass                                    # logging to a file is optional


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config["WTF_CSRF_TIME_LIMIT"] = None    # token lives as long as the (idle-limited) session
    if test_config:
        app.config.update(test_config)
    _setup_logging(app)
    if app.config.get("TRUST_PROXY"):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    csrf.init_app(app)
    app.teardown_appcontext(db.close_db)
    security.init_security_headers(app)

    from .routes_main import main, voter
    from .routes_officer import officer
    app.register_blueprint(main)
    app.register_blueprint(voter)
    app.register_blueprint(officer)

    @app.before_request
    def _permanent():
        session.permanent = True

    @app.context_processor
    def _inject():
        ctx = {"role": session.get("role"), "user_name": session.get("name"),
               "app_name": app.config["APP_NAME"], "year": datetime.now().year}
        if session.get("role") == "voter":
            ctx["me_photo"] = url_for("main.media_voter", vid=session["uid"])
        elif session.get("role") == "officer":
            try:
                ctx["nav_pending"] = db.scalar("SELECT COUNT(*) FROM Voter WHERE VerificationStatus IN ('Pending','Flagged')")
            except Exception:
                ctx["nav_pending"] = 0
        return ctx

    def _show(code, title, message):
        return render_template("error.html", code=code, title=title, message=message), code

    @app.errorhandler(CSRFError)
    def _csrf(_e):
        security.log_event("CSRF_REJECTED", session.get("role"), session.get("name"), request.path)
        return _show(400, "Form expired", "Your form expired or was not valid. Go back, refresh the page and try again.")

    @app.errorhandler(dberr.Error)
    def _db(e):
        app.logger.error("database error on %s: %s", request.path, e)
        return _show(503, "Temporarily unavailable", db.friendly_error(e))

    @app.errorhandler(db.AppError)
    def _app(e):
        return _show(400, "Could not complete that", str(e))

    @app.errorhandler(HTTPException)
    def _http(e):
        code = e.code or 500
        title, msg = _HTTP_TEXT.get(code, (e.name, "Something went wrong."))
        return _show(code, title, msg)

    @app.errorhandler(Exception)
    def _500(e):
        app.logger.exception("unhandled error on %s", request.path)
        return _show(500, "Something went wrong", "An unexpected problem occurred. The incident has been logged.")

    return app
