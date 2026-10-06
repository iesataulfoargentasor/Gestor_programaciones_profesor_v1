from uuid import uuid4

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from app.database import get_db

auth = Blueprint("auth", __name__)


@auth.route("/login", methods=["GET", "POST"])
def login():
    if g.get("user"):
        return redirect(url_for("documents.catalog"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()[:80]
        password = request.form.get("password", "")
        db = get_db(current_app.config["DATABASE"])
        try:
            user = db.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
        finally:
            db.close()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["username"] = username
            session.permanent = True
            flash("Has iniciado sesión.", "success")
            return redirect(url_for("documents.catalog"))
        flash("Usuario o contraseña no válidos.", "error")
    return render_template("login.html")


@auth.post("/logout")
def logout():
    username = session.get("username")
    session.clear()
    flash(f"Sesión cerrada ({username}).", "success")
    return redirect(url_for("auth.login"))
