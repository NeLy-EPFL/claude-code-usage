import csv
import io
import os
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from tzlocal import get_localzone

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from claude import AnthropicUsageClient
import formatter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger(__name__)

app = App(token=os.environ.get("SLACK_BOT_TOKEN"))
usage_client = AnthropicUsageClient()

_TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _parse_utc(s: str) -> datetime:
    return datetime.strptime(s, _TIMESTAMP_FMT).replace(tzinfo=timezone.utc)


def _reply(client, channel: str, text: str, thread_ts: str = None):
    return client.chat_postMessage(
        channel=channel,
        text=text,
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
        **({"thread_ts": thread_ts} if thread_ts else {}),
    )


def _reply_json(client, channel: str, data: dict, filename: str, title: str) -> None:
    json_str = json.dumps(data, indent=2)
    if len(json_str) > 3000:
        _reply(client, channel, "Raw JSON is large. Uploading as file...")
        client.files_upload_v2(
            channel=channel,
            file=json_str.encode(),
            filename=filename,
            title=title,
        )
    else:
        _reply(client, channel, f"{title}\n```json\n{json_str}\n```")


def _get_usage_table_for_workspace(workspace: str) -> str:
    data = usage_client.query_usage_current_month(workspace)
    data = usage_client.filter_data_for_workspace(data, workspace)
    return formatter.format_usage_table(data)


def _get_cost_table_for_workspace(workspace: str) -> str:
    data = usage_client.query_cost_current_month(workspace)
    data = usage_client.filter_data_for_workspace(data, workspace)
    return formatter.format_cost_table(data)


def _parse_year_month(s: str):
    try:
        dt = datetime.strptime(s, "%Y-%m").replace(tzinfo=timezone.utc)
    except ValueError:
        raise ValueError(f"Invalid month format '{s}'. Use yyyy-mm (e.g. 2026-04).")
    start = dt.replace(day=1)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, end


def _generate_monthly_summary_csvs(year_month: str):
    start, end = _parse_year_month(year_month)
    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_usage = ex.submit(usage_client.query_usage, "all", start, end, "1d")
        fut_cost = ex.submit(usage_client.query_cost, "all", start, end)
    usage_data = fut_usage.result()
    cost_data = fut_cost.result()

    id_to_name = {v: k for k, v in usage_client.get_all_workspaces().items()}

    ws_tokens: dict[str, int] = {}
    ws_costs: dict[str, float] = {}
    daily: dict = defaultdict(lambda: {
        "uncached_input_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
        "cost_cents": 0.0,
    })

    for bucket in usage_data.get("data", []):
        date = bucket.get("starting_at", "")[:10]
        for row in bucket.get("results", []):
            ws_id = row.get("workspace_id")
            if not ws_id:
                continue
            cc = row.get("cache_creation", {})
            uncached = int(row.get("uncached_input_tokens", 0))
            cache_creation = (
                int(cc.get("ephemeral_1h_input_tokens", 0))
                + int(cc.get("ephemeral_5m_input_tokens", 0))
            )
            cache_read = int(row.get("cache_read_input_tokens", 0))
            output = int(row.get("output_tokens", 0))
            e = daily[(date, ws_id)]
            e["uncached_input_tokens"] += uncached
            e["cache_creation_tokens"] += cache_creation
            e["cache_read_input_tokens"] += cache_read
            e["output_tokens"] += output
            ws_tokens[ws_id] = ws_tokens.get(ws_id, 0) + uncached + cache_creation + cache_read + output

    for bucket in cost_data.get("data", []):
        date = bucket.get("starting_at", "")[:10]
        for row in bucket.get("results", []):
            ws_id = row.get("workspace_id")
            if not ws_id:
                continue
            amount = float(row.get("amount", 0))
            daily[(date, ws_id)]["cost_cents"] += amount
            ws_costs[ws_id] = ws_costs.get(ws_id, 0.0) + amount

    all_ws_ids = sorted(ws_tokens.keys() | ws_costs.keys(), key=lambda wid: id_to_name.get(wid, wid))

    summary_buf = io.StringIO()
    w = csv.writer(summary_buf)
    w.writerow(["workspace_name", "total_tokens", "total_cost_usd"])
    for ws_id in all_ws_ids:
        w.writerow([
            id_to_name.get(ws_id, ws_id),
            ws_tokens.get(ws_id, 0),
            round(ws_costs.get(ws_id, 0.0) / 100, 4),
        ])

    daily_buf = io.StringIO()
    w2 = csv.writer(daily_buf)
    w2.writerow([
        "date", "workspace_name",
        "uncached_input_tokens", "cache_creation_tokens",
        "cache_read_input_tokens", "output_tokens",
        "total_tokens", "cost_usd",
    ])
    for (date, ws_id) in sorted(daily.keys()):
        e = daily[(date, ws_id)]
        total = (e["uncached_input_tokens"] + e["cache_creation_tokens"]
                 + e["cache_read_input_tokens"] + e["output_tokens"])
        w2.writerow([
            date,
            id_to_name.get(ws_id, ws_id),
            e["uncached_input_tokens"],
            e["cache_creation_tokens"],
            e["cache_read_input_tokens"],
            e["output_tokens"],
            total,
            round(e["cost_cents"] / 100, 4),
        ])

    return summary_buf.getvalue().encode(), daily_buf.getvalue().encode()


