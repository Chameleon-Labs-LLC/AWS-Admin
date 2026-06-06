from aws_admin.commands import env
from aws_admin import vault


def test_redeploy_starts_release_job(fake_amplify):
    client = fake_amplify(app_env={"A": "1"})
    out = env.redeploy("my", client=client)
    jobs = [c for c in client.calls if c[0] == "start_job"]
    assert jobs and jobs[0][1]["jobType"] == "RELEASE"
    assert "RELEASE" in out


def test_edit_delegates_to_vault(fake_amplify, monkeypatch):
    client = fake_amplify(app_env={"A": "1"})
    env.pull("my", client=client)
    called = {}

    def fake_edit(app_name, _open_editor=None):
        called["app"] = app_name
        return True

    monkeypatch.setattr(vault, "edit_app_buffer", fake_edit)
    out = env.edit("my")
    assert called["app"] == "MyApp2"
    assert "updated" in out.lower()
