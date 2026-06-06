from aws_admin.commands import env
from aws_admin import vault


def _seed_local(fake_amplify, app_env, branch_env, local_app, local_branch):
    """Pull remote into a snapshot, then mutate the local snapshot to desired state."""
    client = fake_amplify(app_env=app_env, branch_env=branch_env)
    env.pull("my", client=client)
    snap = vault.load_snapshot("MyApp2")
    assert snap is not None
    snap["app_level"] = local_app
    snap["branch_level"] = local_branch
    vault.save_snapshot("MyApp2", snap)


def test_push_dry_run_does_not_call_update(fake_amplify):
    _seed_local(fake_amplify, {"A": "1"}, {}, {"A": "2"}, {})
    client = fake_amplify(app_env={"A": "1"}, branch_env={})
    out = env.push("my", apply=False, client=client)
    assert "DRY RUN" in out
    assert "changed: A" in out
    assert not any(c[0] == "update_app" for c in client.calls)


def test_push_apply_sends_full_set_and_backs_up(fake_amplify):
    _seed_local(fake_amplify, {"A": "1", "B": "2"}, {}, {"A": "9", "C": "3"}, {})
    client = fake_amplify(app_env={"A": "1", "B": "2"}, branch_env={})
    env.push("my", apply=True, client=client)
    update_calls = [c for c in client.calls if c[0] == "update_app"]
    assert len(update_calls) == 1
    assert update_calls[0][1]["environmentVariables"] == {"A": "9", "C": "3"}
    backups = list((vault.config.state_dir() / "backups").glob("MyApp2-*.enc"))
    assert backups


def test_push_apply_updates_branch_when_branch_vars_present(fake_amplify):
    _seed_local(fake_amplify, {"A": "1"}, {"BR": "x"}, {"A": "1"}, {"BR": "y"})
    client = fake_amplify(app_env={"A": "1"}, branch_env={"BR": "x"})
    env.push("my", apply=True, client=client)
    assert any(c[0] == "update_branch" for c in client.calls)


def test_push_apply_skips_branch_when_none(fake_amplify):
    _seed_local(fake_amplify, {"A": "1"}, {}, {"A": "2"}, {})
    client = fake_amplify(app_env={"A": "1"}, branch_env={})
    env.push("my", apply=True, client=client)
    assert not any(c[0] == "update_branch" for c in client.calls)


def test_push_apply_redeploy_triggers_job(fake_amplify):
    _seed_local(fake_amplify, {"A": "1"}, {}, {"A": "2"}, {})
    client = fake_amplify(app_env={"A": "1"}, branch_env={})
    env.push("my", apply=True, redeploy=True, client=client)
    jobs = [c for c in client.calls if c[0] == "start_job"]
    assert jobs and jobs[0][1]["jobType"] == "RELEASE"


def test_push_apply_clears_branch_vars_when_local_empty(fake_amplify):
    # Remote has branch vars; local snapshot clears them -> update_branch called with {}.
    _seed_local(fake_amplify, {"A": "1"}, {"BR": "x"}, {"A": "1"}, {})
    client = fake_amplify(app_env={"A": "1"}, branch_env={"BR": "x"})
    env.push("my", apply=True, client=client)
    branch_calls = [c for c in client.calls if c[0] == "update_branch"]
    assert len(branch_calls) == 1
    assert branch_calls[0][1]["environmentVariables"] == {}


def test_push_dry_run_warns_on_branch_clear(fake_amplify):
    _seed_local(fake_amplify, {"A": "1"}, {"BR": "x"}, {"A": "1"}, {})
    client = fake_amplify(app_env={"A": "1"}, branch_env={"BR": "x"})
    out = env.push("my", apply=False, client=client)
    assert "CLEARED" in out
    assert "x" not in out  # no values leak in the warning/diff