def _upload_monthly_csvs(client, channel: str, year_month: str, summary_csv: bytes, daily_csv: bytes, thread_ts: str = None) -> None:
    extra = {"thread_ts": thread_ts} if thread_ts else {}
    client.files_upload_v2(
        channel=channel,
        file=summary_csv,
        filename=f"monthly_summary_{year_month}.csv",
        title=f"Monthly Summary - {year_month}",
        **extra,
    )
    client.files_upload_v2(
        channel=channel,
        file=daily_csv,
        filename=f"monthly_daily_{year_month}.csv",
        title=f"Monthly Daily Breakdown - {year_month}",
        **extra,
    )


def _send_monthly_report_to_channel() -> None:
    channel = os.environ.get("MONTHLY_REPORT_CHANNEL")
    if not channel:
        logger.warning("MONTHLY_REPORT_CHANNEL not set; skipping monthly report.")
        return

    now = datetime.now()
    year_month = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")

    logger.info(f"Sending monthly report for {year_month} to {channel}")
    msg = _reply(app.client, channel, f"*Monthly Claude usage summary for {year_month}*")
    channel_id = msg["channel"]
    thread_ts = msg["ts"]
    try:
        summary_csv, daily_csv = _generate_monthly_summary_csvs(year_month)
        _upload_monthly_csvs(app.client, channel_id, year_month, summary_csv, daily_csv, thread_ts)
        logger.info(f"Monthly report for {year_month} sent to {channel}")
    except Exception as e:
        logger.error(f"Failed to send monthly report for {year_month}: {e}")
        try:
            _reply(app.client, channel_id, f"Failed to generate report: `{e}`", thread_ts=thread_ts)
        except Exception as reply_err:
            logger.error(f"Failed to post error to thread: {reply_err}")


def _help_text() -> str:
    return (
        "*Possible commands:*\n"
        "1. `help`\n"
        "2. `list workspaces`\n"
        "3. `usage <workspace_name>`\n"
        "    - 'workspace_name' can also be 'all'\n"
        "4. `cost <workspace_name>`\n"
        "    - 'workspace_name' can also be 'all'\n"
        "5. `usage-rawjson <workspace_name> <start_time> <end_time> <bucket_width>`\n"
        "    - 'start_time' and 'end_time' are in ISO format (e.g. '2026-04-20T00:00:00Z')\n"
        "    - 'bucket_width' is '1d' (day), '1h' (hour), or '1m' (minute).\n"
        "6. `cost-rawjson <workspace_name> <start_time> <end_time>`\n"
        "    - 'start_time' and 'end_time' are in ISO format (e.g. '2026-04-20T00:00:00Z')\n"
        "7. `monthly-summary <yyyy-mm>`\n"
        "    - Generates two CSV files for the month:\n"
        "      • Workspace totals (name, tokens, cost)\n"
        "      • Daily breakdown per workspace (date, token types, cost)\n\n"
        "*Example:* `usage sibo-base`\n"
    )


