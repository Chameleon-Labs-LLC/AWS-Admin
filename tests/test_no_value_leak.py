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
