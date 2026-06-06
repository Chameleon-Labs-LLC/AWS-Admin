from aws_admin.commands import env
from aws_admin import vault


def test_pull_writes_snapshot_and_returns_summary(fake_amplify):
    client = fake_amplify(app_env={"A": "1", "B": "2"}, branch_env={"BR": "x"})
    summary = env.pull("my", client=client)
    snap = vault.load_snapshot("MyApp2")
    assert snap is not None
    assert snap["app_id"] == "d0000000000my0"
    assert snap["app_level"] == {"A": "1", "B": "2"}
    assert snap["branch_level"] == {"BR": "x"}
    assert "2 app-level keys" in summary
    assert "1 branch-level key" in summary
    # Summary leaks no values.
    assert "1" not in summary.replace("2 app-level keys", "").replace("1 branch-level key", "")


def test_pull_handles_missing_branch(fake_amplify):
    client = fake_amplify(app_env={"A": "1"}, branch_exists=False)
    env.pull("my", client=client)
    snap = vault.load_snapshot("MyApp2")
    assert snap is not None
    assert snap["branch_level"] == {}


def test_diff_against_remote_is_key_only(fake_amplify):
    client = fake_amplify(app_env={"A": "1", "STRIPE": "old"}, branch_env={})
    env.pull("my", client=client)
    client2 = fake_amplify(app_env={"A": "1", "STRIPE": "new", "C": "3"}, branch_env={})
    text = env.diff("my", client=client2)
    assert "changed: STRIPE" in text
    assert "added: C" in text
    assert "old" not in text and "new" not in text


def test_diff_without_snapshot_raises(fake_amplify):
    client = fake_amplify(app_env={"A": "1"})
    try:
        env.diff("my", client=client)
        assert False, "expected error"
    except FileNotFoundError as e:
        assert "pull" in str(e)
