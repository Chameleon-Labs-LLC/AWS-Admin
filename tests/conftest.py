import pytest


class FakeAmplify:
    """Hand-written stand-in for the boto3 Amplify client used in command tests.

    Records calls so tests can assert REPLACE-not-merge behavior, and returns
    canned app/branch env vars.
    """

    def __init__(self, app_env=None, branch_env=None, branch_exists=True):
        self._app_env = dict(app_env or {})
        self._branch_env = dict(branch_env or {})
        self._branch_exists = branch_exists
        self.calls = []

    def get_app(self, appId):
        self.calls.append(("get_app", {"appId": appId}))
        return {"app": {"appId": appId, "name": "Fake",
                        "environmentVariables": dict(self._app_env)}}

    def get_branch(self, appId, branchName):
        self.calls.append(("get_branch", {"appId": appId, "branchName": branchName}))
        if not self._branch_exists:
            from botocore.exceptions import ClientError
            raise ClientError(
                {"Error": {"Code": "NotFoundException", "Message": "no branch"}},
                "GetBranch",
            )
        return {"branch": {"branchName": branchName,
                           "environmentVariables": dict(self._branch_env)}}

    def update_app(self, appId, environmentVariables):
        self.calls.append(("update_app",
                           {"appId": appId, "environmentVariables": environmentVariables}))
        self._app_env = dict(environmentVariables)
        return {"app": {"appId": appId}}

    def update_branch(self, appId, branchName, environmentVariables):
        self.calls.append(("update_branch",
                           {"appId": appId, "branchName": branchName,
                            "environmentVariables": environmentVariables}))
        self._branch_env = dict(environmentVariables)
        return {"branch": {"branchName": branchName}}

    def start_job(self, appId, branchName, jobType):
        self.calls.append(("start_job",
                           {"appId": appId, "branchName": branchName, "jobType": jobType}))
        return {"jobSummary": {"jobId": "1", "status": "PENDING"}}


@pytest.fixture
def fake_amplify():
    return FakeAmplify


SYNTHETIC_CONFIG_TOML = """\
account_id = "000000000000"

[database]
host = "db.example.invalid"
name = "example_db"

[apps.ExampleOrg]
app_id = "d0000000000eo0"
aliases = ["eo"]

[apps.AppBeta]
app_id = "d0000000000ab0"
aliases = ["ab"]

[apps.AppAlpha]
app_id = "d0000000000aa0"
aliases = ["aa"]

[apps.AppGamma]
app_id = "d0000000000ag0"
aliases = ["ag"]

[apps.MyApp2]
app_id = "d0000000000my0"
aliases = ["my"]
"""


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    """Every test gets its own AWS_ADMIN_HOME (vault isolation) seeded with a
    synthetic config.toml so config-dependent code has known, non-sensitive values."""
    monkeypatch.setenv("AWS_ADMIN_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(SYNTHETIC_CONFIG_TOML)
    return tmp_path


class _Col:
    """Stand-in for a psycopg Column (exposes .name)."""
    def __init__(self, name):
        self.name = name


class FakeCursor:
    def __init__(self, description=None, rows=None, rowcount=0):
        self._description = [_Col(n) for n in description] if description is not None else None
        self._rows = list(rows or [])
        self.rowcount = rowcount
        self.executed = []

    @property
    def description(self):
        return self._description

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return list(self._rows)


class FakeDBConn:
    """Fake psycopg connection that records commit/rollback/close and read_only."""
    def __init__(self, description=None, rows=None, rowcount=0):
        self.read_only = None
        self.autocommit = False
        self.calls = []
        self._cursor = FakeCursor(description, rows, rowcount)

    def cursor(self):
        return self._cursor

    def commit(self):
        self.calls.append("commit")

    def rollback(self):
        self.calls.append("rollback")

    def close(self):
        self.calls.append("close")


@pytest.fixture
def fake_db():
    return FakeDBConn
