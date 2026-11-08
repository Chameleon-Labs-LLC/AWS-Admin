"""Tests for `aws-admin testsite`. No real AWS, DB, or network."""
import base64
import hashlib
import hmac

import pytest

from aws_admin import config
from aws_admin.commands import testsite as ts

TESTSITE_TOML = """
[testsite]
app_name = "ExampleOrg-Test"
source_app = "ExampleOrg"
db_name = "example_db_test"
db_role = "example_test_role"
"""

MANAGED = {"managed-by": "aws-admin-testsite"}


@pytest.fixture
def with_testsite(isolated_home):
    with (isolated_home / "config.toml").open("a") as fh:
        fh.write(TESTSITE_TOML)
    return isolated_home


class NotFound(Exception):
    pass


class FakeAmp:
    class exceptions:
        NotFoundException = NotFound

    def __init__(self, existing=False, compute_role=None):
        self.calls = []
        self.apps = {}
        self.branches = {}
        self._compute_role = compute_role
        self.apps["d0000000000eo0"] = {"appId": "d0000000000eo0", "name": "ExampleOrg",
                                       "appArn": "arn:aws:amplify:::apps/d0000000000eo0",
                                       "repository": "https://github.com/example/repo",
                                       "iamServiceRoleArn": "arn:aws:iam::0:role/logging",
                                       "environmentVariables": {"PROD_SECRET": "prod-value"}}
        if existing:
            self.apps["dtest"] = {"appId": "dtest", "name": "ExampleOrg-Test",
                                  "appArn": "arn:aws:amplify:::apps/dtest", "tags": dict(MANAGED)}

    def list_apps(self, **kw):
        return {"apps": list(self.apps.values())}

    def get_app(self, appId):
        app = dict(self.apps[appId])
        if appId != "d0000000000eo0" and self._compute_role:
            app["computeRoleArn"] = self._compute_role
        return {"app": app}

    def create_app(self, **kw):
        self.calls.append(("create_app", kw))
        self.apps["dtest"] = {"appId": "dtest", "name": kw["name"], "tags": kw.get("tags"),
                              "appArn": "arn:aws:amplify:::apps/dtest",
                              "environmentVariables": kw["environmentVariables"]}
        return {"app": {"appId": "dtest"}}

    def update_app(self, **kw):
        self.calls.append(("update_app", kw))
        self.apps[kw["appId"]].update(environmentVariables=kw["environmentVariables"])
        return {"app": {"appId": kw["appId"]}}

    def tag_resource(self, resourceArn, tags):
        self.calls.append(("tag_resource", {"resourceArn": resourceArn, "tags": tags}))

    def list_branches(self, appId, **kw):
        return {"branches": list(self.branches.values())}

    def get_branch(self, appId, branchName):
        if branchName not in self.branches:
            raise NotFound()
        return {"branch": self.branches[branchName]}

    def create_branch(self, **kw):
        self.calls.append(("create_branch", kw))
        b = {"branchName": kw["branchName"], "displayName": kw["branchName"].replace("/", "-")}
        self.branches[kw["branchName"]] = b
        return {"branch": b}

    def update_branch(self, **kw):
        self.calls.append(("update_branch", kw))
        return {"branch": {}}

    def start_job(self, **kw):
        self.calls.append(("start_job", kw))
        return {"jobSummary": {"jobId": "7", "status": "PENDING"}}

    def get_job(self, **kw):
        return {"job": {"summary": {"status": "SUCCEED"}}}

    def list_jobs(self, **kw):
        return {"jobSummaries": [{"status": "SUCCEED"}]}

    def delete_app(self, appId):
        self.calls.append(("delete_app", {"appId": appId}))


class ScriptedCursor:
    def __init__(self, results):
        self.results = list(results)
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchall(self):
        return self.results.pop(0) if self.results else []


class ScriptedConn:
    def __init__(self, results):
        self.autocommit = False
        self.read_only = None
        self.cur = ScriptedCursor(results)
        self.closed = False

    def cursor(self):
        return self.cur

    def commit(self):
        pass

    def close(self):
        self.closed = True


# fetchall() sequences, in query order:
# preflight role row, preflight db owner, [isolation if role exists], final isolation
FRESH = [[], [], [(0, 0)]]
OURS = [[(ts.ROLE_MARKER, False)], [("example_test_role",)], [(0, 0)], [(0, 0)]]


