"""AWS cost & free-tier-expiry report. Read-only: Cost Explorer + Free Tier APIs.

No secret values appear in output. The account ID is redacted to its last four
digits — consistent with the rest of aws-admin, which treats the full account
ID as sensitive. Cost Explorer bills ~$0.01 per paid request; one report makes
a handful of them.
"""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass

from .. import aws_client, config

# Standard on-demand us-east-1 rates for the items AWS tracks under the
# 12-month free tier. Used ONLY to estimate the post-expiry bill increase.
# Keyed by the Free-Tier API ``usageType`` (matched exact-first, then by
# substring so region-prefixed variants like "USE1-..." still resolve).
# Edit these if AWS pricing changes — they are deliberately transparent and
# approximate the increase, not an authoritative invoice.
RATES: dict[str, tuple[float, str]] = {
    "BuildDuration":                 (0.01,       "$0.01 / build-minute"),
    "DataStorage":                   (0.023,      "$0.023 / GB-mo"),
    "DataTransferOut":               (0.15,       "$0.15 / GB served"),
    "HostingComputeRequestCount":    (0.0000003,  "$0.30 / 1M SSR requests"),
    "HostingComputeRequestDuration": (0.00005556, "$0.20 / GB-hour SSR compute"),
    "EBS:VolumeUsage":               (0.08,       "$0.08 / GB-mo (gp3; gp2 $0.10)"),
    "PublicIPv4:InUseAddress":       (0.005,      "$0.005 / IPv4 address-hour"),
    "TimedStorage-ByteHrs":          (0.023,      "$0.023 / GB-mo (S3 Standard)"),
    "Requests-Tier1":                (0.000005,   "$0.005 / 1k S3 write reqs"),
    "Requests-Tier2":                (0.0000004,  "$0.0004 / 1k S3 read reqs"),
    "MessageUnits":                  (0.0001,     "$0.10 / 1k SES messages"),
}


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _redact_account(account: str) -> str:
    acct = account or ""
    return f"****{acct[-4:]}" if len(acct) >= 4 else "****"


