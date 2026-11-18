"""Operator login: users table, bcrypt hashing, sessions, route protection.

Scope decided in DECISIONS.md: login protects the operator dashboard/pages and
every mutation (API + web forms); public status/metrics and all reads stay open.
"""
import pytest

from pulse import users

from .test_api import TEST_ADMIN_PASSWORD, anon_client, client  # fixtures reused


# ------------------------------------------------------------------ passwords

def test_bcrypt_hash_roundtrip():
    h = users.hash_password("correct horse battery staple")
    assert h.startswith("$2") and "$" in h[3:], "must be a bcrypt-format hash"
    assert users.verify_password("correct horse battery staple", h)
    assert not users.verify_password("wrong password", h)


def test_malformed_hash_is_invalid_not_crash():
    assert users.verify_password("x", "not-a-bcrypt-hash") is False


def test_create_user_stores_hash_not_password(in_memory_db):
    user = users.create_user("Ops", "ops@pulse.local", "super-secret-9")
    assert user["password_hash"] != "super-secret-9"
    assert user["email"] == "ops@pulse.local"
    assert users.verify_password("super-secret-9", user["password_hash"])


def test_duplicate_email_rejected(in_memory_db):
    assert users.create_user("A", "dup@pulse.local", "long-password-1") is not None
    assert users.create_user("B", "dup@pulse.local", "long-password-2") is None


def test_email_normalised_to_lowercase(in_memory_db):
    users.create_user("A", "MiXeD@Pulse.LOCAL", "long-password-1")
    assert users.get_user_by_email("mixed@pulse.local") is not None


def test_public_user_never_contains_hash(in_memory_db):
    user = users.create_user("A", "safe@pulse.local", "long-password-1")
    assert "password_hash" not in users.public_user(user)


# ------------------------------------------------------------------ bootstrap

def test_bootstrap_admin_idempotent(in_memory_db, monkeypatch):
    from pulse import config
    monkeypatch.setattr(config, "ADMIN_PASSWORD", TEST_ADMIN_PASSWORD)
    users.ensure_bootstrap_admin()
    users.ensure_bootstrap_admin()  # second call must not duplicate
    assert users.count_users() == 1


def test_bootstrap_admin_matches_env_password(in_memory_db, monkeypatch):
    from pulse import config
    monkeypatch.setattr(config, "ADMIN_PASSWORD", TEST_ADMIN_PASSWORD)
    users.ensure_bootstrap_admin()
    admin = users.get_user_by_email(config.ADMIN_EMAIL)
    assert users.verify_password(TEST_ADMIN_PASSWORD, admin["password_hash"])


# ------------------------------------------------------------------ API auth

def test_login_success_and_me(client):
    me = client.get("/api/me").json()
    assert me["email"] == "admin@pulse.local"
    assert me["role"] == "admin"
    assert "password_hash" not in me


def test_login_wrong_password(anon_client):
    r = anon_client.post("/api/login", json={
        "email": "admin@pulse.local", "password": "definitely-wrong"})
    assert r.status_code == 401


def test_login_unknown_email(anon_client):
    r = anon_client.post("/api/login", json={
        "email": "nobody@pulse.local", "password": "whatever-123"})
    assert r.status_code == 401


def test_bad_email_format_is_422(anon_client):
    r = anon_client.post("/api/login", json={"email": "not-an-email", "password": "x"})
    assert r.status_code == 422


def test_logout_clears_session(client):
    assert client.get("/api/me").status_code == 200
    assert client.post("/api/logout").json() == {"ok": True}
    assert client.get("/api/me").status_code == 401


def test_anon_mutations_rejected(anon_client):
    assert anon_client.post("/api/services", json={
        "name": "x", "health_url": "http://localhost:9/health"}).status_code == 401
    assert anon_client.patch("/api/services/1", json={}).status_code == 401
    assert anon_client.delete("/api/services/1").status_code == 401
    assert anon_client.post("/api/incidents/1/ack").status_code == 401
    assert anon_client.post("/api/incidents/1/notes", json={"note": "n"}).status_code == 401
    assert anon_client.post("/api/incidents/1/resolve").status_code == 401
    assert anon_client.post("/api/users", json={
        "name": "x", "email": "x@pulse.local", "password": "long-enough-8"}).status_code == 401


def test_anon_reads_stay_open(anon_client):
    # decided scope: monitoring reads are public even before login
    for path in ("/api/services", "/api/status", "/api/metrics", "/api/incidents"):
        assert anon_client.get(path).status_code == 200, path