def _connect_factory(results):
    holder = {}

    def connect(read_only=True):
        holder["conn"] = ScriptedConn(results)
        holder["conn"].read_only = read_only
        return holder["conn"]
    return connect, holder


def _fresh():
    return _connect_factory(FRESH)[0]


def _all_secret_values():
    s = ts._load_secrets()
    return [s[k] for k in ts.SECRET_KEYS]


def test_settings_requires_testsite_table(isolated_home):
    with pytest.raises(config.ConfigError):
        ts.settings()


@pytest.mark.parametrize("override,msg", [
    ('db_name = "example_db"', "production database"),
    ('db_role = "postgres"', "admin DB user"),
    ('app_name = "ExampleOrg"', "configured production app"),
    ('db_name = "Bad-Name"', "lowercase"),
])
def test_guard_refuses_overlap_with_production(isolated_home, override, msg):
    key = override.split(" = ")[0]
    toml = "\n".join(line if not line.startswith(key + " ") else override
                     for line in TESTSITE_TOML.splitlines())
    with (isolated_home / "config.toml").open("a") as fh:
        fh.write(toml)
    with pytest.raises(ValueError, match=msg):
        ts.settings()


def test_scram_verifier_matches_rfc5802_construction():
    salt = b"0123456789abcdef"
    v = ts.scram_sha256_verifier("pw", salt=salt, iterations=4096)
    salted = hashlib.pbkdf2_hmac("sha256", b"pw", salt, 4096)
    stored = hashlib.sha256(hmac.new(salted, b"Client Key", hashlib.sha256).digest()).digest()
    server = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    assert v == f"SCRAM-SHA-256$4096:{b64(salt)}${b64(stored)}:{b64(server)}"


def test_up_creates_role_db_and_app_without_leaking(with_testsite):
    connect, holder = _connect_factory(FRESH)
    amp = FakeAmp()
    out = ts.up(github_token="gh-token-value", client=amp, _connect=connect)

    executed = repr(holder["conn"].cur.executed)
    assert "CREATE ROLE" in executed and "CREATE DATABASE" in executed
    assert "REVOKE CONNECT" in executed and "SCRAM-SHA-256$" in executed
    assert "pg_has_role" in executed  # isolation counts role memberships too
    assert holder["conn"].autocommit is True and holder["conn"].closed

    name, kw = amp.calls[0]
    assert name == "create_app"
    assert kw["tags"] == MANAGED
    assert set(kw["environmentVariables"]) == set(ts.APP_ENV_KEYS)
    assert "PROD_SECRET" not in kw["environmentVariables"]
    assert "computeRoleArn" not in kw
    assert kw["platform"] == "WEB_COMPUTE" and kw["enableBranchAutoBuild"] is False
    assert "example_db_test" in kw["environmentVariables"]["DATABASE_URL"]

    for value in _all_secret_values() + ["gh-token-value", "prod-value"]:
        assert value not in out
        assert value not in executed  # the role password goes over as a verifier only


def test_up_existing_app_updates_with_full_field_set(with_testsite):
    amp = FakeAmp(existing=True)
    out = ts.up(client=amp, _connect=_connect_factory(OURS)[0])
    name, kw = amp.calls[0]
    assert name == "update_app" and kw["appId"] == "dtest"
    assert set(kw["environmentVariables"]) == set(ts.APP_ENV_KEYS)
    assert "updated" in out and "password refreshed" in out


def test_up_creating_app_without_token_fails(with_testsite):
    with pytest.raises(ValueError, match="GitHub token"):
        ts.up(client=FakeAmp(), _connect=_fresh())


# --- database ownership and isolation (checked before any mutation) ---

@pytest.mark.parametrize("role_row,db_rows", [
    ((None, False), []),                                    # someone else's role, no db
    (("app role", False), []),                              # commented, but not ours
    ((ts.ROLE_MARKER, True), [("example_test_role",)]),     # ours by marker, but powerful
])
def test_existing_foreign_role_is_refused_before_any_change(with_testsite, role_row, db_rows):
    connect, holder = _connect_factory([[role_row], db_rows, [(0, 0)]])
    with pytest.raises(ValueError, match="not created by aws-admin testsite"):
        ts.up(github_token="t", client=FakeAmp(), _connect=connect)
    executed = repr(holder["conn"].cur.executed)
    assert "ALTER ROLE" not in executed and "GRANT" not in executed


