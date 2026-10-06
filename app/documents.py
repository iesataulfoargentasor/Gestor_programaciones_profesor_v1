import io
import logging
import sqlite3
from uuid import uuid4

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, send_file, url_for

from app.database import get_db
from app.security import require
from app.service import DocumentService
from app.validation import ValidationError, validate_metadata, validate_upload

documents = Blueprint("documents", __name__)
log = logging.getLogger(__name__)


def service():
    return current_app.extensions["documents"]


@documents.get("/")
@require("read")
def catalog():
    filters = {key: request.args.get(key, "").strip() for key in ("q", "center", "module", "academic_year", "status")}
    records = service().catalog(filters)
    modules = sorted({r["module"] for r in service().catalog()})
    years = sorted({r["academic_year"] for r in service().catalog()}, reverse=True)
    return render_template("catalog.html", records=records, filters=filters, modules=modules, years=years)


@documents.route("/documents/new", methods=["GET", "POST"])
@require("upload")
def upload():
    form = request.form if request.method == "POST" else {"center": current_app.config["CENTER"]}
    errors = {}
    if request.method == "POST":
        try:
            metadata = validate_metadata(request.form)
            uploaded = validate_upload(request.files.get("file"), current_app.config["MAX_FILE_BYTES"], current_app.config["ALLOWED_EXTENSIONS"])
            result = service().upload(metadata, uploaded, g.user["id"], request.form.get("relation_target", "").strip())
            if result.get("duplicate"):
                record = result["duplicate"]
                flash("El contenido coincide exactamente con un archivo ya registrado; no se creó otra versión ni se duplicaron fragmentos.", "warning")
                return redirect(url_for("documents.detail", document_id=record["document_id"]))
            flash("Archivo recibido en estado Pendiente. La carga no supone aprobación ni publicación.", "success")
            return redirect(url_for("documents.detail", document_id=result["document_id"]))
        except ValidationError as exc:
            errors = exc.errors
        except sqlite3.IntegrityError:
            errors["relation_target"] = "Ha cambiado el catálogo durante la carga. Recarga y vuelve a comprobar la relación."
        except OSError:
            log.exception("Error local al guardar original")
            errors["archivo"] = "No se pudo guardar el original en el almacenamiento local."
    return render_template("upload.html", form=form, errors=errors, logical_docs=service().logical_documents()), (422 if errors else 200)


@documents.get("/documents/<document_id>")
@require("read")
def detail(document_id):
    record = service().document_detail(document_id)
    if not record:
        abort(404)
    return render_template("detail.html", record=record)


@documents.get("/versions/<version_id>/download")
@require("download")
def download(version_id):
    path = service().download_path(version_id)
    if not path:
        abort(404)
    db = get_db(current_app.config["DATABASE"])
    try:
        row = db.execute("SELECT f.original_name FROM versions v JOIN files f ON f.id=v.file_id WHERE v.id=?", (version_id,)).fetchone()
    finally:
        db.close()
    return send_file(path, as_attachment=True, download_name=row["original_name"], mimetype="application/octet-stream", max_age=0)


@documents.get("/review")
@require("review")
def review_queue():
    filters = {key: request.args.get(key, "").strip() for key in ("module", "academic_year", "status")}
    items = service().review_queue(filters)
    return render_template("review.html", items=items, filters=filters)


@documents.post("/versions/<version_id>/action")
@require("review")
def review_action(version_id):
    action = request.form.get("action", "")
    reason = request.form.get("reason", "")
    try:
        if action == "process":
            result = service().process_extraction(version_id, g.user["id"])
            if result == "pending_error":
                flash("No se pudo extraer texto. El original se conserva y la versión queda pendiente de revisión.", "error")
            else:
                flash("Extracción completada. Revisa el texto y sus localizadores antes de confirmar.", "success")
        else:
            result = service().review_action(version_id, g.user["id"], action, reason)
            if result is None:
                abort(404)
            flash({"approve": "Versión aprobada para procesar.", "return": "Versión devuelta a revisión.", "reject": "Versión rechazada; se conserva su motivo.",
                   "confirm": "Extracción verificada; lista para publicar.", "publish": "Versión publicada como vigente.", "retire": "Versión retirada del corpus consultable."}.get(action, "Acción completada."), "success")
    except ValidationError as exc:
        for message in exc.errors.values():
            flash(message, "error")
    return redirect(url_for("documents.detail", document_id=_document_for_version(version_id)))


def _document_for_version(version_id):
    db = get_db(current_app.config["DATABASE"])
    try:
        row = db.execute("SELECT document_id FROM versions WHERE id=?", (version_id,)).fetchone()
        return row["document_id"] if row else "missing"
    finally:
        db.close()
