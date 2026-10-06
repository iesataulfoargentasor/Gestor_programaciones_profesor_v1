import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.database import get_db, transaction
from app.extraction import ExtractionError, extract_text, split_pages
from app.validation import ValidationError

log = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def audit(db, user_id, action, entity_type, entity_id, detail=""):
    db.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?,?)",
               (str(uuid4()), user_id, action, entity_type, entity_id, detail[:500], now()))


class DocumentService:
    def __init__(self, database, storage_dir, chunk_size=1000, overlap=150):
        self.database = database
        self.storage_dir = Path(storage_dir).resolve()
        self.chunk_size, self.overlap = chunk_size, overlap
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def catalog(self, filters=None):
        filters = filters or {}
        sql = """SELECT d.*, latest.id AS active_id, latest.version_no AS active_version, latest.status AS active_status,
                        latest.declared_status, f.original_name, latest.created_at AS uploaded_at,
                        COALESCE(e.result,'not_started') AS extraction_status,
                        u.username AS uploader, current.id AS published_version_id
                 FROM documents d LEFT JOIN versions latest ON latest.id=(SELECT id FROM versions x WHERE x.document_id=d.id ORDER BY x.version_no DESC LIMIT 1)
                 LEFT JOIN files f ON f.id=latest.file_id
                 LEFT JOIN extractions e ON e.id=(SELECT id FROM extractions ex WHERE ex.version_id=latest.id ORDER BY ex.created_at DESC,ex.rowid DESC LIMIT 1)
                 LEFT JOIN versions current ON current.id=d.current_version_id
                 LEFT JOIN users u ON u.id=latest.uploaded_by WHERE 1=1"""
        params = []
        for key, column in (("center", "d.center"), ("module", "d.module"), ("academic_year", "d.academic_year")):
            value = (filters.get(key) or "").strip()
            if value:
                sql += f" AND {column}=?"
                params.append(value)
        if filters.get("status"):
            sql += " AND latest.status=?"
            params.append(filters["status"])
        if filters.get("q"):
            sql += " AND (d.title LIKE ? OR d.module LIKE ? OR f.original_name LIKE ?)"
            like = "%" + filters["q"].strip()[:100] + "%"
            params.extend((like, like, like))
        sql += " ORDER BY d.academic_year DESC, d.module COLLATE NOCASE, d.title COLLATE NOCASE"
        db = get_db(self.database)
        try:
            return [dict(r) for r in db.execute(sql, params).fetchall()]
        finally:
            db.close()

    def logical_documents(self):
        db = get_db(self.database)
        try:
            return [dict(r) for r in db.execute("SELECT id,center,module,academic_year,title FROM documents ORDER BY academic_year DESC,module,title").fetchall()]
        finally:
            db.close()

    def document_detail(self, document_id):
        db = get_db(self.database)
        try:
            doc = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            if not doc:
                return None
            result = dict(doc)
            result["versions"] = [dict(r) for r in db.execute(
                "SELECT v.*,f.original_name,f.storage_name,f.extension,f.mime_type,f.size_bytes,f.sha256,f.source,"
                "e.id AS extraction_id,e.result AS extraction_status,e.error AS extraction_error,e.text_normalized AS extracted_text,"
                "u.username AS uploader,au.username AS approver,vu.username AS verifier,pu.username AS publisher "
                "FROM versions v LEFT JOIN users u ON u.id=v.uploaded_by LEFT JOIN users au ON au.id=v.approved_by "
                "LEFT JOIN users vu ON vu.id=v.verified_by LEFT JOIN users pu ON pu.id=v.published_by "
                "JOIN files f ON f.id=v.file_id LEFT JOIN extractions e ON e.id=(SELECT id FROM extractions ex WHERE ex.version_id=v.id ORDER BY ex.created_at DESC,ex.rowid DESC LIMIT 1) "
                "WHERE v.document_id=? ORDER BY v.version_no DESC", (document_id,)).fetchall()]
            for version in result["versions"]:
                version["chunks"] = [dict(r) for r in db.execute("SELECT id,locator,text FROM chunks WHERE extraction_id=? ORDER BY rowid", (version["extraction_id"],)).fetchall()] if version["extraction_id"] else []
                version["reviews"] = [dict(r) for r in db.execute(
                    "SELECT r.*,u.username AS reviewer FROM reviews r JOIN users u ON u.id=r.reviewer_id WHERE r.version_id=? ORDER BY r.created_at DESC",
                    (version["id"],)).fetchall()]
            result["audit"] = [dict(r) for r in db.execute(
                "SELECT a.*,u.username FROM audit_events a LEFT JOIN users u ON u.id=a.user_id WHERE a.entity_id IN (SELECT id FROM versions WHERE document_id=?) OR (a.entity_type='document' AND a.entity_id=?) ORDER BY a.created_at DESC",
                (document_id, document_id)).fetchall()]
            return result
        finally:
            db.close()

    def _version(self, db, version_id):
        row = db.execute("SELECT v.*,f.original_name,f.storage_name,f.extension,f.mime_type,f.size_bytes,f.sha256,f.source,"
                         "e.id AS extraction_id,e.result AS extraction_status,e.error AS extraction_error,e.text_normalized AS extracted_text,"
                         "d.center,d.module,d.academic_year,d.title AS document_title,d.current_version_id "
                         "FROM versions v JOIN documents d ON d.id=v.document_id JOIN files f ON f.id=v.file_id "
                         "LEFT JOIN extractions e ON e.id=(SELECT id FROM extractions ex WHERE ex.version_id=v.id ORDER BY ex.created_at DESC,ex.rowid DESC LIMIT 1) WHERE v.id=?", (version_id,)).fetchone()
        return dict(row) if row else None

    def upload(self, metadata, upload, actor_id, relation_target=""):
        version_id, document_id = str(uuid4()), ""
        destination = self.storage_dir / version_id / f"original.{upload.extension}"
        destination.parent.mkdir(parents=True, exist_ok=False)
        try:
            with transaction(self.database, immediate=True) as db:
                duplicate = db.execute("SELECT v.id,v.document_id,v.version_no,d.title,d.module,d.academic_year,d.center FROM versions v JOIN files f ON f.id=v.file_id JOIN documents d ON d.id=v.document_id WHERE f.sha256=? LIMIT 1", (upload.sha256,)).fetchone()
                if duplicate:
                    db.execute("INSERT INTO provenance VALUES (?,?,?,?,?,?)",
                               (str(uuid4()), duplicate["id"], actor_id, upload.original_name, metadata["source"], now()))
                    audit(db, actor_id, "duplicate_upload_attempt", "version", duplicate["id"], "Hash exacto; no se creó una nueva versión")
                    return {"duplicate": dict(duplicate)}

                logical = db.execute("SELECT * FROM documents WHERE center=? AND module=? AND academic_year=?",
                                     (metadata["center"], metadata["module"], metadata["academic_year"])).fetchone()
                if logical:
                    if relation_target != logical["id"]:
                        raise ValidationError({"relation_target": "Ya existe una programación para este centro, módulo y curso. Selecciónala para indicar si es revisión o complemento."})
                    if metadata["relation_type"] not in {"revision", "complemento", "diferente"}:
                        raise ValidationError({"relation_type": "Indica si la carga es una revisión, un complemento o todavía no puedes determinar la relación."})
                    if len(metadata["relation_reason"]) < 5:
                        raise ValidationError({"relation_reason": "Explica brevemente la relación con la programación existente (al menos 5 caracteres)."})
                    document_id = logical["id"]
                    previous = db.execute("SELECT id FROM versions WHERE document_id=? ORDER BY version_no DESC LIMIT 1", (document_id,)).fetchone()
                    version_no = db.execute("SELECT COALESCE(MAX(version_no),0)+1 FROM versions WHERE document_id=?", (document_id,)).fetchone()[0]
                    replaces = previous["id"] if previous and metadata["relation_type"] == "revision" else None
                else:
                    if relation_target:
                        raise ValidationError({"relation_target": "La programación seleccionada no coincide con el centro, módulo y curso indicados."})
                    document_id = str(uuid4())
                    version_no, replaces = 1, None
                    db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)",
                               (document_id, metadata["center"], metadata["module"], metadata["academic_year"], metadata["title"], None, actor_id, now()))
                file_id = str(uuid4())
                with open(destination, "xb") as output:
                    output.write(upload.content)
                db.execute("INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?)",
                           (file_id, upload.original_name, f"{version_id}/original.{upload.extension}", upload.extension,
                            upload.mime_type, len(upload.content), upload.sha256, metadata["source"], now()))
                db.execute("INSERT INTO versions (id,document_id,version_no,file_id,status,declared_status,relation_type,relation_reason,declared_version,uploaded_by,created_at,replaces_version_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           (version_id, document_id, version_no, file_id, "pending", metadata["declared_status"], metadata["relation_type"] or None,
                            metadata["relation_reason"], metadata["declared_version"], actor_id, now(), replaces))
                audit(db, actor_id, "upload", "version", version_id, f"Versión {version_no} recibida en Pendiente")
                if version_no == 1:
                    audit(db, actor_id, "create", "document", document_id, "Identidad lógica creada")
            return {"document_id": document_id, "version_id": version_id, "version_no": version_no}
        except Exception:
            if destination.exists():
                destination.unlink(missing_ok=True)
            raise
        finally:
            if destination.parent.exists() and not any(destination.parent.iterdir()):
                destination.parent.rmdir()

    def review_action(self, version_id, actor_id, action, reason=""):
        allowed = {"approve", "return", "reject", "confirm", "publish", "retire"}
        if action not in allowed:
            raise ValidationError({"action": "Acción no válida."})
        if action in {"reject", "return", "retire"} and len(reason.strip()) < 5:
            raise ValidationError({"reason": "Indica el motivo (al menos 5 caracteres)."})
        with transaction(self.database, immediate=True) as db:
            version = self._version(db, version_id)
            if not version:
                return None
            if action in {"approve", "confirm", "publish"} and version["uploaded_by"] == actor_id:
                raise ValidationError({"permiso": "La persona que subió el archivo no puede aprobar ni publicar su propia incorporación."})
            timestamp = now()
            if action == "approve":
                if version["status"] != "pending":
                    raise ValidationError({"estado": "Solo se puede aprobar una versión pendiente."})
                if version["declared_status"] == "borrador":
                    raise ValidationError({"estado": "Una programación declarada como borrador no puede aprobarse para publicación."})
                db.execute("UPDATE versions SET status='approved',approved_by=?,approved_at=? WHERE id=?", (actor_id, timestamp, version_id))
                decision, new_status = "aprobada_para_procesar", "approved"
            elif action == "return":
                if version["status"] not in {"pending", "approved", "pending_error", "ready"}:
                    raise ValidationError({"estado": "Esta versión no se puede devolver en su estado actual."})
                db.execute("UPDATE versions SET status='pending' WHERE id=?", (version_id,))
                decision, new_status = "devuelta_a_revision", "pending"
            elif action == "reject":
                if version["status"] in {"published", "superseded", "retired"}:
                    raise ValidationError({"estado": "No se puede rechazar una versión histórica o publicada."})
                db.execute("UPDATE versions SET status='rejected' WHERE id=?", (version_id,))
                decision, new_status = "rechazada", "rejected"
            elif action == "confirm":
                if version["status"] != "in_preparation" or not version["extracted_text"].strip():
                    raise ValidationError({"estado": "Solo se puede confirmar una extracción con texto y vista previa."})
                db.execute("UPDATE versions SET status='ready',verified_by=?,verified_at=? WHERE id=?", (actor_id, timestamp, version_id))
                decision, new_status = "extraccion_verificada", "ready"
            elif action == "publish":
                if version["status"] != "ready" or not version["extracted_text"].strip():
                    raise ValidationError({"estado": "Solo se puede publicar una extracción verificada."})
                if version["declared_status"] == "borrador":
                    raise ValidationError({"estado": "Una programación declarada como borrador no se puede publicar."})
                old_id = version["current_version_id"]
                if old_id and old_id != version_id:
                    db.execute("UPDATE versions SET status='superseded' WHERE id=? AND status='published'", (old_id,))
                    db.execute("UPDATE versions SET replaces_version_id=? WHERE id=?", (old_id, version_id))
                    audit(db, actor_id, "supersede", "version", old_id, f"Sustituida por {version_id}")
                db.execute("UPDATE versions SET status='published',published_by=?,published_at=? WHERE id=?", (actor_id, timestamp, version_id))
                db.execute("UPDATE documents SET current_version_id=? WHERE id=?", (version_id, version["document_id"]))
                decision, new_status = "publicada", "published"
            else:  # retire
                if version["status"] != "published":
                    raise ValidationError({"estado": "Solo se puede retirar una versión publicada."})
                db.execute("UPDATE versions SET status='retired' WHERE id=?", (version_id,))
                db.execute("UPDATE documents SET current_version_id=NULL WHERE id=? AND current_version_id=?", (version["document_id"], version_id))
                decision, new_status = "retirada", "retired"
            db.execute("INSERT INTO reviews VALUES (?,?,?,?,?,?)", (str(uuid4()), version_id, actor_id, decision, reason.strip(), timestamp))
            audit(db, actor_id, action, "version", version_id, reason.strip() or f"{version['status']} -> {new_status}")
            return new_status

    def process_extraction(self, version_id, actor_id):
        db = get_db(self.database)
        try:
            version = self._version(db, version_id)
        finally:
            db.close()
        if not version:
            return None
        if version["status"] not in {"approved", "pending_error"}:
            raise ValidationError({"estado": "Aprueba primero la versión o corrige el error indicado."})
        path = (self.storage_dir / version["storage_name"]).resolve()
        if self.storage_dir not in path.parents or not path.is_file():
            raise ValidationError({"archivo": "No se encuentra el original en la ruta interna."})
        content = path.read_bytes()
        with transaction(self.database, immediate=True) as db:
            extraction_id = str(uuid4())
            db.execute("INSERT INTO extractions (id,version_id,extractor,result,created_at) VALUES (?,?,?,?,?)",
                       (extraction_id, version_id, "pypdf/python-docx/plain-text", "processing", now()))
            db.execute("UPDATE versions SET status='in_preparation' WHERE id=?", (version_id,))
            audit(db, actor_id, "extraction_started", "version", version_id, "Extracción solicitada")
        try:
            pages = extract_text(content, version["extension"])
            text = "\n\n".join(piece for _, piece in pages if piece.strip()).strip()
            if not text:
                raise ExtractionError("No se obtuvo texto. Puede ser un PDF escaneado o un documento vacío.")
            chunks = split_pages(pages, self.chunk_size, self.overlap)
            if not chunks:
                raise ExtractionError("No se pudieron generar fragmentos con contenido.")
        except ExtractionError as exc:
            with transaction(self.database, immediate=True) as db:
                db.execute("UPDATE extractions SET result='error',error=? WHERE id=?", (str(exc), extraction_id))
                db.execute("UPDATE versions SET status='pending_error' WHERE id=?", (version_id,))
                audit(db, actor_id, "extraction_error", "version", version_id, str(exc))
            return "pending_error"
        with transaction(self.database, immediate=True) as db:
            db.execute("DELETE FROM chunks WHERE extraction_id=?", (extraction_id,))
            for locator, piece, content_hash in chunks:
                db.execute("INSERT INTO chunks VALUES (?,?,?,?,?)", (str(uuid4()), extraction_id, locator, piece, content_hash))
            db.execute("UPDATE extractions SET result='success',text_normalized=?,hash_text=? WHERE id=?", (text, __import__('hashlib').sha256(text.encode('utf-8')).hexdigest(), extraction_id))
            db.execute("UPDATE versions SET status='in_preparation' WHERE id=?", (version_id,))
            audit(db, actor_id, "extraction_complete", "version", version_id, f"Extracción correcta; {len(chunks)} fragmentos generados")
        return "in_preparation"

    def download_path(self, version_id):
        db = get_db(self.database)
        try:
            row = db.execute("SELECT f.storage_name FROM versions v JOIN files f ON f.id=v.file_id WHERE v.id=?", (version_id,)).fetchone()
        finally:
            db.close()
        if not row:
            return None
        path = (self.storage_dir / row["storage_name"]).resolve()
        if self.storage_dir not in path.parents or not path.is_file():
            return None
        return path

    def review_queue(self, filters=None):
        filters = filters or {}
        sql = """SELECT v.*,f.original_name,e.error AS extraction_error,e.result AS extraction_status,
                        d.center,d.module,d.academic_year,d.title AS document_title,u.username AS uploader
                 FROM versions v JOIN files f ON f.id=v.file_id JOIN documents d ON d.id=v.document_id JOIN users u ON u.id=v.uploaded_by
                 LEFT JOIN extractions e ON e.id=(SELECT id FROM extractions ex WHERE ex.version_id=v.id ORDER BY ex.created_at DESC,ex.rowid DESC LIMIT 1)
                 WHERE v.status NOT IN ('published','superseded','retired','rejected')"""
        params = []
        for key, column in (("module", "d.module"), ("academic_year", "d.academic_year"), ("status", "v.status")):
            value = (filters.get(key) or "").strip()
            if value:
                sql += f" AND {column}=?"
                params.append(value)
        sql += " ORDER BY v.created_at ASC"
        db = get_db(self.database)
        try:
            return [dict(row) for row in db.execute(sql, params).fetchall()]
        finally:
            db.close()

    def rag_chunks(self, module, academic_year, center=None):
        sql = """SELECT c.id AS fragment_id,d.id AS document_id,v.id AS version_id,
                        'programacion_didactica' AS category,d.module,d.academic_year,
                        'local:' || v.id AS source_uri,c.locator,c.text,
                        v.status AS publication_status,c.content_hash,f.original_name,d.title,v.version_no,f.source
                 FROM chunks c JOIN extractions e ON e.id=c.extraction_id JOIN versions v ON v.id=e.version_id
                 JOIN files f ON f.id=v.file_id JOIN documents d ON d.id=v.document_id
                 WHERE e.result='success' AND e.id=(SELECT id FROM extractions ex WHERE ex.version_id=v.id ORDER BY ex.created_at DESC,ex.rowid DESC LIMIT 1)
                   AND v.status='published' AND d.current_version_id=v.id AND d.module=? AND d.academic_year=?"""
        params = [module, academic_year]
        if center:
            sql += " AND d.center=?"
            params.append(center)
        db = get_db(self.database)
        try:
            return [dict(r) for r in db.execute(sql + " ORDER BY d.title,c.rowid", params).fetchall()]
        finally:
            db.close()

    def metrics(self):
        db = get_db(self.database)
        try:
            states = [dict(r) for r in db.execute("SELECT status,COUNT(*) AS total FROM versions GROUP BY status ORDER BY status").fetchall()]
            modules = [dict(r) for r in db.execute("SELECT d.module,COUNT(DISTINCT d.id) AS documents,COUNT(c.id) AS fragments FROM documents d LEFT JOIN versions v ON v.id=d.current_version_id AND v.status='published' LEFT JOIN extractions e ON e.version_id=v.id AND e.result='success' AND e.id=(SELECT id FROM extractions ex WHERE ex.version_id=v.id ORDER BY ex.created_at DESC,ex.rowid DESC LIMIT 1) LEFT JOIN chunks c ON c.extraction_id=e.id GROUP BY d.module ORDER BY d.module").fetchall()]
            return {"states": states, "modules": modules}
        finally:
            db.close()
