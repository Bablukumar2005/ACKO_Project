"""User accounts for operator login: bcrypt hashing + `users` table access.

Same access pattern as store.py — every read/write goes through db.tx(). This
module only touches the `users` table; nothing here reads monitoring data.
Passwords are never logged, generated or returned — only bcrypt hashes.
"""
import logging
import sqlite3

import bcrypt

from . import config, db
from .util import now_iso

log = logging.getLogger("pulse.users")

ROLES = ("admin", "operator", "viewer")


# ------------------------------------------------------------------ hashing

def hash_password(password: str) -> str:
    """bcrypt with a per-user random salt (stored inside the hash string)."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:  # malformed hash in the DB: treat as invalid, never crash
        return False


# ------------------------------------------------------------------- store

def create_user(name: str, email: str, password: str, role: str = "operator") -> dict | None:
    """Insert a user; returns None when the email is already registered."""
    try:
        with db.tx(write=True) as c:
            cur = c.execute(
                """INSERT INTO users (name, email, password_hash, role, created_at)
                   VALUES (?,?,?,?,?)""",
                (name.strip(), email.strip().lower(), hash_password(password), role, now_iso()),
            )
            user_id = cur.lastrowid
    except sqlite3.IntegrityError:  # UNIQUE(email)
        return None
    return get_user(user_id)


def get_user(user_id: int) -> dict | None:
    with db.tx() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_email(email: str) -> dict | None:
    with db.tx() as c:
        row = c.execute("SELECT * FROM users WHERE email=?",
                        (email.strip().lower(),)).fetchone()
        return dict(row) if row else None


def count_users() -> int:
    with db.tx() as c:
        return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def public_user(user: dict) -> dict:
    """Safe view for API responses and pages: never the password hash."""
    return {k: v for k, v in user.items() if k != "password_hash"}


def register_user(name: str, email: str, password: str) -> dict | None:
    """Self-service account creation (role 'operator').

    Returns the new user, or None when the email is already registered.
    Email is normalised to lowercase before the UNIQUE check.
    """
    return create_user(name, email, password, role="operator")


# ---------------------------------------------------------------- bootstrap

def ensure_bootstrap_admin() -> None:
    """Create the first admin from PULSE_ADMIN_PASSWORD (idempotent).

    No password is ever generated or logged: when the env var is unset the
    message simply points at the public sign-up page, which any visitor can use
    to create the first account.
    """
    if count_users():
        return
    if not config.ADMIN_PASSWORD:
        log.info("users table empty and PULSE_ADMIN_PASSWORD unset — create the "
                 "first account on the sign-up page (/signup)")
        return
    if create_user("Admin", config.ADMIN_EMAIL, config.ADMIN_PASSWORD, role="admin"):
        log.info("bootstrap admin created for %s (from PULSE_ADMIN_PASSWORD)",
                 config.ADMIN_EMAIL)
