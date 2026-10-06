import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, g, render_template, request, session

from app.database import initialize_database
from app.security import csrf_token, has_permission
from app.service import DocumentService

ROOT = Path(__file__).resolve().parent.parent


def create_app(test_config=None):
    load_dotenv(ROOT / ".env")
    app = Flask(__name__, instance_path=str(ROOT / "instance"))
    data_dir = Path(os.getenv("DATA_DIR", "instance")).expanduser()
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    data_dir = data_dir.resolve()
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", ""),
        DATABASE=str(data_dir / "docia.sqlite3"),
        STORAGE_DIR=str(data_dir / "originales"),
        CENTER=os.getenv("CENTER_NAME", "IES Ataúlfo Argenta"),
        MAX_FILE_BYTES=int(os.getenv("MAX_FILE_MB", "20")) * 1024 * 1024,
        ALLOWED_EXTENSIONS={"pdf", "docx", "txt"},
        CHUNK_SIZE=1000,
        CHUNK_OVERLAP=150,
        RAG_API_TOKEN=os.getenv("RAG_API_TOKEN", ""),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=2),
        MAX_CONTENT_LENGTH=int(os.getenv("MAX_FILE_MB", "20")) * 1024 * 1024 + 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    if not app.config["SECRET_KEY"] or len(app.config["SECRET_KEY"]) < 32:
        raise RuntimeError("Configura en .env una SECRET_KEY aleatoria de al menos 32 caracteres. Ejecuta scripts/init_project.py.")
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    Path(app.config["STORAGE_DIR"]).mkdir(parents=True, exist_ok=True)
    initialize_database(app.config["DATABASE"])
    app.extensions["documents"] = DocumentService(app.config["DATABASE"], app.config["STORAGE_DIR"],
                                                    app.config["CHUNK_SIZE"], app.config["CHUNK_OVERLAP"])

    from app.auth import auth
    from app.documents import documents
    from app.rag_api import rag_api

    app.register_blueprint(auth)
    app.register_blueprint(documents)
    app.register_blueprint(rag_api)
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.globals["has_permission"] = has_permission

    @app.before_request
    def load_user_and_protect_posts():
        from app.database import get_db
        username = session.get("username")
        if username:
            db = get_db(app.config["DATABASE"])
            try:
                g.user = db.execute("SELECT id, username, role, active FROM users WHERE username=?", (username,)).fetchone()
            finally:
                db.close()
        else:
            g.user = None
        if request.method == "POST":
            from app.security import verify_csrf
            if not verify_csrf(request.form.get("csrf_token", "")):
                abort(400, description="El formulario ha caducado. Recarga la página e inténtalo de nuevo.")

    @app.context_processor
    def shared_context():
        return {"center_name": app.config["CENTER"], "max_file_mb": app.config["MAX_FILE_BYTES"] // (1024 * 1024)}

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(413)
    def too_large(_error):
        return render_template("error.html", title="Archivo demasiado grande", message=f"El límite actual es {app.config['MAX_FILE_BYTES'] // (1024 * 1024)} MB."), 413

    @app.errorhandler(404)
    def missing(_error):
        return render_template("error.html", title="No encontrado", message="El recurso no existe o ya no está disponible."), 404

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("error.html", title="Acceso denegado", message="Tu cuenta no tiene permiso para esta operación."), 403

    @app.errorhandler(500)
    def internal(_error):
        return render_template("error.html", title="Error interno", message="No se ha confirmado la operación. Consulta el catálogo antes de repetirla."), 500

    return app