def test_existing_database_not_owned_by_role_is_refused(with_testsite):
    connect, holder = _connect_factory([[], [("postgres",)]])
    with pytest.raises(ValueError, match="not owned by"):
        ts.up(github_token="t", client=FakeAmp(), _connect=connect)
    assert "CREATE ROLE" not in repr(holder["conn"].cur.executed)

    connect2, holder2 = _connect_factory([[], [("postgres",)]])
    with pytest.raises(ValueError, match="not owned by"):
        ts.drop_database(ts.settings(), _connect=connect2)
    assert "DROP" not in repr(holder2["conn"].cur.executed)


@pytest.mark.parametrize("iso", [(5, 0), (0, 1)])   # table privileges, or a role membership
def test_existing_role_failing_isolation_is_refused_before_alter(with_testsite, iso):
    connect, holder = _connect_factory([[(ts.ROLE_MARKER, False)], [], [iso]])
    with pytest.raises(ValueError, match="isolation check FAILED"):
        ts.up(github_token="t", client=FakeAmp(), _connect=connect)
    assert "ALTER ROLE" not in repr(holder["conn"].cur.executed)


@pytest.mark.parametrize("iso", [(3, 0), (0, 2)])
def test_isolation_failure_after_create_raises(with_testsite, iso):
    with pytest.raises(ValueError, match="isolation check FAILED"):
        ts.up(github_token="t", client=FakeAmp(), _connect=_connect_factory([[], [], [iso]])[0])


# --- app identity and compute roles ---

def test_untagged_app_with_test_name_is_not_adopted_or_deleted(with_testsite):
    amp = FakeAmp()
    amp.apps["dother"] = {"appId": "dother", "name": "ExampleOrg-Test",
                          "appArn": "arn:x", "tags": {}}
    with pytest.raises(ValueError, match="not created by aws-admin testsite"):
        ts.up(client=amp, _connect=_fresh())
    assert not [c for c in amp.calls if c[0] == "update_app"]
    with pytest.raises(ValueError, match="not created by aws-admin testsite"):
        ts.down(yes=True, client=amp, _connect=_connect_factory([])[0])
    assert not [c for c in amp.calls if c[0] == "delete_app"]


def test_production_app_id_is_refused_even_if_renamed(with_testsite):
    amp = FakeAmp()
    amp.apps["d0000000000eo0"]["name"] = "ExampleOrg-Test"
    amp.apps["d0000000000eo0"]["tags"] = dict(MANAGED)
    with pytest.raises(ValueError, match="configured production app"):
        ts.up(client=amp, _connect=_fresh())
    assert not [c for c in amp.calls if c[0] == "update_app"]


def test_legacy_untagged_app_recorded_in_state_is_adopted_and_tagged(with_testsite):
    amp = FakeAmp()
    ts.up(github_token="t", client=amp, _connect=_fresh())   # records app_id in state
    amp.apps["dtest"]["tags"] = {}                          # created before tagging existed
    ts.up(client=amp, _connect=_connect_factory(OURS)[0])
    assert ("tag_resource", {"resourceArn": "arn:aws:amplify:::apps/dtest",
                             "tags": MANAGED}) in amp.calls


def test_compute_role_on_test_app_is_refused(with_testsite):
    with pytest.raises(ValueError, match="compute role"):
        ts.up(github_token="t", client=FakeAmp(compute_role="arn:role"), _connect=_fresh())


def test_branch_compute_role_is_refused(with_testsite):
    amp = FakeAmp()
    ts.up(github_token="t", client=amp, _connect=_fresh())
    amp.branches["main"] = {"branchName": "main", "displayName": "main",
                            "computeRoleArn": "arn:role"}
    with pytest.raises(ValueError, match="compute role"):
        ts.deploy("main", client=amp, _sleep=lambda s: None)
    with pytest.raises(ValueError, match="compute role"):
        ts.branch("main", client=amp)
    with pytest.raises(ValueError, match="compute role"):
        ts.up(client=amp, _connect=_connect_factory(OURS)[0])


# --- branches, builds, smoke, creds, exec ---

