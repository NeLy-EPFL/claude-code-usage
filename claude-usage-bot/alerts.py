import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class AlertManager:
    """Persist per-user alert subscriptions and track which thresholds have been notified."""

    def __init__(self, filepath: str | None = None):
        if filepath is None:
            filepath = os.environ.get("ALERTS_FILE", "./data/alerts.json")
        self._path = Path(filepath)
        self._lock = threading.Lock()
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                with open(self._path) as f:
                    self._data = json.load(f)
            except Exception as e:
                logger.error("Failed to load alerts file %s: %s", self._path, e)
                self._data = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=2)

    def subscribe(self, user_id: str, workspace_name: str) -> None:
        with self._lock:
            existing = self._data.get(user_id, {})
            self._data[user_id] = {
                "workspace_name": workspace_name,
                "enabled": True,
                # Preserve previously recorded thresholds if the user re-subscribes.
                "notified": existing.get("notified", {}),
            }
            self._save()

    def unsubscribe(self, user_id: str) -> bool:
        """Disable alerts for a user. Returns False if the user wasn't subscribed."""
        with self._lock:
            if user_id not in self._data or not self._data[user_id].get("enabled"):
                return False
            self._data[user_id]["enabled"] = False
            self._save()
            return True

    def get_subscription(self, user_id: str) -> dict | None:
        """Return the active subscription for user_id, or None if not subscribed."""
        with self._lock:
            sub = self._data.get(user_id)
            if sub and sub.get("enabled"):
                return dict(sub)
            return None

    def get_all_subscriptions(self) -> dict[str, dict]:
        with self._lock:
            return {
                uid: dict(sub)
                for uid, sub in self._data.items()
                if sub.get("enabled")
            }

    def get_notified_thresholds(self, user_id: str, year_month: str) -> list[int]:
        with self._lock:
            sub = self._data.get(user_id, {})
            return list(sub.get("notified", {}).get(year_month, []))

    def mark_notified(self, user_id: str, year_month: str, threshold: int) -> None:
        with self._lock:
            if user_id not in self._data:
                return
            notified = self._data[user_id].setdefault("notified", {})
            month_list = notified.setdefault(year_month, [])
            if threshold not in month_list:
                month_list.append(threshold)
            self._save()
