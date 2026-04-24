import os
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests


class AnthropicUsageClient:
    USAGE_ENDPOINT = "https://api.anthropic.com/v1/organizations/usage_report/messages"
    COST_ENDPOINT = "https://api.anthropic.com/v1/organizations/cost_report"
    WORKSPACES_ENDPOINT = "https://api.anthropic.com/v1/organizations/workspaces"
    TIMESTAMP_FORMAT = r"%Y-%m-%dT%H:%M:%SZ"
    ALLOWED_BUCKET_WIDTHS = ["1m", "1h", "1d"]

    def __init__(self):
        self.headers = {
            "anthropic-version": "2023-06-01",
            "x-api-key": os.environ.get("ANTHROPIC_ADMIN_API_KEY"),
        }
        self.cached_workspace_ids = {}

    def _paginated_get(self, endpoint: str, base_params: dict) -> dict:
        merged_data = []
        next_page = None
        while True:
            params = dict(base_params)
            if next_page:
                params["page"] = next_page
            # Append group_by[] literally — requests would percent-encode [] to %5B%5D.
            qs = urllib.parse.urlencode(params) + "&group_by[]=workspace_id"
            response = requests.get(f"{endpoint}?{qs}", headers=self.headers)
            response.raise_for_status()
            body = response.json()
            merged_data.extend(body.get("data", []))
            next_page = body.get("next_page")
            if not body.get("has_more") or not next_page:
                return {"data": merged_data, "has_more": False, "next_page": None}

    def query_usage(
        self,
        workspace_name: str,
        start_time: datetime,
        end_time: datetime,
        bucket_width: str,
    ) -> dict:
        if bucket_width not in self.ALLOWED_BUCKET_WIDTHS:
            raise ValueError(
                f"Invalid bucket width {bucket_width}. Choose from {self.ALLOWED_BUCKET_WIDTHS}."
            )
        if workspace_name.lower() != "all":
            self.workspace_name_to_id(workspace_name)

        base_params = {
            "starting_at": start_time.astimezone(timezone.utc).strftime(self.TIMESTAMP_FORMAT),
            "ending_at": end_time.astimezone(timezone.utc).strftime(self.TIMESTAMP_FORMAT),
            "bucket_width": bucket_width,
        }
        return self._paginated_get(self.USAGE_ENDPOINT, base_params)

    def filter_data_for_workspace(self, data: dict, workspace_name_or_all: str) -> dict:
        if workspace_name_or_all.lower() == "all":
            return data

        workspace_id = self.workspace_name_to_id(workspace_name_or_all)
        filtered_buckets = [
            {
                "starting_at": bucket.get("starting_at"),
                "ending_at": bucket.get("ending_at"),
                "results": [
                    row for row in bucket.get("results", [])
                    if row.get("workspace_id") == workspace_id
                ],
            }
            for bucket in data.get("data", [])
        ]
        return {
            "data": filtered_buckets,
            "has_more": data.get("has_more", False),
            "next_page": data.get("next_page"),
        }

    def query_usage_current_month(self, workspace_name_or_all: str) -> dict:
        now = datetime.now(timezone.utc)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_time = today.replace(day=1)

        current_hour = now.replace(minute=0, second=0, microsecond=0)

        merged_data = []
        if start_time < today:
            history = self.query_usage(workspace_name_or_all, start_time, today, bucket_width="1d")
            merged_data = history["data"]

        # Completed hours of today in one call, then the current in-progress hour in minute
        # granularity so the current hour's usage is always reflected.
        today_buckets = []
        if today < current_hour:
            hours = self.query_usage(workspace_name_or_all, today, current_hour, bucket_width="1h")
            today_buckets = hours["data"]
        current_hour_data = self.query_usage(workspace_name_or_all, current_hour, now, bucket_width="1m")
        today_buckets.extend(current_hour_data["data"])
        merged_data.append(self._sum_buckets_into_day(today_buckets, today))

        return {"data": merged_data, "has_more": False, "next_page": None}

    def _sum_buckets_into_day(self, buckets: list, day_start: datetime) -> dict:
        workspace_totals: dict = {}
        for bucket in buckets:
            for row in bucket.get("results", []):
                ws_id = row.get("workspace_id")
                if ws_id not in workspace_totals:
                    workspace_totals[ws_id] = {
                        "workspace_id": ws_id,
                        "uncached_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "cache_creation": {
                            "ephemeral_1h_input_tokens": 0,
                            "ephemeral_5m_input_tokens": 0,
                        },
                        "output_tokens": 0,
                    }
                t = workspace_totals[ws_id]
                t["uncached_input_tokens"] += int(row.get("uncached_input_tokens", 0))
                t["cache_read_input_tokens"] += int(row.get("cache_read_input_tokens", 0))
                t["output_tokens"] += int(row.get("output_tokens", 0))
                cc = row.get("cache_creation", {})
                t["cache_creation"]["ephemeral_1h_input_tokens"] += int(cc.get("ephemeral_1h_input_tokens", 0))
                t["cache_creation"]["ephemeral_5m_input_tokens"] += int(cc.get("ephemeral_5m_input_tokens", 0))
        day_end = day_start + timedelta(days=1)
        return {
            "starting_at": day_start.strftime(self.TIMESTAMP_FORMAT),
            "ending_at": day_end.strftime(self.TIMESTAMP_FORMAT),
            "results": list(workspace_totals.values()),
        }

    def query_cost(
        self,
        workspace_name: str,
        start_time: datetime,
        end_time: datetime,
        limit: int = 31,
    ) -> dict:
        if workspace_name.lower() != "all":
            self.workspace_name_to_id(workspace_name)

        base_params = {
            "starting_at": start_time.astimezone(timezone.utc).strftime(self.TIMESTAMP_FORMAT),
            "ending_at": end_time.astimezone(timezone.utc).strftime(self.TIMESTAMP_FORMAT),
            "bucket_width": "1d",
            "limit": limit,
        }
        return self._paginated_get(self.COST_ENDPOINT, base_params)

    def query_cost_current_month(self, workspace_name_or_all: str) -> dict:
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        start_time = today.replace(day=1)
        next_month = (start_time.replace(day=28) + timedelta(days=4)).replace(day=1)
        return self.query_cost(workspace_name_or_all, start_time, next_month)

    def workspace_name_to_id(self, workspace_name: str) -> str:
        if workspace_name not in self.cached_workspace_ids:
            self.refresh_workspace_cache()
        workspace_id = self.cached_workspace_ids.get(workspace_name)
        if not workspace_id:
            raise ValueError(f"Workspace name {workspace_name} not found.")
        return workspace_id

    def refresh_workspace_cache(self):
        response = requests.get(
            self.WORKSPACES_ENDPOINT, {"limit": 100}, headers=self.headers
        )
        response.raise_for_status()
        response_json = response.json()
        if response_json["has_more"]:
            raise NotImplementedError(
                "More than 100 workplaces found. Pagination not implemented yet."
            )
        self.cached_workspace_ids = {
            e["name"]: e["id"] for e in response_json["data"] if e["type"] == "workspace"
        }

    def get_all_workspaces(self) -> dict:
        if not self.cached_workspace_ids:
            self.refresh_workspace_cache()
        return self.cached_workspace_ids
