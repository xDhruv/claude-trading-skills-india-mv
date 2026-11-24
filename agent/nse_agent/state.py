"""JSON state file: what was sent today, tracked ideas, seen news, cached history. Written atomically."""
import json
import os
import pathlib
import tempfile
from datetime import datetime


def _blank(day: str) -> dict:
    return {"date": day, "emails": 0, "ideas": {}, "alerts": [], "done": [], "last": {}, "cache": {},
            "context": {}, "thread": None}


class State:
    def __init__(self, directory: str):
        self.dir = pathlib.Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "state.json"
        self.data = _blank("")
        self.seen_news: list[str] = []
        if self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self.seen_news = raw.pop("seen_news", [])
                self.data = raw
            except (json.JSONDecodeError, OSError):
                pass

    def rollover(self, day: str) -> None:
        if self.data.get("date") != day:
            self.data = _blank(day)

    def save(self) -> None:
        payload = dict(self.data, seen_news=self.seen_news[-800:])
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, self.path)

    def due(self, key: str, now: datetime, every_min: float) -> bool:
        last = self.data["last"].get(key)
        return last is None or (now - datetime.fromisoformat(last)).total_seconds() >= every_min * 60

    def mark(self, key: str, now: datetime) -> None:
        self.data["last"][key] = now.isoformat()

    def once(self, key: str) -> bool:
        """True the first time `key` is seen today (and records it)."""
        if key in self.data["done"]:
            return False
        self.data["done"].append(key)
        return True
