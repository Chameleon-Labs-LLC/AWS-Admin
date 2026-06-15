import pytest
from aws_admin import config


@pytest.mark.parametrize("token,expected", [
    ("eo", "ExampleOrg"),
    ("EO", "ExampleOrg"),
    ("exampleorg", "ExampleOrg"),
    ("d0000000000eo0", "ExampleOrg"),
    ("ab", "AppBeta"),
    ("aa", "AppAlpha"),
    ("ag", "AppGamma"),
    ("my", "MyApp2"),
    ("MyApp2", "MyApp2"),
    ("d0000000000my0", "MyApp2"),
])
def test_resolve_app_known(token, expected):
    ref = config.resolve_app(token)
    assert ref.name == expected


def test_resolve_app_ids_correct():
    assert config.resolve_app("my").app_id == "d0000000000my0"
    assert config.resolve_app("eo").app_id == "d0000000000eo0"


def test_resolve_unknown_raises_with_choices():
    with pytest.raises(config.UnknownAppError) as exc:
        config.resolve_app("nope")
    msg = str(exc.value)
    assert "nope" in msg
    assert "MyApp2" in msg and "ExampleOrg" in msg


_SORTED_APP_NAMES = ["AppAlpha", "AppBeta", "AppGamma", "ExampleOrg", "MyApp2"]


def test_known_apps_sorted_alphabetically():
    assert [ref.name for ref in config.known_apps()] == _SORTED_APP_NAMES


def test_app_aliases_sorted_alphabetically():
    assert [name for name, _ in config.app_aliases()] == _SORTED_APP_NAMES


def test_unknown_app_choices_listed_alphabetically():
    with pytest.raises(config.UnknownAppError) as exc:
        config.resolve_app("nope")
    msg = str(exc.value)
    positions = [msg.index(name) for name in _SORTED_APP_NAMES]
    assert positions == sorted(positions)
