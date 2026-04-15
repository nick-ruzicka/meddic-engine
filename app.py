"""Flask entry point — creates the app, registers blueprints, initializes the DB."""

import os
import logging
from flask import Flask
from flask_cors import CORS
from dotenv import load_dotenv
from database import init_db

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s"
)
logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__, template_folder="dashboard/templates",
                static_folder="dashboard/static")
    app.secret_key = os.getenv("FLASK_SECRET_KEY", "-dev-secret")
    CORS(app)

    # Initialize database on startup
    init_db()

    # Register blueprints
    from dashboard.routes import dashboard_bp
    from api.routes import api_bp
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(api_bp, url_prefix="/api")

    logger.info("MEDDIC Engine started")
    return app


if __name__ == "__main__":
    app = create_app()
    port = int(os.getenv("PORT", 8765))
    debug = os.getenv("DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
