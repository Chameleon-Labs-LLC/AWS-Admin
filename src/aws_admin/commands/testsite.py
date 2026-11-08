"""`aws-admin testsite`: an agent-managed test copy of an Amplify SSR app.

One separate Amplify app plus one separate database on the same RDS instance,
so a risky change can be proven on real Amplify before it reaches production.

Isolation is the point of every function here:

* The test app is a *different* Amplify app, so none of the source app's
  app-level env vars (production secrets) exist on it.
* Its ``DATABASE_URL`` names a separate database owned by a separate login
  role. Amplify builds run ``prisma migrate deploy``, so this is the only
  database a test build can ever migrate.
* It has no compute role, so its SSR runtime holds no AWS credentials.
* Every branch sits behind Amplify basic auth.

Secret values (role password, AUTH_SECRET, basic-auth and test-account
passwords) are generated here, kept only in the encrypted local vault, and
never returned in any output string. Outputs carry key names, statuses and
URLs only. Deployment-specific names come from ``[testsite]`` in config.toml.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.cookiejar
import json
import os
import secrets as _secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .. import config, vault
from ..aws_client import amplify_client
from ..db import connection

# Cloudflare's published always-pass Turnstile test keys (public, documented at
# developers.cloudflare.com/turnstile/troubleshooting/testing/). Not secrets.
TURNSTILE_TEST_SITE_KEY = "1x00000000000000000000AA"
TURNSTILE_TEST_SECRET_KEY = "1x0000000000000000000000000000000AA"

ADMIN_EMAIL = "agent-admin@example.com"
USER_EMAIL = "agent-user@example.com"
BASIC_AUTH_USER = "agent"
SECRET_KEYS = ("db_password", "auth_secret", "basic_password",
               "admin_password", "user_password")
APP_ENV_KEYS = ("AUTH_SECRET", "AUTH_TRUST_HOST", "DATABASE_URL",
                "NEXT_PUBLIC_TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY")
BRANCH_ENV_KEYS = ("AUTH_URL", "NEXTAUTH_URL", "SITE_URL")


# --------------------------------------------------------------------------
# Settings, state and secrets
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TestSiteSettings:
    app_name: str
    source_app: str
    db_name: str
    db_role: str


def settings() -> TestSiteSettings:
    """Read ``[testsite]`` from config.toml and refuse any overlap with production."""
    data = config.load_raw()
    try:
        ts = data["testsite"]
        out = TestSiteSettings(
            app_name=ts["app_name"],
            source_app=ts["source_app"],
            db_name=ts["db_name"],
            db_role=ts["db_role"],
        )
    except (KeyError, TypeError) as exc:
        raise config.ConfigError(f"config.toml needs a [testsite] table: missing {exc}") from exc
    _guard(out)
    return out


def _guard(ts: TestSiteSettings) -> None:
    if ts.db_name.lower() == str(config.DB_NAME).lower():
        raise ValueError("testsite db_name equals the production database name; refusing.")
    if ts.db_role.lower() == config.DB_USER.lower():
        raise ValueError("testsite db_role equals the admin DB user; refusing.")
    configured = {name.lower() for name, _ in config.app_aliases()}
    if ts.app_name.lower() in configured:
        raise ValueError(
            f"testsite app_name '{ts.app_name}' is a configured production app; refusing.")
    for value in (ts.db_name, ts.db_role):
        if not value.replace("_", "").isalnum() or not value.islower():
            raise ValueError("testsite db_name/db_role must be lowercase [a-z0-9_].")


def _dir() -> Path:
    return config.state_dir() / "testsite"


def _state_path() -> Path:
    return _dir() / "state.json"


def _secrets_path() -> Path:
    return _dir() / "secrets.enc"


def _load_state() -> dict:
    path = _state_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _save_state(state: dict) -> None:
    _dir().mkdir(parents=True, exist_ok=True)
    os.chmod(_dir(), 0o700)
    vault._write_private(_state_path(), json.dumps(state, indent=2, sort_keys=True).encode())


def _load_secrets(create: bool = False) -> dict:
    path = _secrets_path()
    if path.exists():
        data = vault.decrypt(path.read_bytes())
    elif create:
        data = {}
    else:
        raise FileNotFoundError("No testsite secrets yet. Run `aws-admin testsite up` first.")
    changed = False
    for key in SECRET_KEYS:
        if not data.get(key):
            data[key] = _secrets.token_urlsafe(32)
            changed = True
    if changed:
        _dir().mkdir(parents=True, exist_ok=True)
        vault._write_private(path, vault.encrypt(data))
    return data


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

def scram_sha256_verifier(password: str, salt: bytes | None = None,
                          iterations: int = 4096) -> str:
    """Client-side SCRAM-SHA-256 verifier, so the plaintext password never
    reaches the server (or any server-side statement log)."""
    salt = salt or os.urandom(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


ROLE_MARKER = "managed by aws-admin testsite"


class CheckFailed(RuntimeError):
    """A build or smoke check failed; the message is the full (value-free) report."""


def _rows(cur, query, params=None):
    cur.execute(query, params)
    return cur.fetchall()


def _db_preflight(ts: TestSiteSettings, cur) -> tuple[bool, bool]:
    """Decide, before any mutation, whether the role and database are ours.

    Returns (role_exists, db_exists). Refuses when:
    * the database exists but is not owned by the managed role (it could be
      anything else on the instance: never adopt or drop it);
    * the role exists but is powerful, or carries neither the marker comment
      nor ownership of the test database;
    * an existing role fails the isolation check.
    """
    role_rows = _rows(cur, """
        SELECT shobj_description(r.oid, 'pg_authid'),
               r.rolsuper OR r.rolcreaterole OR r.rolcreatedb
        FROM pg_roles r WHERE r.rolname = %s
    """, (ts.db_role,))
    db_rows = _rows(cur, """
        SELECT pg_get_userbyid(d.datdba) FROM pg_database d WHERE d.datname = %s
    """, (ts.db_name,))
    role_exists, db_exists = bool(role_rows), bool(db_rows)
    owner = db_rows[0][0] if db_exists else None
    if db_exists and owner != ts.db_role:
        raise ValueError(
            f"database {ts.db_name} exists but is not owned by {ts.db_role}; it was not "
            f"created by aws-admin testsite. Refusing to adopt, alter or drop it.")
    if role_exists:
        comment, powerful = role_rows[0]
        if powerful or not (comment == ROLE_MARKER or owner == ts.db_role):
            raise ValueError(
                f"role {ts.db_role} exists but was not created by aws-admin testsite; "
                f"refusing to modify it. Pick another db_role.")
        isolation_check(ts, cur)
    return role_exists, db_exists


def ensure_database(ts: TestSiteSettings, secrets: dict, _connect=None) -> list[str]:
    """Create/refresh the test role and database. Idempotent."""
    from psycopg import sql

    conn = (_connect or connection.connect)(read_only=False)
    conn.autocommit = True  # CREATE DATABASE cannot run inside a transaction
    lines = []
    try:
        cur = conn.cursor()
        role, db = sql.Identifier(ts.db_role), sql.Identifier(ts.db_name)
        verifier = sql.Literal(scram_sha256_verifier(secrets["db_password"]))
        role_exists, db_exists = _db_preflight(ts, cur)  # before any mutation
        if role_exists:
            # No NOSUPERUSER here: RDS's admin cannot alter that attribute, and
            # _db_preflight has already refused any superuser role.
            cur.execute(sql.SQL("ALTER ROLE {} WITH LOGIN NOCREATEDB NOCREATEROLE "
                                "PASSWORD {}").format(role, verifier))
            lines.append(f"role {ts.db_role}: password refreshed")
        else:
            cur.execute(sql.SQL("CREATE ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB "
                                "NOCREATEROLE NOINHERIT PASSWORD {}").format(role, verifier))
            lines.append(f"role {ts.db_role}: created")
        cur.execute(sql.SQL("COMMENT ON ROLE {} IS {}").format(role, sql.Literal(ROLE_MARKER)))
        # The admin user must be able to SET ROLE to make it the database owner.
        cur.execute(sql.SQL("GRANT {} TO CURRENT_USER").format(role))
        if db_exists:
            lines.append(f"database {ts.db_name}: exists")
        else:
            cur.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(db, role))
            lines.append(f"database {ts.db_name}: created")
        cur.execute(sql.SQL("REVOKE CONNECT, TEMPORARY ON DATABASE {} FROM PUBLIC").format(db))
        cur.execute(sql.SQL("GRANT CONNECT, TEMPORARY ON DATABASE {} TO {}").format(db, role))
        lines.append(isolation_check(ts, cur))
    finally:
        conn.close()
    return lines


def isolation_check(ts: TestSiteSettings, cur) -> str:
    """Fail unless the test role can reach nothing in the production database.

    Two counts: relations it holds a privilege on directly, and roles it is a
    member of (transitively; pg_has_role MEMBER also covers roles it could
    only SET ROLE into, which NOINHERIT would hide from the first count).
    """
    rows = _rows(cur, """
        SELECT
          (SELECT count(*) FROM pg_class c
             JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
              AND n.nspname NOT LIKE 'pg_toast%%'
              AND has_table_privilege(%s, c.oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE')),
          (SELECT count(*) FROM pg_roles r
            WHERE r.rolname NOT IN (%s, 'pg_database_owner')
              AND pg_has_role(%s, r.oid, 'MEMBER'))
    """, (ts.db_role, ts.db_role, ts.db_role))
    tables, memberships = rows[0] if rows else (0, 0)
    if tables or memberships:
        raise ValueError(
            f"isolation check FAILED: role {ts.db_role} has privileges on {tables} "
            f"production relation(s) and is a member of {memberships} other role(s). "
            f"Fix before using the test site.")
    return (f"isolation: role {ts.db_role} has no privilege on any production relation "
            f"and no role memberships")


def database_url(ts: TestSiteSettings, secrets: dict) -> str:
    pw = urllib.parse.quote(secrets["db_password"], safe="")
    return (f"postgresql://{ts.db_role}:{pw}@{config.DB_HOST}:{config.DB_PORT}/"
            f"{ts.db_name}?sslmode=require&connection_limit=2")


def drop_database(ts: TestSiteSettings, _connect=None) -> list[str]:
    from psycopg import sql

    conn = (_connect or connection.connect)(read_only=False)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        _db_preflight(ts, cur)  # never drop a database or role this tool did not make
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)")
                    .format(sql.Identifier(ts.db_name)))
        cur.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(ts.db_role)))
    finally:
        conn.close()
    return [f"database {ts.db_name}: dropped", f"role {ts.db_role}: dropped"]


def seed(ts: TestSiteSettings | None = None, _connect=None) -> str:
    """Upsert one admin and one non-admin email/password account in the test DB."""
    import bcrypt
    import psycopg

    ts = ts or settings()
    s = _load_secrets()
    connect = _connect or psycopg.connect
    conn = connect(host=config.DB_HOST, port=config.DB_PORT, dbname=ts.db_name,
                   user=ts.db_role, password=s["db_password"], sslmode=config.DB_SSLMODE)
    try:
        cur = conn.cursor()
        for uid, email, pw_key, is_admin in (
            ("agent-test-admin", ADMIN_EMAIL, "admin_password", True),
            ("agent-test-user", USER_EMAIL, "user_password", False),
        ):
            pw_hash = bcrypt.hashpw(s[pw_key].encode(), bcrypt.gensalt(rounds=10)).decode()
            cur.execute(
                'INSERT INTO users (id, email, name, "passwordHash", "emailVerified", '
                '"isAdmin", "ageAttestedAt", "updatedAt") '
                "VALUES (%s, %s, %s, %s, now(), %s, now(), now()) "
                'ON CONFLICT (email) DO UPDATE SET "passwordHash" = EXCLUDED."passwordHash", '
                '"emailVerified" = now(), "isAdmin" = EXCLUDED."isAdmin", '
                '"passwordChangedAt" = NULL, "updatedAt" = now()',
                (uid, email, "Agent test " + ("admin" if is_admin else "user"),
                 pw_hash, is_admin),
            )
        conn.commit()
    finally:
        conn.close()
    return f"seeded test accounts: {ADMIN_EMAIL} (admin), {USER_EMAIL} (non-admin)"


# --------------------------------------------------------------------------
# Amplify
# --------------------------------------------------------------------------

def _find_app(client, name: str) -> dict | None:
    token = None
    while True:
        kwargs = {"maxResults": 100}
        if token:
            kwargs["nextToken"] = token
        resp = client.list_apps(**kwargs)
        for app in resp.get("apps", []):
            if app.get("name") == name:
                return app
        token = resp.get("nextToken")
        if not token:
            return None


MANAGED_TAG_KEY, MANAGED_TAG_VALUE = "managed-by", "aws-admin-testsite"


def _assert_app_is_ours(app: dict) -> None:
    """Refuse to update or delete an app this tool did not create.

    Ours = tagged managed-by=aws-admin-testsite, or the app id recorded in the
    local state when this tool created it. A configured production app id is
    refused either way, even if it was renamed to the test name in AWS.
    """
    app_id = app["appId"]
    if app_id in {ref.app_id for ref in config.known_apps()}:
        raise ValueError(f"app {app_id} is a configured production app; refusing.")
    tagged = (app.get("tags") or {}).get(MANAGED_TAG_KEY) == MANAGED_TAG_VALUE
    if not tagged and app_id != _load_state().get("app_id"):
        raise ValueError(
            f"app '{app.get('name')}' ({app_id}) was not created by aws-admin testsite "
            f"(no {MANAGED_TAG_KEY} tag); refusing to modify or delete it.")


def _list_branches(client, app_id: str) -> list[dict]:
    out, token = [], None
    while True:
        kwargs = {"appId": app_id, "maxResults": 50}
        if token:
            kwargs["nextToken"] = token
        resp = client.list_branches(**kwargs)
        out.extend(resp.get("branches", []))
        token = resp.get("nextToken")
        if not token:
            return out


def _assert_no_compute_roles(client, app_id: str) -> None:
    """The test site must hold no AWS credentials at runtime: no app-level and
    no branch-level compute role."""
    if client.get_app(appId=app_id)["app"].get("computeRoleArn"):
        raise ValueError("test app has a compute role attached; refusing (it must have none).")
    with_role = [b["branchName"] for b in _list_branches(client, app_id)
                 if b.get("computeRoleArn")]
    if with_role:
        raise ValueError(f"test branch(es) {', '.join(with_role)} have a compute role "
                         f"attached; refusing (the test site must have none).")


def _app_env(ts: TestSiteSettings, s: dict) -> dict:
    return {
        "AUTH_SECRET": s["auth_secret"],
        "AUTH_TRUST_HOST": "true",
        "DATABASE_URL": database_url(ts, s),
        "NEXT_PUBLIC_TURNSTILE_SITE_KEY": TURNSTILE_TEST_SITE_KEY,
        "TURNSTILE_SECRET_KEY": TURNSTILE_TEST_SECRET_KEY,
    }


def _origin(app_id: str, display_name: str) -> str:
    return f"https://{display_name}.{app_id}.amplifyapp.com"


def ensure_app(ts: TestSiteSettings, s: dict, github_token: str | None = None,
               client=None) -> tuple[str, list[str]]:
    client = client or amplify_client()
    source = client.get_app(appId=config.resolve_app(ts.source_app).app_id)["app"]
    fields = dict(
        name=ts.app_name,
        platform="WEB_COMPUTE",
        iamServiceRoleArn=source["iamServiceRoleArn"],
        environmentVariables=_app_env(ts, s),
        enableBranchAutoBuild=False,
        enableAutoBranchCreation=False,
        enableBranchAutoDeletion=False,
        enableBasicAuth=False,  # set per branch instead
    )
    existing = _find_app(client, ts.app_name)
    if existing:
        _assert_app_is_ours(existing)  # before any mutation
        app_id = existing["appId"]
        # update_app is a PUT: send every field we own; computeRoleArn stays unset on purpose.
        client.update_app(appId=app_id, **fields)
        if (existing.get("tags") or {}).get(MANAGED_TAG_KEY) != MANAGED_TAG_VALUE:
            client.tag_resource(resourceArn=existing["appArn"],
                                tags={MANAGED_TAG_KEY: MANAGED_TAG_VALUE})
        action = "updated"
    else:
        if not github_token:
            raise ValueError("Creating the test app needs a GitHub token on stdin "
                             "(--github-token-stdin).")
        app_id = client.create_app(repository=source["repository"], accessToken=github_token,
                                   tags={MANAGED_TAG_KEY: MANAGED_TAG_VALUE},
                                   **fields)["app"]["appId"]
        action = "created"
    _assert_no_compute_roles(client, app_id)
    state = _load_state()
    state["app_id"] = app_id
    _save_state(state)
    keys = ", ".join(sorted(fields["environmentVariables"]))
    return app_id, [f"app {ts.app_name} ({app_id}): {action}; no compute role",
                    f"app env keys: {keys}"]


def up(github_token: str | None = None, client=None, _connect=None) -> str:
    ts = settings()
    s = _load_secrets(create=True)
    lines = ensure_database(ts, s, _connect=_connect)
    _, app_lines = ensure_app(ts, s, github_token=github_token, client=client)
    return "\n".join(lines + app_lines)


def _app_id() -> str:
    app_id = _load_state().get("app_id")
    if not app_id:
        raise FileNotFoundError("No test app recorded. Run `aws-admin testsite up` first.")
    return app_id


def branch(name: str, client=None) -> str:
    """Connect (or refresh) a branch: no auto-build, basic auth, per-branch URLs."""
    client = client or amplify_client()
    s = _load_secrets()
    app_id = _app_id()
    _assert_no_compute_roles(client, app_id)
    creds = base64.b64encode(f"{BASIC_AUTH_USER}:{s['basic_password']}".encode()).decode()
    display = name.replace("/", "-")
    common = dict(framework="Next.js - SSR", stage="DEVELOPMENT", enableAutoBuild=False,
                  enableBasicAuth=True, basicAuthCredentials=creds,
                  enablePullRequestPreview=False)

    def env_for(display_name):
        origin = _origin(app_id, display_name)
        return {"AUTH_URL": origin, "NEXTAUTH_URL": origin, "SITE_URL": origin}

    try:
        current = client.get_branch(appId=app_id, branchName=name)["branch"]
        display = current.get("displayName") or display
        client.update_branch(appId=app_id, branchName=name,
                             environmentVariables=env_for(display), **common)
        action = "updated"
    except client.exceptions.NotFoundException:
        created = client.create_branch(appId=app_id, branchName=name,
                                       environmentVariables=env_for(display), **common)["branch"]
        if created.get("displayName") and created["displayName"] != display:
            display = created["displayName"]
            client.update_branch(appId=app_id, branchName=name,
                                 environmentVariables=env_for(display), **common)
        action = "created"
    state = _load_state()
    state.setdefault("branches", {})[name] = display
    _save_state(state)
    return (f"branch {name}: {action}; basic auth on; auto-build off\n"
            f"url: {_origin(app_id, display)}")


def deploy(name: str, wait: bool = True, client=None, timeout_s: int = 1800,
           poll_s: int = 20, _sleep=time.sleep) -> str:
    client = client or amplify_client()
    app_id = _app_id()
    _assert_no_compute_roles(client, app_id)
    job = client.start_job(appId=app_id, branchName=name, jobType="RELEASE")["jobSummary"]
    job_id = job["jobId"]
    if not wait:
        return f"job {job_id} started on {name}"
    deadline = time.monotonic() + timeout_s
    status = job.get("status", "PENDING")
    while status in ("CREATED", "PENDING", "PROVISIONING", "RUNNING", "CANCELLING"):
        if time.monotonic() > deadline:
            raise CheckFailed(f"job {job_id} on {name}: still {status} after {timeout_s}s")
        _sleep(poll_s)
        status = client.get_job(appId=app_id, branchName=name,
                                jobId=job_id)["job"]["summary"]["status"]
    display = _load_state().get("branches", {}).get(name, name.replace("/", "-"))
    report = f"job {job_id} on {name}: {status}\nurl: {_origin(app_id, display)}"
    if status != "SUCCEED":
        raise CheckFailed(report)
    return report


def status(client=None, _connect=None) -> str:
    ts = settings()
    state = _load_state()
    if not state.get("app_id"):
        return "test site: not provisioned (run `aws-admin testsite up`)"
    client = client or amplify_client()
    app_id = state["app_id"]
    app = client.get_app(appId=app_id)["app"]
    lines = [f"app {app.get('name')} ({app_id}); compute role: "
             f"{'SET (unexpected)' if app.get('computeRoleArn') else 'none'}",
             "app env keys: " + ", ".join(sorted(app.get("environmentVariables", {})))]
    branch_roles = {b["branchName"]: b.get("computeRoleArn")
                    for b in _list_branches(client, app_id)}
    for name, display in sorted(state.get("branches", {}).items()):
        if branch_roles.get(name):
            lines.append(f"branch {name}: COMPUTE ROLE SET (unexpected)")
        jobs = client.list_jobs(appId=app_id, branchName=name, maxResults=1).get("jobSummaries", [])
        last = jobs[0]["status"] if jobs else "never built"
        lines.append(f"branch {name}: last job {last}; {_origin(app_id, display)}")
    conn = (_connect or connection.connect)(read_only=True)
    try:
        cur = conn.cursor()
        exists = bool(_rows(cur, "SELECT 1 FROM pg_database WHERE datname = %s", (ts.db_name,)))
        lines.append(f"database {ts.db_name}: {'exists' if exists else 'MISSING'}")
        lines.append(isolation_check(ts, cur))
    finally:
        conn.close()
    return "\n".join(lines)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _http(opener, url, auth=None, data=None):
    req = urllib.request.Request(url, data=data)
    if auth:
        req.add_header("Authorization", f"Basic {auth}")
    try:
        with opener.open(req, timeout=60) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, b""


def _foreign_cookie_domain(headers, host: str | None) -> str | None:
    """The Domain= of a session-token Set-Cookie that `host` cannot accept, if any."""
    if not headers or not host:
        return None
    get_all = getattr(headers, "get_all", None)
    cookies = get_all("Set-Cookie") or [] if get_all else []
    for c in cookies:
        if "session-token" not in c:
            continue
        for part in c.split(";"):
            k, _, v = part.strip().partition("=")
            if k.lower() == "domain":
                d = v.strip().lstrip(".").lower()
                if d and not (host == d or host.endswith("." + d)):
                    return "." + d
    return None


def smoke(name: str, _opener_factory=None) -> str:
    """Read-only checks on a deployed branch. Prints statuses only, never credentials."""
    s = _load_secrets()
    app_id = _app_id()
    display = _load_state().get("branches", {}).get(name, name.replace("/", "-"))
    base = _origin(app_id, display)
    auth = base64.b64encode(f"{BASIC_AUTH_USER}:{s['basic_password']}".encode()).decode()
    jar = http.cookiejar.CookieJar()
    factory = _opener_factory or (lambda: urllib.request.build_opener(
        _NoRedirect, urllib.request.HTTPCookieProcessor(jar)))
    opener = factory()
    results = []

    def check(label, ok, detail):
        results.append((ok, f"{'PASS' if ok else 'FAIL'}  {label}: {detail}"))

    def skip(label, reason):
        results.append((True, f"SKIP  {label}: {reason}"))

    code, _, _ = _http(opener, base + "/")
    check("anonymous GET / blocked by basic auth", code == 401, code)
    for path in ("/", "/products", "/auth/signin"):
        code, _, _ = _http(opener, base + path, auth)
        check(f"GET {path}", code == 200, code)
    code, headers, _ = _http(opener, base + "/admin", auth)
    loc = (headers or {}).get("Location", "") if headers else ""
    check("signed-out GET /admin redirects to sign-in",
          300 <= code < 400 and ("signin" in loc or "/dashboard" in loc),
          f"{code} -> {urllib.parse.urlparse(loc).path}")

    # Signed in as the non-admin test account: /admin must still be refused.
    code, _, body = _http(opener, base + "/api/auth/csrf", auth)
    csrf = json.loads(body or b"{}").get("csrfToken") if code == 200 else None
    if csrf:
        form = urllib.parse.urlencode({"csrfToken": csrf, "email": USER_EMAIL,
                                       "password": s["user_password"],
                                       "callbackUrl": base + "/"}).encode()
        code, headers, _ = _http(opener, base + "/api/auth/callback/credentials", auth, form)
        signed_in = any("session-token" in c.name for c in jar)
        foreign = _foreign_cookie_domain(headers, urllib.parse.urlparse(base).hostname)
        if not signed_in and foreign:
            skip("signed-in checks",
                 f"the app pins its session cookie to Domain={foreign}, which this host "
                 f"cannot hold (configurable cookie domain needed; see agent-test-site.md)")
        else:
            check("non-admin sign-in", signed_in,
                  f"{code}, session cookie {'set' if signed_in else 'missing'}")
        if signed_in:
            code, headers, _ = _http(opener, base + "/admin", auth)
            loc = headers.get("Location", "") if headers else ""
            check("signed-in non-admin GET /admin refused",
                  code in (401, 403, 404) or (300 <= code < 400 and "/admin" not in
                                              urllib.parse.urlparse(loc).path),
                  f"{code} -> {urllib.parse.urlparse(loc).path}")
    else:
        check("csrf token", False, code)

    passed = sum(1 for ok, _ in results if ok)
    report = "\n".join([line for _, line in results] + [f"{passed}/{len(results)} passed"])
    if passed != len(results):
        raise CheckFailed(report)
    return report


def exec_with_db(argv: list[str], _run=None) -> int:
    """Run a command with the test DATABASE_URL in its environment only.

    For schema work such as `npx prisma db push`. The URL is never printed or
    placed on the command line; the child's own output is passed through, so
    only run commands that do not echo their environment.
    """
    import subprocess

    if not argv:
        raise ValueError("usage: aws-admin testsite exec -- <command> [args...]")
    ts = settings()
    s = _load_secrets()
    env = dict(os.environ, DATABASE_URL=database_url(ts, s))
    run = _run or subprocess.run
    return run(argv, env=env).returncode


def creds_to_file(path: str) -> str:
    """Write the basic-auth and test-account credentials to a 0600 file for a human.

    Prints only the path, never the values.
    """
    s = _load_secrets()
    app_id = _app_id()
    branches = _load_state().get("branches", {})
    lines = ["# ChameleonLabs test site credentials (test-only; never production)",
             f"basic_auth_user={BASIC_AUTH_USER}",
             f"basic_auth_password={s['basic_password']}",
             f"admin_email={ADMIN_EMAIL}", f"admin_password={s['admin_password']}",
             f"user_email={USER_EMAIL}", f"user_password={s['user_password']}"]
    lines += [f"url_{n}={_origin(app_id, d)}" for n, d in sorted(branches.items())]
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    vault._write_private(target, ("\n".join(lines) + "\n").encode())
    return f"wrote test-site credentials to {target} (mode 0600)"


def down(yes: bool = False, client=None, _connect=None) -> str:
    if not yes:
        raise ValueError("`testsite down` deletes the test app and test database; pass --yes.")
    ts = settings()
    client = client or amplify_client()
    lines = []
    app = _find_app(client, ts.app_name)
    if app:
        _assert_app_is_ours(app)
        client.delete_app(appId=app["appId"])
        lines.append(f"app {ts.app_name} ({app['appId']}): deleted")
    else:
        lines.append(f"app {ts.app_name}: not found")
    lines += drop_database(ts, _connect=_connect)
    for p in (_state_path(), _secrets_path()):
        if p.exists():
            p.unlink()
    lines.append("local test-site state and secrets: removed")
    return "\n".join(lines)
