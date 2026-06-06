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
