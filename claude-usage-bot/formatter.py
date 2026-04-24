"""Formatting utilities for usage data."""

from tabulate import tabulate


def format_usage_table(usage_data: dict) -> str:
    buckets = usage_data.get("data", [])
    if not buckets:
        return "No usage data found."

    rows = []
    has_any_usage = False
    for bucket in buckets:
        # The API returns "results" for grouped queries; "result" appears in some legacy shapes.
        results = bucket.get("results") or bucket.get("result") or []

        uncached_input_tokens = 0
        cache_creation_tokens = 0
        cache_read_tokens = 0
        output_tokens = 0

        for row in results:
            uncached_input_tokens += int(
                row.get("uncached_input_tokens", row.get("input_tokens", 0))
            )
            cache_creation = row.get("cache_creation", {})
            cache_creation_tokens += int(cache_creation.get("ephemeral_1h_input_tokens", 0))
            cache_creation_tokens += int(cache_creation.get("ephemeral_5m_input_tokens", 0))
            cache_read_tokens += int(
                row.get("cache_read_input_tokens", row.get("cached_input_tokens", 0))
            )
            output_tokens += int(row.get("output_tokens", 0))

        # Fallback for non-grouped buckets where totals sit at the bucket level.
        if not results:
            uncached_input_tokens += int(
                bucket.get("uncached_input_tokens", bucket.get("input_tokens", 0))
            )
            cache_creation = bucket.get("cache_creation", {})
            cache_creation_tokens += int(cache_creation.get("ephemeral_1h_input_tokens", 0))
            cache_creation_tokens += int(cache_creation.get("ephemeral_5m_input_tokens", 0))
            cache_read_tokens += int(
                bucket.get("cache_read_input_tokens", bucket.get("cached_input_tokens", 0))
            )
            output_tokens += int(bucket.get("output_tokens", 0))

        total_tokens = uncached_input_tokens + cache_creation_tokens + cache_read_tokens + output_tokens
        if total_tokens > 0:
            has_any_usage = True

        bucket_start = bucket.get("starting_at", "Unknown")
        date_only = bucket_start.split("T", 1)[0] if "T" in bucket_start else bucket_start
        rows.append([date_only, total_tokens])

    if not has_any_usage:
        return "No usage data found for the selected period."

    rows.append(["Total", sum(r[1] for r in rows)])
    display_rows = [[date, f"{round(tokens / 1000)}k"] for date, tokens in rows]
    return tabulate(display_rows, headers=["Date", "Total Tokens"], tablefmt="simple", colalign=("left", "right"))


def format_cost_table(cost_data: dict) -> str:
    buckets = cost_data.get("data", [])
    if not buckets:
        return "No cost data found."

    rows = []
    has_any_cost = False
    for bucket in buckets:
        results = bucket.get("results") or []
        total_cents = sum(float(r.get("amount", 0)) for r in results)
        if total_cents > 0:
            has_any_cost = True

        bucket_start = bucket.get("starting_at", "Unknown")
        date_only = bucket_start.split("T", 1)[0] if "T" in bucket_start else bucket_start
        rows.append([date_only, total_cents])

    if not has_any_cost:
        return "No cost data found for the selected period."

    rows.append(["Total", sum(r[1] for r in rows)])
    display_rows = [[date, f"${cents / 100:.2f}"] for date, cents in rows]
    return tabulate(display_rows, headers=["Date", "Cost (USD)"], tablefmt="simple", colalign=("left", "right"))


def format_workspaces_table(workspaces: dict) -> str:
    if not workspaces:
        return "No workspaces found."

    rows = [[name, workspace_id] for name, workspace_id in workspaces.items()]
    return tabulate(rows, headers=["Workspace Name", "Workspace ID"], tablefmt="simple")
