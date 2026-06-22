import datetime as dt

from aws_admin.commands import cost


def _run(cost_clients, **kw):
    ce, ft, sts = cost_clients
    return cost.report(ce=ce, freetier=ft, sts=sts, today=dt.date(2026, 6, 22), **kw)


def test_account_is_redacted(cost_clients):
    text = _run(cost_clients)
    assert "****9012" in text
    assert "123456789012" not in text  # full account ID never appears


def test_trend_lists_each_month(cost_clients):
    text = _run(cost_clients, months=6)
    for m in ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"):
        assert f"| {m} |" in text


def test_by_service_drops_zero_rows(cost_clients):
    text = _run(cost_clients)
    assert "Amazon EC2" in text
    assert "AWS WAF" in text
    assert "Zero Svc" not in text  # $0.00 services are filtered out


def test_month_projection_uses_run_rate(cost_clients):
    text = _run(cost_clients)
    # 21 of 30 days, $100 MTD -> 100/21*30 = $142.86
    assert "Month-to-date actual (21 of 30 days): **$100.00**" in text
    assert "$142.86" in text


def test_free_tier_estimate(cost_clients):
    text = _run(cost_clients)
    # BuildDuration: min(1175,1000)*0.01 = $10.00
    assert "$10.00" in text
    # PublicIPv4: min(1023,750)*0.005 = $3.75
    assert "$3.75" in text
    # total increase = 13.75
    assert "$13.75/mo" in text
    # Always-Free items are not counted as an expiry impact
    assert "AWS Lambda" not in text.split("Free-tier-expiry impact")[1].split("Active free trials")[0]


def test_free_trial_is_flagged_separately(cost_clients):
    text = _run(cost_clients)
    assert "Active free trials" in text
    assert "Amazon DevOps Guru" in text


def test_out_writes_file(cost_clients, tmp_path):
    out = tmp_path / "report.md"
    text = _run(cost_clients, out=str(out))
    assert out.exists()
    assert out.read_text().startswith("# AWS Cost Report")
    assert f"[written] {out}" in text
