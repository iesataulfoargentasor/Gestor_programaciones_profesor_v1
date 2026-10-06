import hmac

from flask import Blueprint, current_app, jsonify, request

rag_api = Blueprint("rag_api", __name__, url_prefix="/api/rag")


@rag_api.get("/chunks")
def published_chunks():
    expected = current_app.config["RAG_API_TOKEN"]
    supplied = request.headers.get("Authorization", "")
    supplied = supplied[7:] if supplied.startswith("Bearer ") else ""
    if not expected:
        return jsonify(error="El acceso de PIA no está configurado."), 503
    if not supplied or not hmac.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8")):
        return jsonify(error="No autorizado."), 401
    module = request.args.get("module", "").strip()
    year = request.args.get("academic_year", "").strip()
    if not module or not year:
        return jsonify(error="Indica module y academic_year para filtrar el corpus."), 400
    chunks = current_app.extensions["documents"].rag_chunks(module, year, request.args.get("center", "").strip() or None)
    return jsonify(items=chunks, count=len(chunks), scope={"module": module, "academic_year": year})
