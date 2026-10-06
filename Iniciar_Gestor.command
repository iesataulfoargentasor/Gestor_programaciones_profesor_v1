#!/bin/zsh
set -e
cd "${0:A:h}"

if [[ ! -x ".venv/bin/python" ]]; then
  print "No encuentro el entorno Python .venv. Consulta la sección macOS del README.md."
  read '?Pulsa Intro para cerrar...'
  exit 1
fi

if ! .venv/bin/python -c 'import flask, waitress, dotenv, pypdf, docx' >/dev/null 2>&1; then
  print "Faltan dependencias. Activa la instalación indicada en el README.md."
  read '?Pulsa Intro para cerrar...'
  exit 1
fi

.venv/bin/python scripts/init_project.py
(sleep 2; open "http://127.0.0.1:5051/") >/dev/null 2>&1 &
.venv/bin/python run.py
