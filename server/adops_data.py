"""Read-only data access for AdOps Copilot.

All functions here only read the synthetic dataset. None of them can change it.
The MCP server exposes these as tools; the eval runner uses the same functions
to compute ground-truth answers, so the expected answers and the tools always agree.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Thresholds from the pacing runbook.
UNDERPACING_BELOW = 90.0
SEVERE_BELOW = 70.0
OVERPACING_ABOVE = 110.0

METRICS = ("impressions", "viewers", "ad_viewers", "completions", "revenue_inr", "fill_rate")


class DataError(ValueError):
    """Raised for bad inputs. The message is safe to show to the model and the user."""


@lru_cache(maxsize=1)
def _load() -> tuple[str, dict, list[dict], dict]:
    meta = json.loads((DATA_DIR / "campaigns.json").read_text())
    campaigns = {c["campaign_id"]: c for c in meta["campaigns"]}
    with open(DATA_DIR / "delivery.csv", newline="") as f:
        rows = []
        for r in csv.DictReader(f):
            rows.append({
                "campaign_id": r["campaign_id"],
                "date": r["date"],
                "impressions": int(r["impressions"]),
                "viewers": int(r["viewers"]),
                "ad_viewers": int(r["ad_viewers"]),
                "completions": int(r["completions"]),
                "fill_rate": float(r["fill_rate"]),
                "revenue_inr": float(r["revenue_inr"]),
            })
    inventory = json.loads((DATA_DIR / "inventory.json").read_text())["placements"]
    return meta["as_of"], campaigns, rows, inventory


def as_of() -> str:
    return _load()[0]


def _campaign(campaign_id: str) -> dict:
    cid = (campaign_id or "").strip().upper()
    campaigns = _load()[1]
    if cid not in campaigns:
        raise DataError(f"Unknown campaign_id '{campaign_id}'. IDs look like C001 to C100.")
    return campaigns[cid]


def _parse_date(value: str | None, name: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise DataError(f"{name} must be YYYY-MM-DD, got '{value}'.") from e


def _rows(campaign_id: str | None = None, start: date | None = None, end: date | None = None,
          channel: str | None = None) -> list[dict]:
    _, campaigns, rows, _ = _load()
    out = []
    for r in rows:
        if campaign_id and r["campaign_id"] != campaign_id:
            continue
        if channel and campaigns[r["campaign_id"]]["channel"] != channel:
            continue
        d = date.fromisoformat(r["date"])
        if start and d < start:
            continue
        if end and d > end:
            continue
        out.append(r)
    return out


def _summarize(rows: list[dict]) -> dict:
    imps = sum(r["impressions"] for r in rows)
    viewers = sum(r["viewers"] for r in rows)
    ad_viewers = sum(r["ad_viewers"] for r in rows)
    completions = sum(r["completions"] for r in rows)
    revenue = round(sum(r["revenue_inr"] for r in rows), 2)
    fill = round(sum(r["fill_rate"] for r in rows) / len(rows), 4) if rows else 0.0
    return {
        "days": len(rows),
        "impressions": imps,
        "viewers": viewers,
        "ad_viewers": ad_viewers,
        "ad_reach_rate": round(ad_viewers / viewers, 4) if viewers else 0.0,
        "completions": completions,
        "completion_rate": round(completions / imps, 4) if imps else 0.0,
        "avg_fill_rate": fill,
        "revenue_inr": revenue,
        "ecpm_inr": round(revenue / imps * 1000, 2) if imps else 0.0,
    }


# ---------------------------------------------------------------- tools

def list_campaigns(status: str | None = None, advertiser: str | None = None,
                   channel: str | None = None, ad_format: str | None = None) -> dict:
    """List campaigns, optionally filtered."""
    campaigns = _load()[1].values()
    out = []
    for c in campaigns:
        if status and c["status"] != status.lower():
            continue
        if advertiser and advertiser.lower() not in c["advertiser"].lower():
            continue
        if channel and c["channel"] != channel.lower():
            continue
        if ad_format and c["format"] != ad_format.lower():
            continue
        out.append({k: c[k] for k in ("campaign_id", "advertiser", "format", "channel",
                                       "placement", "status", "start_date", "end_date")})
    return {"as_of": as_of(), "count": len(out), "campaigns": out}


def get_campaign_performance(campaign_id: str, start_date: str | None = None,
                             end_date: str | None = None) -> dict:
    """Totals and rates for one campaign over a date range (default: everything delivered so far)."""
    c = _campaign(campaign_id)
    start, end = _parse_date(start_date, "start_date"), _parse_date(end_date, "end_date")
    if start and end and start > end:
        raise DataError("start_date is after end_date.")
    summary = _summarize(_rows(c["campaign_id"], start, end))
    return {"campaign_id": c["campaign_id"], "advertiser": c["advertiser"],
            "range": {"start": start_date or c["start_date"], "end": end_date or as_of()},
            **summary}


def get_pacing(campaign_id: str) -> dict:
    """Delivered vs expected impressions to date, with the runbook's status label."""
    c = _campaign(campaign_id)
    today = date.fromisoformat(as_of())
    start, end = date.fromisoformat(c["start_date"]), date.fromisoformat(c["end_date"])
    flight_days = (end - start).days + 1
    last_day = min(today, end)
    elapsed = max(0, (last_day - start).days + 1)
    expected = round(c["goal_impressions"] * elapsed / flight_days)
    delivered = sum(r["impressions"] for r in _rows(c["campaign_id"], start, last_day))
    pacing = round(delivered / expected * 100, 1) if expected else 0.0
    if pacing < SEVERE_BELOW:
        label = "severely_underpacing"
    elif pacing < UNDERPACING_BELOW:
        label = "underpacing"
    elif pacing > OVERPACING_ABOVE:
        label = "overpacing"
    else:
        label = "on_track"
    return {"campaign_id": c["campaign_id"], "advertiser": c["advertiser"], "status": c["status"],
            "goal_impressions": c["goal_impressions"], "days_elapsed": elapsed,
            "flight_days": flight_days, "expected_to_date": expected, "delivered_to_date": delivered,
            "pacing_pct": pacing, "pacing_status": label}


