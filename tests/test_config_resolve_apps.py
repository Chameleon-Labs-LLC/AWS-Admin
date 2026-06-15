import pytest

from aws_admin import config

# isolated_home (autouse, conftest) seeds these synthetic apps.
_ALL = ["AppAlpha", "AppBeta", "AppGamma", "ExampleOrg", "MyApp2"]


def test_resolve_apps_expands_all():
    assert [r.name for r in config.resolve_apps(["all"])] == _ALL


def test_resolve_apps_all_is_case_insensitive():
    assert [r.name for r in config.resolve_apps(["ALL"])] == _ALL


def test_resolve_apps_all_anywhere_in_list_wins():
    assert [r.name for r in config.resolve_apps(["my", "all"])] == _ALL


def test_resolve_apps_resolves_and_dedupes_preserving_order():
    # 'my' and 'MyApp2' are the same app; 'aa' is AppAlpha.
    refs = config.resolve_apps(["my", "aa", "MyApp2"])
    assert [r.name for r in refs] == ["MyApp2", "AppAlpha"]


def test_resolve_apps_unknown_token_raises():
    with pytest.raises(config.UnknownAppError):
        config.resolve_apps(["definitely-not-an-app"])
