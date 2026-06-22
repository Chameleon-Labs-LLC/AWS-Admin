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


# --- Cost report fakes -----------------------------------------------------
import datetime as _dt


class FakeCE:
    """Stand-in for the boto3 Cost Explorer client.

    Non-grouped queries return one canned $100 month per month in [Start, End)
    (so trend lists N rows and an intra-month MTD query returns one). Grouped
    queries return a fixed by-service breakdown, including a $0 row that the
    command is expected to drop.
    """

    def __init__(self):
        self.calls = []

    def get_cost_and_usage(self, TimePeriod, Granularity, Metrics, GroupBy=None):
        self.calls.append(("get_cost_and_usage", TimePeriod, bool(GroupBy)))
        if GroupBy:
            return {"ResultsByTime": [{"Groups": [
                {"Keys": ["Amazon EC2"], "Metrics": {"UnblendedCost": {"Amount": "80.00"}}},
                {"Keys": ["AWS WAF"], "Metrics": {"UnblendedCost": {"Amount": "39.00"}}},
                {"Keys": ["Zero Svc"], "Metrics": {"UnblendedCost": {"Amount": "0"}}},
            ]}]}
        start = _dt.date.fromisoformat(TimePeriod["Start"])
        end = _dt.date.fromisoformat(TimePeriod["End"])
        si = start.year * 12 + (start.month - 1)
        ei = end.year * 12 + (end.month - 1)
        months = [_dt.date(i // 12, i % 12 + 1, 1).isoformat() for i in range(si, ei)]
        if not months:  # intra-month window (MTD)
            months = [start.replace(day=1).isoformat()]
        return {"ResultsByTime": [
            {"TimePeriod": {"Start": s}, "Total": {"UnblendedCost": {"Amount": "100.00"}}}
            for s in months
        ]}


class FakeFreeTier:
    def get_free_tier_usage(self, **kwargs):
        return {"freeTierUsages": [
            {"service": "AWS Amplify", "usageType": "BuildDuration",
             "freeTierType": "12 Months Free", "actualUsageAmount": 831.0,
             "forecastedUsageAmount": 1175.0, "limit": 1000.0, "unit": "minutes"},
            {"service": "Amazon Virtual Private Cloud", "usageType": "PublicIPv4:InUseAddress",
             "freeTierType": "12 Months Free", "actualUsageAmount": 750.0,
             "forecastedUsageAmount": 1023.0, "limit": 750.0, "unit": "Hrs"},
            {"service": "AWS Lambda", "usageType": "Request",
             "freeTierType": "Always Free", "actualUsageAmount": 1436.0,
             "forecastedUsageAmount": 2051.0, "limit": 1000000.0, "unit": "Request"},
            {"service": "Amazon DevOps Guru", "usageType": "ResourceGroup-B-usagehours",
             "freeTierType": "Free Trial", "actualUsageAmount": 509.0,
             "forecastedUsageAmount": 707.0, "limit": 7200.0, "unit": "usagehours"},
        ]}


class FakeSts:
    def get_caller_identity(self):
        return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/x",
                "UserId": "AIDAEXAMPLE"}


@pytest.fixture
def cost_clients():
    """(ce, freetier, sts) fakes for the cost report command."""
    return FakeCE(), FakeFreeTier(), FakeSts()
