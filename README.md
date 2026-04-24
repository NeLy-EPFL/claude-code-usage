# A Slack bot that reports Claude Code usage

This Slack bot exposes access to the Claude usage API to team members, which ordinarily requires an admin API key. The bot securely manages the admin key on the backend and allows team members to query their workspace's token usage through Slack commands.

## Features

- DM command: `help` - Show command help
- DM command: `list workspaces` - List all available workspaces and their IDs
- DM command: `usage <workspace_name or "all">` - Get a formatted ASCII table of daily total token usage for the current month
- DM command: `cost <workspace_name or "all">` - Get a formatted ASCII table of daily cost for the current month
- DM command: `usage-rawjson <workspace_name or "all"> <start_time> <end_time> <bucket_width>` - Get raw JSON response from Claude usage API with custom date ranges
- DM command: `cost-rawjson <workspace_name or "all"> <start_time> <end_time>` - Get raw JSON response from Claude cost API with custom date ranges

## Prerequisites

- Docker and Docker Compose installed on your system
- A Slack workspace where you have permission to create/manage apps
- An Anthropic API admin key with usage reporting permissions

## Setup

### 1. Create a Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Click "Create New App" → "From scratch"
3. Name your app (e.g., "Claude Usage Bot") and select your workspace
4. Go to "Socket Mode" in the left sidebar and enable it
5. Create an App Token with the `connections:write` scope - save this as `SLACK_APP_TOKEN`
6. Go to "OAuth & Permissions" and add these Bot Token Scopes:
   - `commands` (for slash commands)
   - `chat:write` (to send messages)
7. Install the app to your workspace and copy the Bot User OAuth Token - save this as `SLACK_BOT_TOKEN`

### 2. Configure DM Events

In your Slack App settings, configure the following:

1. App Home:
   - Go to "App Home"
   - Enable "Allow users to send Slash commands and messages from the messages tab" so users can DM the app

2. Event Subscriptions:
   - Go to "Event Subscriptions"
   - Turn events On
   - Set a Request URL placeholder, for example: `https://example.com/slack/events`
   - Under "Subscribe to bot events", add `message.im`

3. OAuth scopes:
   - Go to "OAuth & Permissions"
   - Bot Token Scopes should include:
     - `chat:write`
     - `files:write`

4. Reinstall app:
   - Reinstall to workspace after any scope or event changes

### 3. Configure Environment Variables

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and fill in your values:
   - `ANTHROPIC_ADMIN_API_KEY` - Your admin API key from [console.anthropic.com](https://console.anthropic.com)
   - `SLACK_BOT_TOKEN` - From OAuth & Permissions page
   - `SLACK_APP_TOKEN` - From Socket Mode page

### 4. Deploy with Docker Compose

The bot is designed to run as a persistent service via Docker Compose with automatic restart on failure.

```bash
docker-compose up -d
```

To view logs:
```bash
docker-compose logs -f claude-usage-bot
```

To stop:
```bash
docker-compose down
```

## Development

To run locally without Docker:

```bash
# Install dependencies
uv sync

# Set up environment
cp .env.example .env
# Edit .env with your values

# Run
uv run python main.py
```

## Usage Examples

In a DM with the app, you can now use:

```
# Show help
help

# List all available workspaces
list workspaces

# Get current month usage for a specific workspace
usage my-workspace-name

# Get usage for all workspaces
usage all

# Get current month cost for a specific workspace
cost my-workspace-name

# Get cost for all workspaces
cost all

# Get raw JSON usage data for a custom date range
usage-rawjson all 2024-01-01T00:00:00Z 2024-01-31T23:59:59Z 1d

# Get raw JSON cost data for a custom date range
cost-rawjson all 2024-01-01T00:00:00Z 2024-01-31T23:59:59Z
```

## API Reference

The bot uses the Anthropic API's usage and cost reporting endpoints:
- Base: `https://api.anthropic.com/v1/organizations/`
- Usage: `/usage_report/messages`
- Cost: `/cost_report`
- Workspaces: `/workspaces`

Supported bucket widths for usage: `1m` (minute), `1h` (hour), `1d` (day)
