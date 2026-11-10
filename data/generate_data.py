"""Generate synthetic ad-ops data for AdOps Copilot.

Everything here is fake. No real publisher, advertiser, or employer data is used.
A fixed seed makes the output identical on every run, so eval answers stay checkable.

Outputs (in this folder):
  campaigns.json  - 100 campaigns with goals, dates, format, channel, CPM
  delivery.csv    - one row per campaign per day (30 days)
  inventory.json  - daily forecast of available impressions per placement

Some campaigns are deliberately broken so the copilot has real problems to find:
  - underpacing:      delivery runs well below the daily goal
  - fill_drop:        fill rate collapses partway through the window
  - zero_delivery:    creative rejected, nothing serves after a date
  - overpacing:       delivering far faster than planned
"""

from __future__ import annotations

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

SEED = 42
HERE = Path(__file__).parent
START = date(2026, 10, 1)
DAYS = 30
TODAY = START + timedelta(days=DAYS - 1)  # 2026-10-30, the "current" date in this dataset

ADVERTISERS = [
    "Northwind Foods", "Bluepeak Telecom", "Kestrel Motors", "Lumen Bank", "Saffron Home",
    "Orbit Fintech", "Tidewater Insurance", "Cedar Health", "Monsoon Apparel", "Granite Realty",
    "Pixel Learning", "Harbor Airlines",
]
FORMATS = ["in_stream", "attention_overlay", "l_band", "lower_third"]
PLACEMENTS = ["live_news_primetime", "live_sports", "vod_entertainment", "vod_news_clips"]

# Which campaigns are broken, and how. Fixed so the eval set can reference them.
PROBLEMS = {
    "C007": "underpacing",
    "C013": "underpacing",
    "C021": "fill_drop",
    "C034": "zero_delivery",
    "C042": "overpacing",
    "C058": "underpacing",
    "C066": "fill_drop",
    "C079": "zero_delivery",
}


def make_campaigns(rng: random.Random) -> list[dict]:
    campaigns = []
    for i in range(1, 101):
        cid = f"C{i:03d}"
        start = START + timedelta(days=rng.randint(0, 5))
        length = rng.choice([20, 25, 30, 35, 40])
        end = start + timedelta(days=length - 1)
        goal = rng.choice([500_000, 1_000_000, 2_000_000, 3_000_000, 5_000_000])
        campaigns.append({
            "campaign_id": cid,
            "advertiser": ADVERTISERS[(i - 1) % len(ADVERTISERS)],
            "format": FORMATS[(i - 1) % len(FORMATS)],
            "placement": PLACEMENTS[(i - 1) % len(PLACEMENTS)],
            "channel": "live" if PLACEMENTS[(i - 1) % len(PLACEMENTS)].startswith("live") else "vod",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "goal_impressions": goal,
            "cpm_inr": rng.choice([120, 150, 180, 220, 260]),
            "status": "active" if end >= TODAY else "completed",
        })
    return campaigns


def make_delivery(rng: random.Random, campaigns: list[dict]) -> list[dict]:
    rows = []
    for c in campaigns:
        start = date.fromisoformat(c["start_date"])
        end = date.fromisoformat(c["end_date"])
        flight_days = (end - start).days + 1
        daily_goal = c["goal_impressions"] / flight_days
        problem = PROBLEMS.get(c["campaign_id"])
        for d in range(DAYS):
            day = START + timedelta(days=d)
            if day < start or day > end:
                continue
            day_index = (day - start).days
            pace = rng.uniform(0.95, 1.08)
            fill = rng.uniform(0.86, 0.95)
            if problem == "underpacing":
                pace = rng.uniform(0.45, 0.6)
            elif problem == "overpacing":
                pace = rng.uniform(1.6, 1.9)
            elif problem == "fill_drop" and day_index >= 10:
                fill = rng.uniform(0.35, 0.5)
                pace *= fill / 0.9
            elif problem == "zero_delivery" and day_index >= 8:
                pace, fill = 0.0, 0.0
            impressions = int(daily_goal * pace)
            viewers = int(impressions / rng.uniform(0.9, 0.98)) if impressions else 0
            ad_viewers = int(impressions * rng.uniform(0.95, 0.995)) if impressions else 0
            completions = int(impressions * rng.uniform(0.7, 0.85)) if impressions else 0
            revenue = round(impressions / 1000 * c["cpm_inr"], 2)
            rows.append({
                "campaign_id": c["campaign_id"],
                "date": day.isoformat(),
                "impressions": impressions,
                "viewers": viewers,
                "ad_viewers": ad_viewers,
                "completions": completions,
                "fill_rate": round(fill, 4),
                "revenue_inr": revenue,
            })
    return rows


def make_inventory(rng: random.Random) -> dict:
    inventory = {}
    for p in PLACEMENTS:
        base = {"live_news_primetime": 4_000_000, "live_sports": 6_000_000,
                "vod_entertainment": 3_000_000, "vod_news_clips": 1_500_000}[p]
        inventory[p] = [
            {"date": (TODAY + timedelta(days=d + 1)).isoformat(),
             "forecast_available_impressions": int(base * rng.uniform(0.85, 1.15))}
            for d in range(30)
        ]
    return inventory


def main() -> None:
    rng = random.Random(SEED)
    campaigns = make_campaigns(rng)
    delivery = make_delivery(rng, campaigns)
    inventory = make_inventory(rng)

    (HERE / "campaigns.json").write_text(json.dumps(
        {"as_of": TODAY.isoformat(), "campaigns": campaigns}, indent=2))
    with open(HERE / "delivery.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(delivery[0].keys()))
        w.writeheader()
        w.writerows(delivery)
    (HERE / "inventory.json").write_text(json.dumps(
        {"as_of": TODAY.isoformat(), "placements": inventory}, indent=2))
    print(f"campaigns={len(campaigns)} delivery_rows={len(delivery)} placements={len(inventory)} as_of={TODAY}")


if __name__ == "__main__":
    main()
