#!/usr/bin/env python3
"""Restaura una copia en un directorio nuevo; nunca sobrescribe datos existentes."""
import argparse
import os
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

parser = argparse.ArgumentParser(description="Restaurar una copia en una carpeta nueva")
parser.add_argument("archive")
parser.add_argument("--target", required=True, help="Directorio nuevo y separado; debe no existir o estar vacío")
args = parser.parse_args()
archive_path = Path(args.archive).expanduser().resolve()
target = Path(args.target).expanduser().resolve()
if target.exists() and any(target.iterdir()):
    raise SystemExit("El destino no está vacío. Elige una ubicación separada para no sobrescribir la instalación activa.")
target.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(archive_path) as archive:
    names = archive.namelist()
    if "docia.sqlite3" not in names:
        raise SystemExit("El ZIP no contiene docia.sqlite3.")
    for name in names:
        member = PurePosixPath(name)
        windows = PureWindowsPath(name)
        if member.is_absolute() or windows.is_absolute() or windows.drive or ".." in member.parts:
            raise SystemExit("La copia contiene una ruta no permitida.")
    for name in names:
        if name == "backup-info.txt":
            continue
        destination = target.joinpath(*PurePosixPath(name).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(name) as src, destination.open("wb") as dst:
            dst.write(src.read())
print(f"Copia restaurada en {target}. Configura DATA_DIR={target} para probarla sin modificar la instalación activa.")