def test_anon_operator_pages_redirect_to_login(anon_client):
    for path in ("/", "/services/1", "/incidents/1"):
        r = anon_client.get(path)
        assert r.status_code == 303, path
        assert r.headers["location"].startswith("/login?next="), path


def test_anon_form_actions_redirect(anon_client):
    for path in ("/incidents/1/ack", "/incidents/1/note", "/incidents/1/resolve"):
        r = anon_client.post(path)
        assert r.status_code == 303, path
        assert r.headers["location"].startswith("/login?next="), path


def test_public_pages_need_no_login(anon_client):
    for path in ("/status", "/metrics", "/login"):
        assert anon_client.get(path).status_code == 200, path


def test_user_creation_requires_admin(anon_client, monkeypatch):
    from pulse import config
    # create a plain operator via admin (first: admin's own session)
    _login(anon_client)
    r = anon_client.post("/api/users", json={
        "name": "Ops", "email": "ops@pulse.local",
        "password": "ops-password-1", "role": "operator"})
    assert r.status_code == 201 and r.json()["role"] == "operator"
    anon_client.post("/api/logout")

    # operator session: authenticated but NOT admin
    r = anon_client.post("/api/login", json={
        "email": "ops@pulse.local", "password": "ops-password-1"})
    assert r.status_code == 200 and r.json()["role"] == "operator"
    assert anon_client.post("/api/users", json={
        "name": "Y", "email": "y@pulse.local", "password": "long-enough-8"}).status_code == 403
    # but a plain operator CAN do operator mutations
    assert anon_client.post("/api/services", json={
        "name": "x", "health_url": "http://localhost:9/health"}).status_code == 201


def test_duplicate_email_api_conflict(anon_client):
    _login(anon_client)
    body = {"name": "A", "email": "same@pulse.local", "password": "long-enough-8"}
    assert anon_client.post("/api/users", json=body).status_code == 201
    assert anon_client.post("/api/users", json=body).status_code == 409


def test_password_hash_never_leaks(client, anon_client, in_memory_db):
    from pulse import store
    service = store.create_service({"name": "leak", "health_url": "http://localhost:9/health"})
    surfaces = [
        client.get("/api/me").text,
        client.get("/").text,
        client.get("/login").text,
        client.get("/status").text,
        client.get(f"/api/services/{service['id']}").text,
    ]
    for html in surfaces:
        assert "$2b$" not in html and "password_hash" not in html


def _login(c):
    return c.post("/api/login", json={
        "email": "admin@pulse.local", "password": TEST_ADMIN_PASSWORD})


# ------------------------------------------------------- self-registration API

def test_register_via_api_creates_operator_and_signs_in(anon_client):
    r = anon_client.post("/api/register", json={
        "name": "Priya", "email": "Priya@Acko.IN", "password": "strong-pass-123"})
    assert r.status_code == 201
    body = r.json()
    assert body["role"] == "operator" and body["email"] == "priya@acko.in"
    assert "password_hash" not in body
    me = anon_client.get("/api/me")          # registration signs the user in
    assert me.status_code == 200 and me.json()["email"] == "priya@acko.in"


def test_register_duplicate_email_conflict(anon_client):
    body = {"name": "X", "email": "dup-reg@acko.in", "password": "strong-pass-123"}
    assert anon_client.post("/api/register", json=body).status_code == 201
    assert anon_client.post("/api/register", json=body).status_code == 409


def test_register_cannot_take_over_admin_email(anon_client):
    r = anon_client.post("/api/register", json={
        "name": "Evil", "email": "admin@pulse.local", "password": "strong-pass-123"})
    assert r.status_code == 409
    admin = users.get_user_by_email("admin@pulse.local")
    # the ORIGINAL admin password still works — account untouched
    assert users.verify_password(TEST_ADMIN_PASSWORD, admin["password_hash"])


def test_register_rejects_weak_password(anon_client):
    r = anon_client.post("/api/register", json={
        "name": "A", "email": "weak@acko.in", "password": "short"})
    assert r.status_code == 422
    assert users.get_user_by_email("weak@acko.in") is None


def test_register_rejects_bad_email(anon_client):
    r = anon_client.post("/api/register", json={
        "name": "A", "email": "not-an-email", "password": "long-enough-8"})
    assert r.status_code == 422


def test_register_login_logout_cycle(anon_client):
    assert anon_client.post("/api/register", json={
        "name": "Cycle", "email": "cycle@acko.in", "password": "cycle-pass-1"}).status_code == 201
    assert anon_client.post("/api/logout").json() == {"ok": True}
    assert anon_client.get("/api/me").status_code == 401
    r = anon_client.post("/api/login", json={
        "email": "cycle@acko.in", "password": "cycle-pass-1"})
    assert r.status_code == 200 and r.json()["role"] == "operator"