@app.event("message")
def handle_dm_message(event, client, logger):
    if event.get("channel_type") != "im":
        return
    if event.get("subtype"):
        return
    if event.get("bot_id"):
        return

    channel = event.get("channel")
    text = (event.get("text") or "").strip()
    normalized = " ".join(text.split())
    lower_text = normalized.lower()

    if not channel:
        return

    try:
        if lower_text == "help":
            _reply(client, channel, _help_text())
            return

        if lower_text == "list workspaces":
            _reply(client, channel, "Fetching data...")
            workspaces = usage_client.get_all_workspaces()
            table = formatter.format_workspaces_table(workspaces)
            _reply(client, channel, f"Claude Workspaces\n```\n{table}\n```")
            return

        if lower_text.startswith("usage-rawjson "):
            parts = normalized.split()
            if len(parts) != 5:
                _reply(client, channel, _help_text())
                return

            workspace = parts[1].strip('"')
            bucket_width = parts[4]

            try:
                start_time = _parse_utc(parts[2])
                end_time = _parse_utc(parts[3])
            except ValueError:
                _reply(client, channel, _help_text())
                return

            if bucket_width not in usage_client.ALLOWED_BUCKET_WIDTHS:
                _reply(client, channel, _help_text())
                return

            _reply(client, channel, "Fetching data...")
            data = usage_client.query_usage(workspace, start_time, end_time, bucket_width)
            data = usage_client.filter_data_for_workspace(data, workspace)
            _reply_json(client, channel, data,
                        filename=f"usage_data_{workspace}.json",
                        title=f"Usage Raw JSON - {workspace}")
            return

        if lower_text.startswith("cost-rawjson "):
            parts = normalized.split()
            if len(parts) != 4:
                _reply(client, channel, _help_text())
                return

            workspace = parts[1].strip('"')

            try:
                start_time = _parse_utc(parts[2])
                end_time = _parse_utc(parts[3])
            except ValueError:
                _reply(client, channel, _help_text())
                return

            _reply(client, channel, "Fetching data...")
            data = usage_client.query_cost(workspace, start_time, end_time)
            data = usage_client.filter_data_for_workspace(data, workspace)
            _reply_json(client, channel, data,
                        filename=f"cost_data_{workspace}.json",
                        title=f"Cost Raw JSON - {workspace}")
            return

        if lower_text.startswith("usage "):
            parts = normalized.split(maxsplit=1)
            if len(parts) != 2:
                _reply(client, channel, _help_text())
                return

            workspace = parts[1].strip().strip('"')
            _reply(client, channel, "Fetching data...")
            table = _get_usage_table_for_workspace(workspace)
            _reply(client, channel, f"Token usage for {workspace} this month\n```\n{table}\n```")
            return

        if lower_text.startswith("cost "):
            parts = normalized.split(maxsplit=1)
            if len(parts) != 2:
                _reply(client, channel, _help_text())
                return

            workspace = parts[1].strip().strip('"')
            _reply(client, channel, "Fetching data...")
            table = _get_cost_table_for_workspace(workspace)
            _reply(client, channel, f"Cost for {workspace} this month\n```\n{table}\n```")
            return

        if lower_text.startswith("monthly-summary "):
            parts = normalized.split(maxsplit=1)
            if len(parts) != 2:
                _reply(client, channel, _help_text())
                return

            year_month = parts[1].strip()
            _reply(client, channel, f"Generating monthly summary for {year_month}...")
            summary_csv, daily_csv = _generate_monthly_summary_csvs(year_month)
            _upload_monthly_csvs(client, channel, year_month, summary_csv, daily_csv)
            return

        _reply(client, channel, _help_text())

    except ValueError as e:
        _reply(client, channel, f"Error: {str(e)}")
    except Exception as e:
        logger.error(f"Error handling DM message: {e}")
        _reply(client, channel, f"An error occurred: {str(e)}")


def start_bot():
    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not app_token:
        raise ValueError("SLACK_APP_TOKEN environment variable is not set")

    scheduler = BackgroundScheduler(timezone=get_localzone())
    scheduler.add_job(_send_monthly_report_to_channel, CronTrigger(day=1, hour=9))
    scheduler.start()
    logger.info("Monthly report scheduler started (fires on the 1st of each month at 09:00 system time)")

    handler = SocketModeHandler(app, app_token)
    logger.info("Slack bot started and connected via Socket Mode")
    handler.start()
