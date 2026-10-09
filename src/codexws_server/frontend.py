import os

from flask import Blueprint, send_from_directory

from .config import CDNLOCAL_DIR, FRONTEND_DIR


frontend = Blueprint("frontend", __name__)


@frontend.route("/")
def index():
    response = send_from_directory(FRONTEND_DIR, "index.html", max_age=0)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@frontend.route("/assets/<path:path>")
def frontend_assets(path):
    return send_from_directory(os.path.join(FRONTEND_DIR, "assets"), path, max_age=0)


@frontend.route("/.cdnlocal/<path:path>")
def cdnlocal_assets(path):
    return send_from_directory(CDNLOCAL_DIR, path, max_age=31536000)
