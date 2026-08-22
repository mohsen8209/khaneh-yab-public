from __future__ import annotations

import json
from urllib.request import Request, urlopen

from .config import AppConfig
from .models import Listing, ScoreResult


class NtfyNotifier:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return bool(self.config.notifications.get("enabled"))

    def send(self, listing: Listing, score: ScoreResult, *, force: bool = False) -> bool:
        if not self.enabled and not force:
            return False
        settings = self.config.notifications
        reasons = "، ".join(score.reasons[:3]) or "جزئیات در داشبورد"
        district = listing.district or "محله نامشخص"
        payload = {
            "topic": settings["topic"],
            "title": f"کیس {score.total:.1f} از ۲۰ در {district}",
            "message": (
                f"{listing.title}\n"
                f"ودیعه {format_money(listing.deposit)} | اجاره {format_money(listing.rent)}\n"
                f"{reasons}"
            ),
            "priority": 5 if score.total >= 18 else 4,
            "click": listing.url,
            "tags": ["house"],
        }
        headers = {"Content-Type": "application/json", "User-Agent": "KhanehYab/0.1"}
        if settings.get("no_cache", True):
            headers["Cache"] = "no"
        request = Request(
            settings["server"].rstrip("/"),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=15) as response:
            response.read()
        return True


def format_money(value: int | None) -> str:
    if value is None:
        return "نامشخص"
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:g} میلیارد"
    return f"{value / 1_000_000:g} میلیون"

