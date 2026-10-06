#!/usr/bin/env python3
import argparse
import getpass
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash
from app.database import get_db, initialize_database

parser = argparse.ArgumentParser(description="Crear una cuenta local para el prototipo")
parser.add_argument("username")
parser.add_argument("--role", choices=("collaborator", "reviewer", "admin"), required=True)
args = parser.parse_args()
load_dotenv(ROOT / ".env")
data_dir = Path(os.getenv("DATA_DIR", "instance")).expanduser()
if not data_dir.is_absolute():
    data_dir = ROOT / data_dir
data_dir = data_dir.resolve()
database = data_dir / "docia.sqlite3"
initialize_database(str(database))
password = getpass.getpass("Contraseña (mínimo 12 caracteres): ")
confirm = getpass.getpass("Repite la contraseña: ")
if len(args.username.strip()) < 3 or len(password) < 12 or password != confirm:
    raise SystemExit("Usuario inválido, contraseña corta o contraseñas distintas.")
db = get_db(str(database))
try:
    db.execute("INSERT INTO users VALUES (?,?,?,?,?,?)", (str(uuid4()), args.username.strip(), generate_password_hash(password), args.role, 1, datetime.now(timezone.utc).isoformat()))
except Exception as exc:
    raise SystemExit("No se pudo crear la cuenta. Comprueba que el usuario no exista.") from exc
finally:
    db.close()
print(f"Cuenta {args.username} creada con rol {args.role}.")
