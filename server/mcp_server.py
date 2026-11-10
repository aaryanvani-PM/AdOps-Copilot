"""AdOps Copilot MCP server.

Exposes read-only ad-ops tools over the Model Context Protocol (MCP), so any
MCP-compatible client (this project's agent, Claude Desktop, an IDE) can use them.

Every tool is read-only: there is no tool that can pause, edit, or create a campaign.
That is the first guardrail, and it does not depend on the model behaving.

Run directly:  python -m server.mcp_server
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as a plain script (python server/mcp_server.py), e.g. from Claude Desktop.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

from server import adops_data as data  # noqa: E402
from server.retrieval import search_runbooks as _search_runbooks  # noqa: E402

mcp = MCPServer(
    name="adops-copilot",
    instructions="Read-only ad-ops data tools over a synthetic dataset. No tool can change a campaign.",
)
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True,
                            openWorldHint=False)


def _safe(fn, *args, **kwargs) -> dict:
    """Return bad-input errors as data, so the model can read them and recover."""
    try:
        return fn(*args, **kwargs)
    except data.DataError as e:
        return {"error": str(e)}


@mcp.tool(annotations=READ_ONLY)
def list_campaigns(status: str | None = None, advertiser: str | None = None,
                   channel: str | None = None, ad_format: str | None = None) -> dict:
    """List campaigns. Filters: status ('active' or 'completed'), advertiser (name contains),
    channel ('live' or 'vod'), ad_format ('in_stream', 'attention_overlay', 'l_band', 'lower_third')."""
    return _safe(data.list_campaigns, status, advertiser, channel, ad_format)


@mcp.tool(annotations=READ_ONLY)
def get_campaign_performance(campaign_id: str, start_date: str | None = None,
                             end_date: str | None = None) -> dict:
    """Totals and rates for one campaign: impressions, viewers, ad viewers, ad reach rate,
    completion rate, average fill rate, revenue (INR), eCPM. Dates are YYYY-MM-DD and optional."""
    return _safe(data.get_campaign_performance, campaign_id, start_date, end_date)


@mcp.tool(annotations=READ_ONLY)
def get_pacing(campaign_id: str) -> dict:
    """Pacing for one campaign: delivered vs expected impressions to date, pacing %, and status
    (on_track, underpacing, severely_underpacing, overpacing)."""
    return _safe(data.get_pacing, campaign_id)


@mcp.tool(annotations=READ_ONLY)
def list_pacing_issues(include_completed: bool = False) -> dict:
    """All campaigns that are underpacing or overpacing. Active campaigns only unless
    include_completed is true."""
    return _safe(data.list_pacing_issues, include_completed)


@mcp.tool(annotations=READ_ONLY)
def get_daily_delivery(campaign_id: str, last_n_days: int = 7) -> dict:
    """Day-by-day impressions, fill rate, and revenue for one campaign (most recent days).
    Use it to find when a problem started."""
    return _safe(data.get_daily_delivery, campaign_id, last_n_days)


@mcp.tool(annotations=READ_ONLY)
def compare_periods(metric: str, period_a_start: str, period_a_end: str,
                    period_b_start: str, period_b_end: str,
                    campaign_id: str | None = None, channel: str | None = None) -> dict:
    """Compare a metric between two date ranges for one campaign, a channel ('live' or 'vod'),
    or all campaigns. metric: impressions, viewers, ad_viewers, completions, revenue_inr, fill_rate."""
    return _safe(data.compare_periods, metric, period_a_start, period_a_end,
                 period_b_start, period_b_end, campaign_id, channel)


@mcp.tool(annotations=READ_ONLY)
def get_inventory_forecast(placement: str, days: int = 7) -> dict:
    """Forecast available impressions for a placement over the next N days. Placements:
    live_news_primetime, live_sports, vod_entertainment, vod_news_clips."""
    return _safe(data.get_inventory_forecast, placement, days)


@mcp.tool(annotations=READ_ONLY)
def search_runbooks(query: str, k: int = 3) -> dict:
    """Search ad-ops runbooks and policies (pacing, fill drops, creatives, escalation, ad quality,
    make-goods, metric definitions, data access). Returns sections with their source file."""
    return _search_runbooks(query, k)


@mcp.tool(annotations=READ_ONLY)
def get_dataset_info() -> dict:
    """The dataset's current date and the date ranges for 'this week' and 'last week'."""
    prev_start, prev_end, this_start, this_end = data.last_week()
    return {"as_of": data.as_of(),
            "last_week": {"start": prev_start, "end": prev_end},
            "this_week": {"start": this_start, "end": this_end},
            "note": "All data is synthetic."}


if __name__ == "__main__":
    mcp.run("stdio")
