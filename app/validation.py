import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

from werkzeug.utils import secure_filename


class ValidationError(Exception):
    def __init__(self, errors):
        self.errors = errors


@dataclass
class Upload:
    content: bytes
    original_name: str
    extension: str
    mime_type: str
    sha256: str


def validate_upload(file_storage, max_bytes, allowed):
    if not file_storage or not file_storage.filename:
        raise ValidationError({"archivo": "Selecciona un archivo."})
    original = Path(file_storage.filename.replace("\\", "/")).name
    if len(original) > 180 or not secure_filename(original):
        raise ValidationError({"archivo": "El nombre del archivo no es válido."})
    extension = Path(original).suffix.lower().lstrip(".")
    if extension not in allowed:
        raise ValidationError({"archivo": "Formatos admitidos para el piloto: PDF, DOCX y TXT."})
    content = file_storage.stream.read(max_bytes + 1)
    if not content:
        raise ValidationError({"archivo": "El archivo está vacío."})
    if len(content) > max_bytes:
        raise ValidationError({"archivo": f"El límite de tamaño es {max_bytes // (1024 * 1024)} MB."})
    mime = "application/octet-stream"
    if extension == "pdf":
        if not content.startswith(b"%PDF-"):
            raise ValidationError({"archivo": "La extensión indica PDF, pero el contenido no parece ser un PDF."})
        mime = "application/pdf"
    elif extension == "docx":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = set(archive.namelist())
                if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                    raise ValueError
        except (zipfile.BadZipFile, ValueError):
            raise ValidationError({"archivo": "El contenido no parece ser un documento DOCX válido."})
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif extension == "txt":
        try:
            content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValidationError({"archivo": "El archivo TXT debe estar codificado en UTF-8."})
        mime = "text/plain; charset=utf-8"
    return Upload(content, original, extension, mime, hashlib.sha256(content).hexdigest())


def validate_metadata(form):
    values = {key: form.get(key, "").strip() for key in (
        "center", "module", "academic_year", "title", "source", "declared_status", "declared_version", "relation_type", "relation_reason")}
    errors = {}
    for key, label, limit in (("center", "Centro", 120), ("module", "Módulo", 80), ("academic_year", "Curso académico", 20), ("title", "Título", 200), ("source", "Procedencia", 250)):
        if not values[key]:
            errors[key] = f"El campo {label.lower()} es obligatorio."
        elif len(values[key]) > limit:
            errors[key] = f"El campo {label.lower()} supera el límite de {limit} caracteres."
    if values["declared_status"] not in {"aprobada", "borrador", "no_confirmada"}:
        errors["declared_status"] = "Selecciona el estado declarado del documento."
    if values["declared_version"] and len(values["declared_version"]) > 40:
        errors["declared_version"] = "La versión declarada supera 40 caracteres."
    if values["relation_type"] not in {"", "revision", "complemento", "diferente"}:
        errors["relation_type"] = "Tipo de relación no válido."
    if len(values["relation_reason"]) > 500:
        errors["relation_reason"] = "El motivo no puede superar 500 caracteres."
    if errors:
        raise ValidationError(errors)
    return values
