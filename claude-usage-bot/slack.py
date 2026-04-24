import os
import json
from datetime import datetime, timezone
import logging

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from claude import AnthropicUsageClient
import formatter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = App(token=os.environ.get("SLACK_BOT_TOKEN"))
usage_client = AnthropicUsageClient()

_TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _parse_utc(s: str) -> datetime:
    return datetime.strptime(s, _TIMESTAMP_FMT).replace(tzinfo=timezone.utc)


def _reply(client, channel: str, text: str) -> None:
    client.chat_postMessage(
        channel=channel,
        text=text,
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
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
        "    - 'start_time' and 'end_time' are in ISO format (e.g. '2026-04-20T00:00:00Z')\n\n"
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
    handler = SocketModeHandler(app, app_token)
    logger.info("Slack bot started and connected via Socket Mode")
    handler.start()