def list_pacing_issues(include_completed: bool = False) -> dict:
    """All campaigns that are underpacing or overpacing (active only by default)."""
    issues = []
    for cid, c in _load()[1].items():
        if not include_completed and c["status"] != "active":
            continue
        p = get_pacing(cid)
        if p["pacing_status"] != "on_track":
            issues.append({k: p[k] for k in ("campaign_id", "advertiser", "status",
                                              "pacing_pct", "pacing_status")})
    issues.sort(key=lambda x: x["pacing_pct"])
    return {"as_of": as_of(), "count": len(issues), "issues": issues}


def get_daily_delivery(campaign_id: str, last_n_days: int = 7) -> dict:
    """Day-by-day delivery for one campaign, most recent days."""
    c = _campaign(campaign_id)
    n = max(1, min(int(last_n_days), 30))
    rows = _rows(c["campaign_id"])[-n:]
    return {"campaign_id": c["campaign_id"], "days": [
        {k: r[k] for k in ("date", "impressions", "fill_rate", "revenue_inr")} for r in rows]}


def compare_periods(metric: str, period_a_start: str, period_a_end: str,
                    period_b_start: str, period_b_end: str,
                    campaign_id: str | None = None, channel: str | None = None) -> dict:
    """Compare one metric between two date ranges, for a campaign, a channel, or everything."""
    if metric not in METRICS:
        raise DataError(f"metric must be one of {', '.join(METRICS)}.")
    if channel and channel.lower() not in ("live", "vod"):
        raise DataError("channel must be 'live' or 'vod'.")
    cid = _campaign(campaign_id)["campaign_id"] if campaign_id else None
    ch = channel.lower() if channel else None

    def total(s: str, e: str) -> float:
        rows = _rows(cid, _parse_date(s, "start"), _parse_date(e, "end"), ch)
        if metric == "fill_rate":
            return round(sum(r["fill_rate"] for r in rows) / len(rows), 4) if rows else 0.0
        return round(sum(r[metric] for r in rows), 2)

    a, b = total(period_a_start, period_a_end), total(period_b_start, period_b_end)
    change = round((b - a) / a * 100, 1) if a else None
    return {"metric": metric, "scope": cid or (f"channel={ch}" if ch else "all campaigns"),
            "period_a": {"start": period_a_start, "end": period_a_end, "value": a},
            "period_b": {"start": period_b_start, "end": period_b_end, "value": b},
            "change_pct_b_vs_a": change}


def get_inventory_forecast(placement: str, days: int = 7) -> dict:
    """Forecast available impressions for a placement over the next N days."""
    inventory = _load()[3]
    if placement not in inventory:
        raise DataError(f"Unknown placement '{placement}'. Options: {', '.join(inventory)}.")
    n = max(1, min(int(days), 30))
    daily = inventory[placement][:n]
    return {"placement": placement, "days": n,
            "total_forecast_impressions": sum(d["forecast_available_impressions"] for d in daily),
            "daily": daily}


def last_week() -> tuple[str, str, str, str]:
    """(previous week start, end, this week start, end) relative to the dataset date."""
    today = date.fromisoformat(as_of())
    this_start = today - timedelta(days=6)
    prev_end = this_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=6)
    return (prev_start.isoformat(), prev_end.isoformat(), this_start.isoformat(), today.isoformat())
