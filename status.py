"""Runtime status tracking for the getGameInfo plugin view."""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_EVENTS = 200
MAX_DELIVERIES = 100
COUNTER_RETENTION_DAYS = 7
_COUNTER_KEYS = ("checks", "new_games", "pushes", "failures", "downgraded")


class CheckBusyError(RuntimeError):
    """Raised when a check is requested while another one is running."""


def now_local() -> datetime:
    return datetime.now().astimezone()


class RuntimeState:
    """In-memory status with a small JSON snapshot for restarts.

    Only the plugin's asyncio loop touches this object, so no locking is
    required. File writes are tiny and happen after a check finishes.
    """

    def __init__(self, max_events: int = MAX_EVENTS) -> None:
        self.started_at = now_local()
        self.next_check_at: str | None = None
        self.last_check: dict[str, Any] | None = None
        self.checking = False
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._deliveries: dict[str, dict[str, Any]] = {}
        self._counters: dict[str, dict[str, int]] = {}
        self._seq = 0
        self._storage_path: Path | None = None

    # ---- persistence ----

    def attach_storage(self, path: Path | None) -> None:
        self._storage_path = path

    def load(self) -> None:
        if self._storage_path is None or not self._storage_path.is_file():
            return
        try:
            payload = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("读取插件状态文件失败: %s", exc)
            return
        if not isinstance(payload, dict):
            return
        last_check = payload.get("last_check")
        self.last_check = last_check if isinstance(last_check, dict) else None
        counters = payload.get("counters")
        if isinstance(counters, dict):
            self._counters = {
                str(day): {
                    key: int(value)
                    for key, value in bucket.items()
                    if key in _COUNTER_KEYS and isinstance(value, int)
                }
                for day, bucket in counters.items()
                if isinstance(bucket, dict)
            }
            self._prune_counters()
        deliveries = payload.get("deliveries")
        if isinstance(deliveries, dict):
            self._deliveries = {
                str(key): value
                for key, value in deliveries.items()
                if isinstance(value, dict)
            }
        events = payload.get("events")
        if isinstance(events, list):
            self._events.clear()
            for item in events[-self._events.maxlen :]:
                if not isinstance(item, dict):
                    continue
                if not {"ts", "level", "kind", "message"} <= item.keys():
                    continue
                self._events.append(item)
                try:
                    self._seq = max(self._seq, int(item.get("id", 0)))
                except (TypeError, ValueError):
                    pass

    def save(self) -> None:
        path = self._storage_path
        if path is None:
            return
        try:
            if not path.parent.is_dir():
                # Do not create a data dir for an empty status; once there is
                # something to show, persisting it is worth creating the dir.
                if not (self.last_check or self._events or self._deliveries):
                    return
                path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(path.name + ".tmp")
            temp.write_text(
                json.dumps(
                    {
                        "last_check": self.last_check,
                        "counters": self._counters,
                        "deliveries": self._deliveries,
                        "events": list(self._events),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            temp.replace(path)
        except OSError as exc:
            logger.warning("保存插件状态文件失败: %s", exc)

    # ---- counters ----

    def _today_bucket(self) -> dict[str, int]:
        key = now_local().date().isoformat()
        bucket = self._counters.get(key)
        if bucket is None:
            bucket = {name: 0 for name in _COUNTER_KEYS}
            self._counters[key] = bucket
            self._prune_counters()
        return bucket

    def _prune_counters(self) -> None:
        cutoff = (now_local() - timedelta(days=COUNTER_RETENTION_DAYS)).date().isoformat()
        for day in [key for key in self._counters if key < cutoff]:
            self._counters.pop(day, None)

    def today_counts(self) -> dict[str, int]:
        bucket = self._today_bucket()
        return {name: bucket.get(name, 0) for name in _COUNTER_KEYS}

    # ---- events ----

    def add_event(
        self,
        level: str,
        kind: str,
        message: str,
        **fields: Any,
    ) -> dict[str, Any]:
        self._seq += 1
        event: dict[str, Any] = {
            "id": self._seq,
            "ts": now_local().isoformat(timespec="seconds"),
            "level": level,
            "kind": kind,
            "message": message,
        }
        for key, value in fields.items():
            if value is not None:
                event[key] = value
        self._events.append(event)
        return event

    def events(
        self,
        *,
        limit: int = 100,
        category: str = "all",
        level: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        items = list(self._events)
        if category == "check":
            items = [item for item in items if str(item["kind"]).startswith("check_")]
        elif category == "push":
            items = [item for item in items if str(item["kind"]).startswith("push_")]
        elif category == "error":
            items = [item for item in items if item["level"] == "error"]
        if level in ("info", "warning", "error"):
            items = [item for item in items if item["level"] == level]
        if query:
            needle = query.strip().lower()
            if needle:
                items = [
                    item
                    for item in items
                    if needle in str(item.get("message", "")).lower()
                ]
        items.reverse()
        return items[: max(1, min(limit, MAX_EVENTS))]

    # ---- checks ----

    def start_check(self, *, trigger: str, dry_run: bool) -> None:
        self.checking = True
        self.add_event(
            "info",
            "check_start",
            "开始仅检查新游戏" if dry_run else "开始检查新游戏",
            trigger=trigger,
            dry_run=dry_run,
        )

    def finish_check(
        self,
        *,
        ok: bool,
        trigger: str,
        dry_run: bool,
        duration_ms: int,
        games_seen: int = 0,
        new_games: int = 0,
        pushes: int = 0,
        failures: int = 0,
        downgraded: int = 0,
        baseline_missing: bool = False,
        skipped: int = 0,
        error: str | None = None,
    ) -> dict[str, Any]:
        self.checking = False
        finished = now_local().isoformat(timespec="seconds")
        record: dict[str, Any] = {
            "trigger": trigger,
            "dry_run": dry_run,
            "started_at": None,
            "finished_at": finished,
            "duration_ms": duration_ms,
            "ok": ok,
            "games_seen": games_seen,
            "new_games": new_games,
            "pushes": pushes,
            "failures": failures,
            "downgraded": downgraded,
            "skipped": skipped,
            "baseline_missing": baseline_missing,
            "error": error,
        }
        # Keep the start time of the most recent check event.
        for event in reversed(self._events):
            if event["kind"] == "check_start":
                record["started_at"] = event["ts"]
                break
        self.last_check = record

        bucket = self._today_bucket()
        bucket["checks"] += 1
        bucket["new_games"] += new_games
        if not dry_run:
            bucket["pushes"] += pushes
            bucket["failures"] += failures
            bucket["downgraded"] += downgraded

        if ok:
            if dry_run:
                message = f"仅检查完成：{games_seen} 款，新增 {new_games} 款"
            else:
                message = (
                    f"检查完成：新增 {new_games} 款，推送 {pushes} 次，"
                    f"失败 {failures} 次"
                )
            self.add_event("info", "check_ok", message, **record)
        else:
            self.add_event(
                "error",
                "check_error",
                f"检查失败：{error or '未知错误'}",
                **record,
            )
        self.save()
        return record

    # ---- deliveries ----

    def record_delivery(
        self,
        platform_id: str | None,
        group_id: str,
        *,
        ok: bool,
        fallback: bool = False,
        error: str | None = None,
    ) -> None:
        key = f"{platform_id or ''}:{group_id}"
        self._deliveries[key] = {
            "at": now_local().isoformat(timespec="seconds"),
            "ok": ok,
            "fallback": fallback,
            "error": error,
        }
        while len(self._deliveries) > MAX_DELIVERIES:
            self._deliveries.pop(next(iter(self._deliveries)))

    def get_delivery(
        self, platform_id: str | None, group_id: str
    ) -> dict[str, Any] | None:
        return self._deliveries.get(f"{platform_id or ''}:{group_id}")

    # ---- snapshot ----

    def snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or now_local()
        return {
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "uptime_seconds": max(0, int((now - self.started_at).total_seconds())),
            "checking": self.checking,
            "next_check_at": self.next_check_at,
            "last_check": self.last_check,
            "today": self.today_counts(),
        }

