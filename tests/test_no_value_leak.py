from aws_admin.commands import env
from aws_admin import vault

SECRET = "sk_live_DO_NOT_LEAK_d34db33f"


def test_no_command_output_contains_secret_value(fake_amplify):
    # pull
    client = fake_amplify(app_env={"STRIPE": SECRET, "A": "1"}, branch_env={"BR": SECRET})
    assert SECRET not in env.pull("my", client=client)

    # diff (remote changes the secret)
    client2 = fake_amplify(app_env={"STRIPE": SECRET + "X", "A": "1"}, branch_env={"BR": SECRET})
    assert SECRET not in env.diff("my", client=client2)

    # edit a new secret into the snapshot, then ensure push output never shows it
    snap = vault.load_snapshot("MyApp2")
    assert snap is not None
    snap["app_level"]["STRIPE"] = SECRET + "_rotated"
    vault.save_snapshot("MyApp2", snap)

    client3 = fake_amplify(app_env={"STRIPE": SECRET, "A": "1"}, branch_env={"BR": SECRET})
    dry = env.push("my", apply=False, client=client3)
    applied = env.push("my", apply=True, client=client3)
    assert SECRET not in dry
    assert SECRET not in applied
    assert (SECRET + "_rotated") not in dry
    assert (SECRET + "_rotated") not in applied


def test_vault_files_are_never_plaintext(fake_amplify):
    client = fake_amplify(app_env={"STRIPE": SECRET}, branch_env={})
    env.pull("my", client=client)
    blob = vault.config.vault_path("MyApp2").read_bytes()
    assert SECRET.encode() not in blob


def test_rotate_output_never_contains_value():
    snap = {"app_id": "x", "branch": "main",
            "app_level": {"AI_SECRET": "old"}, "branch_level": {}, "pulled_at": "t"}
    vault.save_snapshot("MyApp2", snap)

    def editor(path):
        path.write_text(f"AI_SECRET={SECRET}\n")

    out = env.rotate("AI_SECRET", ["my"], confirm=lambda p: False, open_editor=editor)
    assert SECRET not in out
    # Stored snapshot stays encrypted — value never lands as plaintext on disk.
    assert SECRET.encode() not in vault.config.vault_path("MyApp2").read_bytes()
