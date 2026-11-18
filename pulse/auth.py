"""Cookie-session auth for operator surfaces (starlette SessionMiddleware).

One session, two guards:
- ``require_api_user`` / ``require_admin``: JSON APIs -> 401 / 403
- ``require_web_user``: HTML pages -> 303 redirect to /login?next=...
"""
import urllib.parse

from fastapi import HTTPException, Request

from . import users

SESSION_KEY = "uid"


def login_session(request: Request, user: dict) -> None:
    request.session[SESSION_KEY] = user["id"]


def logout_session(request: Request) -> None:
    request.session.pop(SESSION_KEY, None)


def current_user(request: Request) -> dict | None:
    uid = request.session.get(SESSION_KEY)
    return users.get_user(uid) if uid else None


def _login_redirect(path: str) -> str:
    return "/login?next=" + urllib.parse.quote(path or "/")


def require_api_user(request: Request) -> dict:
    user = current_user(request)
    if user is None:
        raise HTTPException(401, "login required (POST /api/login)")
    return user


def require_admin(request: Request) -> dict:
    user = require_api_user(request)
    if user["role"] != "admin":
        raise HTTPException(403, "admin role required")
    return user


def require_web_user(request: Request) -> dict:
    """Dependency for HTML pages: 303 to /login (with a safe `next`) when anon."""
    user = current_user(request)
    if user is None:
        raise HTTPException(303, detail="redirecting to login",
                            headers={"Location": _login_redirect(request.url.path)})
    return user
