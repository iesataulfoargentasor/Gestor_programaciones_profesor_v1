#!/usr/bin/env python3
import getpass
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash
from app.database import get_db, initialize_database

env_file = ROOT / ".env"
if not env_file.exists():
    env_file.write_text("SECRET_KEY=" + secrets.token_urlsafe(48) + "\n", encoding="utf-8")
    try:
        env_file.chmod(0o600)
    except OSError:
        pass
load_dotenv(env_file, override=True)
if len(os.getenv("SECRET_KEY", "")) < 32:
    existing = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    kept = [line for line in existing.splitlines() if not line.startswith("SECRET_KEY=")]
    kept.append("SECRET_KEY=" + secrets.token_urlsafe(48))
    env_file.write_text("\n".join(kept) + "\n", encoding="utf-8")
    try:
        env_file.chmod(0o600)
    except OSError:
        pass
    load_dotenv(env_file, override=True)
data_dir = Path(os.getenv("DATA_DIR", "instance")).expanduser()
if not data_dir.is_absolute():
    data_dir = ROOT / data_dir
data_dir = data_dir.resolve()
database = data_dir / "docia.sqlite3"
initialize_database(str(database))

db = get_db(str(database))
try:
    exists = db.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone()
finally:
    db.close()
if not exists:
    print("Crear la primera cuenta administradora del prototipo.")
    username = input("Usuario administrador: ").strip()
    password = getpass.getpass("Contraseña (mínimo 12 caracteres): ")
    confirm = getpass.getpass("Repite la contraseña: ")
    if len(username) < 3 or len(username) > 80 or len(password) < 12 or password != confirm:
        raise SystemExit("Usuario inválido, contraseña corta o contraseñas distintas; vuelve a ejecutar la inicialización.")
    db = get_db(str(database))
    try:
        db.execute("INSERT INTO users VALUES (?,?,?,?,?,?)", (str(uuid4()), username, generate_password_hash(password), "admin", 1, datetime.now(timezone.utc).isoformat()))
    finally:
        db.close()
    print("Cuenta administradora creada.")
else:
    print("Ya existe una cuenta administradora; no se ha modificado ningún usuario.")
print("Base de datos:", database)
print("Originales:", data_dir / "originales")
