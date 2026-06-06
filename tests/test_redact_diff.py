from aws_admin import redact


def test_key_diff_detects_changes_by_value_not_hash():
    old = {"A": "1", "B": "2", "STRIPE": "old"}
    new = {"A": "1", "C": "3", "STRIPE": "new"}
    d = redact.key_diff(old, new)
    assert d == {"added": ["C"], "removed": ["B"], "changed": ["STRIPE"]}


def test_key_diff_no_values_in_output():
    old = {"SECRET": "top-secret-value"}
    new = {"SECRET": "different-secret"}
    d = redact.key_diff(old, new)
    flat = str(d)
    assert "top-secret-value" not in flat
    assert "different-secret" not in flat


def test_format_diff_key_only():
    d = {"added": ["C"], "removed": ["B"], "changed": ["STRIPE"]}
    text = redact.format_diff(d)
    assert "added: C" in text
    assert "removed: B" in text
    assert "changed: STRIPE" in text


def test_format_diff_empty():
    d = {"added": [], "removed": [], "changed": []}
    assert redact.format_diff(d) == "no changes"