def _month_window(n: int, today: dt.date) -> tuple[str, str]:
    """First-of-month n months back, and first of next month (CE end is exclusive)."""
    back = (today.year * 12 + (today.month - 1)) - (n - 1)
    start = dt.date(back // 12, back % 12 + 1, 1)
    nxt = dt.date(today.year + (today.month // 12), today.month % 12 + 1, 1)
    return start.isoformat(), nxt.isoformat()


# ---------------------------------------------------------------------------
# Fetchers (each takes an injected boto3 client for testability)
# ---------------------------------------------------------------------------
def _monthly_trend(ce, start: str, end: str) -> list[tuple[str, float]]:
    res = ce.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="MONTHLY", Metrics=["UnblendedCost"],
    )
    return [
        (r["TimePeriod"]["Start"], _f(r["Total"]["UnblendedCost"]["Amount"]))
        for r in res.get("ResultsByTime", [])
    ]


def _by_service(ce, start: str, end: str) -> list[tuple[str, float]]:
    res = ce.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="MONTHLY", Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    rows = res.get("ResultsByTime", [])
    if not rows:
        return []
    out = [
        (g["Keys"][0], _f(g["Metrics"]["UnblendedCost"]["Amount"]))
        for g in rows[0].get("Groups", [])
    ]
    return sorted((r for r in out if r[1] > 0), key=lambda r: r[1], reverse=True)


def _month_projection(ce, today: dt.date) -> tuple[float, int, int, float]:
    """(mtd_actual, days_billed, days_in_month, linear run-rate projection).

    A linear run-rate is used instead of Cost Explorer's get-cost-forecast,
    which is unreliable over the short remaining-days window with lumpy charges.
    """
    month_start = today.replace(day=1)
    days_billed = today.day - 1  # CE end is exclusive: MTD covers day 1..yesterday
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    mtd = 0.0
    if days_billed > 0:
        res = ce.get_cost_and_usage(
            TimePeriod={"Start": month_start.isoformat(), "End": today.isoformat()},
            Granularity="MONTHLY", Metrics=["UnblendedCost"],
        )
        mtd = sum(_f(r["Total"]["UnblendedCost"]["Amount"]) for r in res.get("ResultsByTime", []))
    projection = (mtd / days_billed * days_in_month) if days_billed else 0.0
    return mtd, days_billed, days_in_month, projection


@dataclass
class FreeTierItem:
    service: str
    usage_type: str
    free_type: str
    used: float
    forecast: float
    limit: float
    unit: str

    def lookup_rate(self) -> tuple[float, str] | None:
        if self.usage_type in RATES:
            return RATES[self.usage_type]
        for key, val in RATES.items():
            if key in self.usage_type:
                return val
        return None

    def new_monthly_cost(self) -> float | None:
        """Newly-billable cost/mo after expiry = currently-free units × rate."""
        rate = self.lookup_rate()
        if rate is None:
            return None
        billable = min(self.forecast, self.limit) if self.limit else self.forecast
        return billable * rate[0]


def _free_tier(client) -> list[FreeTierItem]:
    raw: list[dict] = []
    token = None
    while True:
        kwargs = {"maxResults": 100}
        if token:
            kwargs["nextToken"] = token
        resp = client.get_free_tier_usage(**kwargs)
        raw.extend(resp.get("freeTierUsages", []))
        token = resp.get("nextToken")
        if not token:
            break
    return [
        FreeTierItem(
            service=u.get("service", "?"),
            usage_type=u.get("usageType", "?"),
            free_type=u.get("freeTierType", "?"),
            used=_f(u.get("actualUsageAmount")),
            forecast=_f(u.get("forecastedUsageAmount")),
            limit=_f(u.get("limit")),
            unit=u.get("unit", ""),
        )
        for u in raw
    ]


# ---------------------------------------------------------------------------
# Public command
# ---------------------------------------------------------------------------
def report(
    months: int = 6,
    out: str | None = None,
    *,
    ce=None,
    freetier=None,
    sts=None,
    today: dt.date | None = None,
) -> str:
    """Build the Markdown cost report. Clients/today are injectable for tests."""
    ce = ce if ce is not None else aws_client.ce_client()
    freetier = freetier if freetier is not None else aws_client.freetier_client()
    sts = sts if sts is not None else aws_client.sts_client()
    today = today or dt.date.today()

    account = _redact_account(sts.get_caller_identity().get("Account", ""))
    start, end = _month_window(months, today)
    trend = _monthly_trend(ce, start, end)

    cur_first = today.replace(day=1)
    prev_first = dt.date(cur_first.year - (1 if cur_first.month == 1 else 0),
                         12 if cur_first.month == 1 else cur_first.month - 1, 1)
    by_prev = _by_service(ce, prev_first.isoformat(), cur_first.isoformat())
    by_cur = _by_service(ce, cur_first.isoformat(), today.isoformat()) if today != cur_first else []

    mtd, days_billed, days_in_month, projected = _month_projection(ce, today)
    free = _free_tier(freetier)

    L: list[str] = []
    L.append(f"# AWS Cost Report — account {account}")
    L.append("")
    L.append(f"_Generated {today.isoformat()} · region {config.REGION} · "
             f"read-only Cost Explorer + Free Tier APIs_")
    L.append("")

    L.append("## Monthly spend trend (UnblendedCost)")
    L.append("")
    L.append("| Month | Total (USD) |")
    L.append("|---|---:|")
    for m, amt in trend:
        L.append(f"| {m[:7]} | ${amt:,.2f} |")
    L.append("")

    L.append("## Current month")
    L.append("")
    L.append(f"- Month-to-date actual ({days_billed} of {days_in_month} days): **${mtd:,.2f}**")
    L.append(f"- Projected total (linear run-rate): **${projected:,.2f}**")
    L.append("")

    def _svc(title: str, rows: list[tuple[str, float]]) -> None:
        L.append(f"## {title}")
        L.append("")
        if not rows:
            L.append("_(no data)_")
            L.append("")
            return
        L.append("| Service | USD |")
        L.append("|---|---:|")
        for name, amt in rows:
            L.append(f"| {name} | ${amt:,.2f} |")
        L.append(f"| **Total** | **${sum(a for _, a in rows):,.2f}** |")
        L.append("")

    _svc(f"By service — {prev_first.isoformat()[:7]} (last full month)", by_prev)
    _svc(f"By service — {cur_first.isoformat()[:7]} (partial, through {today.isoformat()})", by_cur)

    twelve = [i for i in free if i.free_type == "12 Months Free"]
    trials = [i for i in free if i.free_type == "Free Trial"]

    L.append("## Free-tier-expiry impact (12-month benefits)")
    L.append("")
    if not twelve:
        L.append("_No active 12-month free-tier benefits found._")
        L.append("")
    else:
        L.append("Estimated NEW monthly cost once the 12-month free tier ends "
                 "(= currently-free usage × standard rate):")
        L.append("")
        L.append("| Service | Usage type | Free limit | Forecast | Unit | Rate | New $/mo |")
        L.append("|---|---|---:|---:|---|---|---:|")
        total = 0.0
        unknown = False
        for i in sorted(twelve, key=lambda x: (x.new_monthly_cost() or 0), reverse=True):
            cost = i.new_monthly_cost()
            rate = i.lookup_rate()
            rate_s = rate[1] if rate else "—"
            if cost is None:
                cost_s, unknown = "n/a", True
            else:
                cost_s = f"${cost:,.2f}"
                total += cost
            L.append(f"| {i.service} | {i.usage_type} | {i.limit:,.0f} | "
                     f"{i.forecast:,.0f} | {i.unit} | {rate_s} | {cost_s} |")
        L.append(f"| | | | | | **Estimated increase** | **${total:,.2f}/mo** |")
        L.append("")
        if unknown:
            L.append("> Rows marked `n/a` have no rate in the script's table — usage is "
                     "shown so you can price them manually if material.")
            L.append("")

    if trials:
        L.append("### ⚠️ Active free trials (separate from the 12-month tier)")
        L.append("")
        L.append("These bill $0 today but start charging when the trial ends:")
        L.append("")
        L.append("| Service | Usage type | Used | Trial limit | Unit |")
        L.append("|---|---|---:|---:|---|")
        for i in trials:
            L.append(f"| {i.service} | {i.usage_type} | {i.used:,.0f} | {i.limit:,.0f} | {i.unit} |")
        L.append("")

    L.append("---")
    L.append("_Estimates use on-demand us-east-1 rates in `cost.py` (`RATES`). They "
             "approximate the bill increase, not an exact invoice._")
    text = "\n".join(L)

    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        text += f"\n\n[written] {out}"
    return text