def test_branch_sets_basic_auth_and_branch_urls_without_leaking(with_testsite):
    amp = FakeAmp()
    ts.up(github_token="t", client=amp, _connect=_fresh())
    out = ts.branch("chore/next-16", client=amp)
    name, kw = [c for c in amp.calls if c[0] == "create_branch"][0]
    assert kw["enableBasicAuth"] is True and kw["enableAutoBuild"] is False
    assert kw["environmentVariables"]["AUTH_URL"] == "https://chore-next-16.dtest.amplifyapp.com"
    assert "https://chore-next-16.dtest.amplifyapp.com" in out
    for value in _all_secret_values() + [kw["basicAuthCredentials"]]:
        assert value not in out


def test_deploy_waits_for_terminal_status(with_testsite):
    amp = FakeAmp()
    ts.up(github_token="t", client=amp, _connect=_fresh())
    out = ts.deploy("main", client=amp, _sleep=lambda s: None)
    assert "SUCCEED" in out


def test_failed_deploy_raises_and_cli_exits_nonzero(with_testsite, monkeypatch, capsys):
    amp = FakeAmp()
    ts.up(github_token="t", client=amp, _connect=_fresh())
    amp.get_job = lambda **kw: {"job": {"summary": {"status": "FAILED"}}}
    with pytest.raises(ts.CheckFailed, match="FAILED"):
        ts.deploy("main", client=amp, _sleep=lambda s: None)

    from aws_admin import cli

    def boom(name, wait=True):
        raise ts.CheckFailed("job 1 on main: FAILED")
    monkeypatch.setattr(ts, "deploy", boom)
    assert cli.main(["testsite", "deploy", "main"]) == 4
    assert "FAILED" in capsys.readouterr().out


def test_foreign_cookie_domain_detection():
    from email.message import Message
    h = Message()
    h["Set-Cookie"] = "__Secure-next-auth.session-token=x; Domain=.chameleonlabs.ai; Path=/; Secure"
    assert ts._foreign_cookie_domain(h, "main.d1.amplifyapp.com") == ".chameleonlabs.ai"
    assert ts._foreign_cookie_domain(h, "www.chameleonlabs.ai") is None
    h2 = Message()
    h2["Set-Cookie"] = "other=1; Domain=.example.com"
    assert ts._foreign_cookie_domain(h2, "main.d1.amplifyapp.com") is None
    assert ts._foreign_cookie_domain(None, "x") is None


def test_creds_file_is_private_and_output_has_path_only(with_testsite, tmp_path):
    ts.up(github_token="t", client=FakeAmp(), _connect=_fresh())
    target = tmp_path / "out" / "creds.txt"
    out = ts.creds_to_file(str(target))
    assert str(target) in out
    for value in _all_secret_values():
        assert value not in out
    assert (target.stat().st_mode & 0o777) == 0o600


def test_exec_passes_db_url_in_env_not_argv(with_testsite):
    ts.up(github_token="t", client=FakeAmp(), _connect=_fresh())
    seen = {}

    class R:
        returncode = 0

    def fake_run(argv, env):
        seen["argv"], seen["env"] = argv, env
        return R()

    assert ts.exec_with_db(["npx", "prisma", "db", "push"], _run=fake_run) == 0
    url = seen["env"]["DATABASE_URL"]
    assert "example_db_test" in url and "connection_limit=2" in url
    assert all(url not in a for a in seen["argv"])
    with pytest.raises(ValueError):
        ts.exec_with_db([], _run=fake_run)


def test_cli_exec_strips_double_dash(with_testsite, monkeypatch):
    from aws_admin import cli
    got = {}
    monkeypatch.setattr(ts, "exec_with_db", lambda argv: got.setdefault("argv", argv) and 0)
    cli.main(["testsite", "exec", "--", "echo", "hi"])
    assert got["argv"] == ["echo", "hi"]


# --- teardown ---

def test_down_requires_yes(with_testsite):
    with pytest.raises(ValueError, match="--yes"):
        ts.down()


def test_down_deletes_app_db_and_state(with_testsite):
    amp = FakeAmp()
    ts.up(github_token="t", client=amp, _connect=_fresh())
    connect2, holder2 = _connect_factory([[(ts.ROLE_MARKER, False)],
                                          [("example_test_role",)], [(0, 0)]])
    out = ts.down(yes=True, client=amp, _connect=connect2)
    assert ("delete_app", {"appId": "dtest"}) in amp.calls
    assert "DROP DATABASE" in repr(holder2["conn"].cur.executed)
    assert not ts._secrets_path().exists() and not ts._state_path().exists()
    assert "deleted" in out
