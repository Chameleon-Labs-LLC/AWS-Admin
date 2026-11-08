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
                                       "repository": "https://github.com/example/repo",
                                       "iamServiceRoleArn": "arn:aws:iam::0:role/logging",
                                       "environmentVariables": {"PROD_SECRET": "prod-value"}}
        if existing:
            self.apps["dtest"] = {"appId": "dtest", "name": "ExampleOrg-Test"}

    def list_apps(self, **kw):
        return {"apps": list(self.apps.values())}

    def get_app(self, appId):
        app = dict(self.apps[appId])
        if appId != "d0000000000eo0" and self._compute_role:
            app["computeRoleArn"] = self._compute_role
        return {"app": app}

    def create_app(self, **kw):
        self.calls.append(("create_app", kw))
        self.apps["dtest"] = {"appId": "dtest", "name": kw["name"],
                              "environmentVariables": kw["environmentVariables"]}
        return {"app": {"appId": "dtest"}}

    def update_app(self, **kw):
        self.calls.append(("update_app", kw))
        self.apps[kw["appId"]].update(environmentVariables=kw["environmentVariables"])
        return {"app": {"appId": kw["appId"]}}

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


def _connect_factory(results):
    holder = {}

    def connect(read_only=True):
        holder["conn"] = ScriptedConn(results)
        holder["conn"].read_only = read_only
        return holder["conn"]
    return connect, holder


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
    # role missing, database missing, isolation count 0
    connect, holder = _connect_factory([[], [], [(0,)]])
    amp = FakeAmp()
    out = ts.up(github_token="gh-token-value", client=amp, _connect=connect)

    executed = repr(holder["conn"].cur.executed)
    assert "CREATE ROLE" in executed and "CREATE DATABASE" in executed
    assert "REVOKE CONNECT" in executed and "SCRAM-SHA-256$" in executed
    assert holder["conn"].autocommit is True and holder["conn"].closed

    name, kw = amp.calls[0]
    assert name == "create_app"
    assert set(kw["environmentVariables"]) == set(ts.APP_ENV_KEYS)
    assert "PROD_SECRET" not in kw["environmentVariables"]
    assert "computeRoleArn" not in kw
    assert kw["platform"] == "WEB_COMPUTE" and kw["enableBranchAutoBuild"] is False
    assert "example_db_test" in kw["environmentVariables"]["DATABASE_URL"]

    for value in _all_secret_values() + ["gh-token-value", "prod-value"]:
        assert value not in out
        assert value not in executed  # the role password goes over as a verifier only


def test_up_existing_app_updates_with_full_field_set(with_testsite):
    connect, _ = _connect_factory([[(1,)], [(1,)], [(0,)]])
    amp = FakeAmp(existing=True)
    out = ts.up(client=amp, _connect=connect)
    name, kw = amp.calls[0]
    assert name == "update_app" and kw["appId"] == "dtest"
    assert set(kw["environmentVariables"]) == set(ts.APP_ENV_KEYS)
    assert "updated" in out


def test_up_creating_app_without_token_fails(with_testsite):
    connect, _ = _connect_factory([[], [], [(0,)]])
    with pytest.raises(ValueError, match="GitHub token"):
        ts.up(client=FakeAmp(), _connect=connect)


def test_isolation_failure_raises(with_testsite):
    connect, _ = _connect_factory([[], [], [(3,)]])
    with pytest.raises(ValueError, match="isolation check FAILED"):
        ts.up(github_token="t", client=FakeAmp(), _connect=connect)


def test_compute_role_on_test_app_is_refused(with_testsite):
    connect, _ = _connect_factory([[], [], [(0,)]])
    with pytest.raises(ValueError, match="compute role"):
        ts.up(github_token="t", client=FakeAmp(compute_role="arn:role"), _connect=connect)


def test_branch_sets_basic_auth_and_branch_urls_without_leaking(with_testsite):
    connect, _ = _connect_factory([[], [], [(0,)]])
    amp = FakeAmp()
    ts.up(github_token="t", client=amp, _connect=connect)
    out = ts.branch("chore/next-16", client=amp)
    name, kw = [c for c in amp.calls if c[0] == "create_branch"][0]
    assert kw["enableBasicAuth"] is True and kw["enableAutoBuild"] is False
    assert kw["environmentVariables"]["AUTH_URL"] == "https://chore-next-16.dtest.amplifyapp.com"
    assert "https://chore-next-16.dtest.amplifyapp.com" in out
    for value in _all_secret_values() + [kw["basicAuthCredentials"]]:
        assert value not in out


def test_deploy_waits_for_terminal_status(with_testsite):
    connect, _ = _connect_factory([[], [], [(0,)]])
    amp = FakeAmp()
    ts.up(github_token="t", client=amp, _connect=connect)
    out = ts.deploy("main", client=amp, _sleep=lambda s: None)
    assert "SUCCEED" in out


def test_creds_file_is_private_and_output_has_path_only(with_testsite, tmp_path):
    connect, _ = _connect_factory([[], [], [(0,)]])
    ts.up(github_token="t", client=FakeAmp(), _connect=connect)
    target = tmp_path / "out" / "creds.txt"
    out = ts.creds_to_file(str(target))
    assert str(target) in out
    for value in _all_secret_values():
        assert value not in out
    assert (target.stat().st_mode & 0o777) == 0o600


def test_down_requires_yes(with_testsite):
    with pytest.raises(ValueError, match="--yes"):
        ts.down()


def test_down_deletes_app_db_and_state(with_testsite):
    connect, holder = _connect_factory([[], [], [(0,)]])
    amp = FakeAmp()
    ts.up(github_token="t", client=amp, _connect=connect)
    connect2, holder2 = _connect_factory([])
    out = ts.down(yes=True, client=amp, _connect=connect2)
    assert ("delete_app", {"appId": "dtest"}) in amp.calls
    assert "DROP DATABASE" in repr(holder2["conn"].cur.executed)
    assert not ts._secrets_path().exists() and not ts._state_path().exists()
    assert "deleted" in out