def test_registration_preserves_admin_and_monitoring(anon_client):
    from pulse import store
    service = store.create_service({"name": "mon", "health_url": "http://localhost:9/health"})
    before = users.count_users()
    assert anon_client.post("/api/register", json={
        "name": "New", "email": "preserve@acko.in", "password": "strong-pass-1"}).status_code == 201
    assert users.get_user_by_email("admin@pulse.local") is not None
    assert store.get_service_by_name("mon") is not None
    assert users.count_users() == before + 1


def test_registered_user_passes_protection(anon_client):
    anon_client.post("/api/register", json={
        "name": "Op", "email": "opuser@acko.in", "password": "strong-pass-1"})
    assert anon_client.get("/api/me").status_code == 200
    assert anon_client.get("/", follow_redirects=True).status_code == 200
    assert anon_client.post("/api/services", json={
        "name": "opsvc", "health_url": "http://localhost:9/health"}).status_code == 201


# ------------------------------------------------------------- sign-up web UI

def test_signup_page_renders_with_toggle_and_links(anon_client):
    html = anon_client.get("/signup").text
    assert 'action="/signup"' in html and 'href="/login"' in html
    assert 'pw-toggle' in html and 'data-target="password"' in html   # show/hide
    assert 'name="confirm"' in html


def test_login_page_links_to_signup_and_has_toggle(anon_client):
    html = anon_client.get("/login").text
    assert 'href="/signup"' in html and 'pw-toggle' in html


def test_signup_web_creates_account_and_session(anon_client):
    r = anon_client.post("/signup", data={
        "name": "Web User", "email": "web@acko.in",
        "password": "web-pass-123", "confirm": "web-pass-123"},
        follow_redirects=True)
    assert r.status_code == 200 and "Web User" in r.text   # dashboard shows their name
    user = users.get_user_by_email("web@acko.in")
    assert user is not None and user["role"] == "operator"


def test_signup_web_mismatch_shows_error_no_user(anon_client):
    r = anon_client.post("/signup", data={
        "name": "W", "email": "mismatch@acko.in",
        "password": "web-pass-123", "confirm": "different-999"})
    assert r.status_code == 422
    assert "do not match" in r.text.lower()
    assert users.get_user_by_email("mismatch@acko.in") is None


def test_signup_web_duplicate_email_error(anon_client):
    data = {"name": "D", "email": "webdup@acko.in",
            "password": "web-pass-123", "confirm": "web-pass-123"}
    assert anon_client.post("/signup", data=data, follow_redirects=True).status_code == 200
    anon_client.post("/api/logout")
    r = anon_client.post("/signup", data=data)
    assert r.status_code == 422 and "already registered" in r.text.lower()
    assert users.count_users() >= 1  # no duplicate row was added by this POST


def test_signup_web_short_password_error(anon_client):
    r = anon_client.post("/signup", data={
        "name": "S", "email": "shortpw@acko.in", "password": "short", "confirm": "short"})
    assert r.status_code == 422 and "at least 8" in r.text.lower()
    assert users.get_user_by_email("shortpw@acko.in") is None


def test_signup_web_bad_email_error(anon_client):
    r = anon_client.post("/signup", data={
        "name": "B", "email": "nope", "password": "web-pass-123", "confirm": "web-pass-123"})
    assert r.status_code == 422 and "valid email" in r.text.lower()


def test_signup_web_keeps_name_and_email_but_never_password(anon_client):
    r = anon_client.post("/signup", data={
        "name": "K", "email": "keep@acko.in", "password": "web-pass-123",
        "confirm": "wrong-confirm"})
    assert r.status_code == 422
    assert 'value="K"' in r.text and 'value="keep@acko.in"' in r.text
    assert "web-pass-123" not in r.text


# ------------------------------------------------------------- log hygiene

def test_passwords_never_reach_the_logs(anon_client, caplog):
    import logging
    with caplog.at_level(logging.DEBUG):
        anon_client.post("/api/register", json={
            "name": "L", "email": "logs@acko.in", "password": "super-secret-pass-9"})
        anon_client.post("/api/login", json={
            "email": "logs@acko.in", "password": "wrong-pass-xyz"})
        anon_client.post("/signup", data={
            "name": "L2", "email": "logs2@acko.in",
            "password": "form-secret-123", "confirm": "form-secret-123"})
    assert "super-secret-pass-9" not in caplog.text
    assert "wrong-pass-xyz" not in caplog.text
    assert "form-secret-123" not in caplog.text
