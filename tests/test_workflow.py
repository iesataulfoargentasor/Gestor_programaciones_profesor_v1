import io
from uuid import uuid4

import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from app.database import get_db


@pytest.fixture
def app(tmp_path):
    database = tmp_path / "data" / "test.sqlite3"
    application = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-" + "x" * 48,
        "DATABASE": str(database),
        "STORAGE_DIR": str(tmp_path / "data" / "originales"),
        "CENTER": "IES Demo",
        "RAG_API_TOKEN": "rag-test-token",
    })
    db = get_db(str(database))
    try:
        for name, role in (("admin", "admin"), ("colab", "collaborator"), ("revisor", "reviewer")):
            db.execute("INSERT INTO users VALUES (?,?,?,?,?,?)", (str(uuid4()), name, generate_password_hash("password-test"), role, 1, "2026-10-06T10:00:00+00:00"))
    finally:
        db.close()
    yield application


@pytest.fixture
def client(app):
    return app.test_client()


def as_user(client, username):
    with client.session_transaction() as session:
        session["username"] = username
        session["csrf"] = "test-csrf"


def form_data(content, name="programacion.txt", **overrides):
    data = {
        "csrf_token": "test-csrf", "center": "IES Demo", "module": "SBD",
        "academic_year": "2026-27", "title": "Programación de SBD", "source": "Documento de práctica autorizado",
        "declared_status": "aprobada", "declared_version": "", "relation_target": "",
        "relation_type": "", "relation_reason": "", "file": (io.BytesIO(content), name),
    }
    data.update(overrides)
    return data


def post_action(client, version_id, action, reason=""):
    return client.post(f"/versions/{version_id}/action", data={"csrf_token": "test-csrf", "action": action, "reason": reason}, follow_redirects=False)


def get_ids(app, document_id=None):
    db = get_db(app.config["DATABASE"])
    try:
        doc = db.execute("SELECT id FROM documents ORDER BY created_at LIMIT 1").fetchone()
        doc_id = document_id or doc["id"]
        versions = [dict(r) for r in db.execute("SELECT id,version_no,status FROM versions WHERE document_id=? ORDER BY version_no", (doc_id,)).fetchall()]
        return doc_id, versions
    finally:
        db.close()


def finish_publication(app, client, version_id):
    assert post_action(client, version_id, "approve").status_code == 302
    assert post_action(client, version_id, "process").status_code == 302
    assert post_action(client, version_id, "confirm").status_code == 302
    assert post_action(client, version_id, "publish").status_code == 302


def test_exact_duplicate_is_not_saved_as_second_version(app, client):
    as_user(client, "colab")
    content = b"Programacion de sistemas distribuidos.\nRA y criterios de evaluacion."
    first = client.post("/documents/new", data=form_data(content), content_type="multipart/form-data")
    assert first.status_code == 302
    doc_id, versions = get_ids(app)
    duplicate = client.post("/documents/new", data=form_data(content, "renombrado.txt"), content_type="multipart/form-data")
    assert duplicate.status_code == 302
    _, versions_after = get_ids(app, doc_id)
    db = get_db(app.config["DATABASE"])
    try:
        provenance = db.execute("SELECT COUNT(*) FROM provenance").fetchone()[0]
    finally:
        db.close()
    assert len(versions) == len(versions_after) == 1
    assert provenance == 1


def test_new_version_replaces_previous_only_after_review(app, client):
    content_v1 = b"Contenido inicial SBD.\nPlan de estudios version uno."
    as_user(client, "colab")
    assert client.post("/documents/new", data=form_data(content_v1), content_type="multipart/form-data").status_code == 302
    doc_id, versions = get_ids(app)
    as_user(client, "revisor")
    assert client.get("/review").status_code == 200
    assert client.get(f"/documents/{doc_id}").status_code == 200
    finish_publication(app, client, versions[0]["id"])

    as_user(client, "colab")
    metadata = form_data(b"Contenido corregido SBD.\nPlan de estudios version dos.", "programacion-v2.txt",
                         relation_target=doc_id, relation_type="revision", relation_reason="Corrección autorizada de contenidos")
    assert client.post("/documents/new", data=metadata, content_type="multipart/form-data").status_code == 302
    _, versions = get_ids(app, doc_id)
    assert [v["status"] for v in versions] == ["published", "pending"]

    as_user(client, "revisor")
    assert client.get(f"/documents/{doc_id}").status_code == 200
    finish_publication(app, client, versions[1]["id"])
    response = client.get("/api/rag/chunks?module=SBD&academic_year=2026-27", headers={"Authorization": "Bearer rag-test-token"})
    assert response.status_code == 200
    items = response.get_json()["items"]
    assert items
    assert all(item["version_id"] == versions[1]["id"] and item["publication_status"] == "published" for item in items)
    assert all("corregido" in item["text"] for item in items)
    _, final_versions = get_ids(app, doc_id)
    assert [v["status"] for v in final_versions] == ["superseded", "published"]
    db = get_db(app.config["DATABASE"])
    try:
        stored = db.execute(
            "SELECT f.original_name,e.result,COUNT(c.id) AS chunks "
            "FROM versions v JOIN files f ON f.id=v.file_id "
            "JOIN extractions e ON e.version_id=v.id JOIN chunks c ON c.extraction_id=e.id "
            "WHERE v.id=? GROUP BY f.id,e.id", (versions[1]["id"],)
        ).fetchone()
    finally:
        db.close()
    assert stored["original_name"] == "programacion-v2.txt"
    assert stored["result"] == "success"
    assert stored["chunks"] > 0


def test_draft_cannot_be_published_and_rag_endpoint_needs_token(app, client):
    as_user(client, "colab")
    draft = form_data(b"Borrador de la programacion.", declared_status="borrador")
    assert client.post("/documents/new", data=draft, content_type="multipart/form-data").status_code == 302
    _, versions = get_ids(app)
    as_user(client, "revisor")
    response = post_action(client, versions[0]["id"], "approve")
    assert response.status_code == 302
    _, latest = get_ids(app)
    assert latest[0]["status"] == "pending"
    assert client.get("/api/rag/chunks?module=SBD&academic_year=2026-27").status_code == 401


def test_upload_rejects_bad_file_signature(app, client):
    as_user(client, "colab")
    data = form_data(b"this is not a PDF", "fake.pdf")
    response = client.post("/documents/new", data=data, content_type="multipart/form-data")
    assert response.status_code == 422
    db = get_db(app.config["DATABASE"])
    try:
        assert db.execute("SELECT COUNT(*) FROM versions").fetchone()[0] == 0
    finally:
        db.close()


def test_pages_are_available_to_the_intended_roles(app, client):
    assert client.get("/login").status_code == 200
    as_user(client, "colab")
    assert client.get("/").status_code == 200
    assert client.get("/documents/new").status_code == 200
    assert client.get("/review").status_code == 403
    as_user(client, "revisor")
    assert client.get("/review").status_code == 200


def test_reviewer_cannot_approve_their_own_upload(app, client):
    as_user(client, "revisor")
    response = client.post("/documents/new", data=form_data(b"Programacion cargada por una revisora."), content_type="multipart/form-data")
    assert response.status_code == 302
    _, versions = get_ids(app)
    assert client.get(response.headers["Location"]).status_code == 200
    response = post_action(client, versions[0]["id"], "approve")
    assert response.status_code == 302
    _, latest = get_ids(app)
    assert latest[0]["status"] == "pending"
