import hmac
import secrets
from functools import wraps

from flask import abort, g, redirect, request, session, url_for

PERMISSIONS = {
    "collaborator": {"read", "upload", "download"},
    "reviewer": {"read", "upload", "download", "review", "publish"},
    "admin": {"read", "upload", "download", "review", "publish", "admin"},
}


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def verify_csrf(supplied):
    expected = session.get("csrf", "")
    return bool(expected) and hmac.compare_digest(expected, supplied or "")


def has_permission(permission, user=None):
    user = user if user is not None else g.get("user")
    return bool(user and user["active"] and permission in PERMISSIONS.get(user["role"], set()))


def require(permission):
    def decorate(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not g.get("user"):
                return redirect(url_for("auth.login", next=request.path))
            if not has_permission(permission):
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return decorate
