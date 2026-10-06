#!/usr/bin/env python3
"""Crea una copia ZIP coherente de la base SQLite y los originales locales."""
import argparse
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
data = Path(os.getenv("DATA_DIR", "instance")).expanduser()
if not data.is_absolute():
    data = ROOT / data
data = data.resolve()
database = data / "docia.sqlite3"
files = data / "originales"
parser = argparse.ArgumentParser(description="Crear copia de seguridad en ZIP")
parser.add_argument("--output", default=str(ROOT / "backups" / ("docia-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".zip")))
args = parser.parse_args()
output = Path(args.output).expanduser().resolve()
if not database.is_file():
    raise SystemExit(f"No se encuentra la base de datos: {database}")
output.parent.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as temp:
    snapshot = Path(temp) / "docia.sqlite3"
    src = sqlite3.connect(database)
    dst = sqlite3.connect(snapshot)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(snapshot, "docia.sqlite3")
        if files.exists():
            for path in files.rglob("*"):
                if path.is_file():
                    archive.write(path, Path("originales") / path.relative_to(files))
        archive.writestr("backup-info.txt", "Copia del gestor DocIA+\n" + datetime.now(timezone.utc).isoformat() + "\n")
print(f"Copia creada: {output}")
