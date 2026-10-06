import os

from waitress import serve

from app import create_app

app = create_app()

if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5051"))
    print(f"Gestor de programaciones DocIA+ disponible en http://{host}:{port}")
    print("Para cerrar, pulsa Ctrl+C en esta terminal.")
    serve(app, host=host, port=port, threads=4, max_request_body_size=app.config["MAX_CONTENT_LENGTH"])
