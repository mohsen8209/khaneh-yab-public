from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .config import AppConfig
from .db import Database
from .divar_client import DivarClient
from .models import Listing, ScoreResult
from .neighborhoods import NeighborhoodAtlas, default_atlas
from .notifier import NtfyNotifier
from .personalization import PersonalizationEngine
from .scoring import (
    SHARED_HOUSING_PATTERNS,
    _matches_any,
    listing_exclusion_reason,
    score_listing,
)


@dataclass
class ScanResult:
    summary_count: int
    detail_count: int
    scored_count: int
    alert_count: int
    expired_count: int
    filtered_count: int
    detail_errors: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary_count": self.summary_count,
            "detail_count": self.detail_count,
            "scored_count": self.scored_count,
            "alert_count": self.alert_count,
            "expired_count": self.expired_count,
            "filtered_count": self.filtered_count,
            "detail_errors": self.detail_errors,
        }


class ScoutService:
    def __init__(
        self,
        config: AppConfig,
        database: Database | None = None,
        client: DivarClient | None = None,
        atlas: NeighborhoodAtlas | None = None,
    ) -> None:
        self.config = config
        self.database = database or Database()
        self.client = client or DivarClient(config)
        self.atlas = atlas or default_atlas()
        self.notifier = NtfyNotifier(config)
        self.personalization = PersonalizationEngine(
            config, self.database, self.atlas
        )

    def run_once(
        self,
        *,
        detail_limit: int | None = None,
        source_urls: list[str] | None = None,
    ) -> ScanResult:
        scan_id = self.database.start_scan()
        was_empty = self.database.stats().get("total", 0) == 0
        summaries: list[Listing] = []
        detail_count = 0
        scored_count = 0
        alert_count = 0
        expired_count = 0
        filtered: dict[str, str] = {}
        errors: list[str] = []
        archive_safe = True
        try:
            if source_urls:
                merged_summaries: dict[str, Listing] = {}
                source_errors: list[str] = []
                for source_url in source_urls:
                    try:
                        source_summaries = self.client.fetch_summaries(source_url)
                        if not getattr(self.client, "last_fetch_complete", True):
                            archive_safe = False
                        for summary in source_summaries:
                            merged_summaries[summary.token] = summary
                    except Exception as error:
                        source_errors.append(f"source {source_url}: {error}")
                if not merged_summaries and source_errors:
                    raise RuntimeError("همه منابع جست‌وجو ناموفق بودند: " + " | ".join(source_errors))
                summaries = list(merged_summaries.values())
                errors.extend(source_errors)
                archive_safe = archive_safe and not source_errors
            else:
                summaries = self.client.fetch_summaries()
                archive_safe = getattr(self.client, "last_fetch_complete", True)
            needs_detail: list[Listing] = []
            newly_discovered: set[str] = set()
            baseline_is_silent = was_empty and not self.config.notifications.get(
                "notify_existing_on_first_run", False
            )
            for summary in summaries:
                is_new = not self.database.has_listing(summary.token)
                if is_new:
                    newly_discovered.add(summary.token)
                if self.database.detail_fetched(summary.token):
                    existing = self.database.get_listing(summary.token)
                    merged = merge_summary(existing, summary) if existing else summary
                    exclusion_reason = listing_exclusion_reason(merged)
                    if exclusion_reason:
                        filtered[summary.token] = exclusion_reason
                        continue
                    self.database.upsert_listing(
                        merged,
                        detail_fetched=True,
                        notification_eligible=not baseline_is_silent,
                    )
                else:
                    exclusion_reason = listing_exclusion_reason(summary)
                    if exclusion_reason:
                        filtered[summary.token] = exclusion_reason
                        continue
                    self.database.upsert_listing(
                        summary,
                        notification_eligible=not baseline_is_silent,
                    )
                    needs_detail.append(summary)

            if summaries and archive_safe:
                expired_count = self.database.archive_missing(
                    {summary.token for summary in summaries}
                )
            self.database.archive_filtered(filtered)

            limit = detail_limit if detail_limit is not None else int(
                self.config.scan["detail_limit_per_run"]
            )
            candidates = sorted(
                needs_detail,
                key=lambda listing: self._detail_priority(
                    listing, newly_discovered=listing.token in newly_discovered
                ),
                reverse=True,
            )[:limit]
            delay = float(self.config.scan["request_delay_seconds"])
            for index, summary in enumerate(candidates):
                if index:
                    time.sleep(delay)
                try:
                    detail = self.client.fetch_detail(summary)
                    self.database.upsert_listing(detail, detail_fetched=True)
                    detail_count += 1
                    exclusion_reason = listing_exclusion_reason(detail)
                    if exclusion_reason:
                        filtered[detail.token] = exclusion_reason
                        self.database.archive_filtered(
                            {detail.token: exclusion_reason}
                        )
                except Exception as error:  # A single withdrawn ad must not stop the scan.
                    errors.append(f"{summary.token}: {error}")

            records = self.database.list_records(limit=2000)
            scored: list[tuple[Listing, ScoreResult, bool]] = []
            for record in records:
                if not record.get("detail_fetched_at"):
                    continue
                clean = {
                    key: value
                    for key, value in record.items()
                    if key
                    not in {
                        "score", "first_seen_at", "last_seen_at", "detail_fetched_at",
                        "notified_at", "is_active", "expired_at", "feedback",
                        "filtered_at", "filter_reason", "feedback_reasons"
                    }
                }
                listing = Listing(**clean)
                comparable = self.database.comparable_costs(
                    listing.district or "",
                    listing.rooms,
                    listing.size,
                    float(self.config.profile["deposit_to_monthly_rate"]),
                    exclude_token=listing.token,
                )
                score = self._score_listing(
                    listing, comparable_costs=comparable
                )
                self.database.save_score(listing.token, score)
                scored.append(
                    (listing, score, record.get("feedback") == "rejected")
                )
                scored_count += 1

            for listing, score, personally_rejected in scored:
                if (
                    personally_rejected
                    or score.status != "alert"
                    or not self.database.is_notification_eligible(listing.token)
                    or self.database.was_notified(listing.token)
                ):
                    continue
                if self.notifier.send(listing, score):
                    self.database.mark_notified(listing.token)
                    alert_count += 1

            result = ScanResult(
                summary_count=len(summaries),
                detail_count=detail_count,
                scored_count=scored_count,
                alert_count=alert_count,
                expired_count=expired_count,
                filtered_count=len(filtered),
                detail_errors=errors,
            )
            self.database.finish_scan(
                scan_id,
                summary_count=result.summary_count,
                detail_count=result.detail_count,
                expired_count=result.expired_count,
                filtered_count=result.filtered_count,
                error="\n".join(errors) if errors else None,
            )
            return result
        except Exception as error:
            self.database.finish_scan(
                scan_id,
                summary_count=len(summaries),
                detail_count=detail_count,
                expired_count=expired_count,
                filtered_count=len(filtered),
                error=str(error),
            )
            raise

    def _score_listing(
        self,
        listing: Listing,
        *,
        comparable_costs: list[float] | None = None,
    ) -> ScoreResult:
        if comparable_costs is None:
            comparable_costs = self.database.comparable_costs(
                listing.district or "",
                listing.rooms,
                listing.size,
                float(self.config.profile["deposit_to_monthly_rate"]),
                exclude_token=listing.token,
            )
        score = score_listing(
            listing,
            self.config,
            self.atlas,
            duplicate_image_count=self.database.duplicate_image_count(listing.token),
            comparable_costs=comparable_costs,
        )
        return self.personalization.apply(listing, score)

    @staticmethod
    def _listing_from_record(record: dict[str, Any]) -> Listing:
        fields = Listing.__dataclass_fields__
        return Listing(
            **{key: value for key, value in record.items() if key in fields}
        )

    def watch(self) -> None:
        interval = int(self.config.scan["poll_interval_seconds"])
        while True:
            try:
                result = self.run_once()
                print(
                    "Scan complete: "
                    f"{result.summary_count} summaries, "
                    f"{result.detail_count} details, "
                    f"{result.filtered_count} filtered, "
                    f"{result.expired_count} expired, "
                    f"{result.alert_count} notifications",
                    flush=True,
                )
            except Exception as error:
                print(f"Scan failed; retrying after {interval}s: {error}", flush=True)
            time.sleep(interval)

    def _detail_priority(self, listing: Listing, *, newly_discovered: bool = False) -> float:
        title = listing.title.lower()
        if _matches_any(title, SHARED_HOUSING_PATTERNS):
            return -1000
        profile = self.config.profile
        score = 1000 if newly_discovered else 0
        score += min(listing.image_count, 10) * 2
        score += min(max((listing.size or 60) - 60, 0), 60) / 6
        if (listing.rent or 0) <= profile["ideal_rent"]:
            score += 8
        if (listing.deposit or 0) <= profile["ideal_deposit"]:
            score += 5
        if listing.is_stock_image:
            score -= 50
        if listing.image_count <= 1:
            score -= 20
        return score


def merge_summary(existing: Listing, summary: Listing) -> Listing:
    existing.title = summary.title or existing.title
    existing.url = summary.url or existing.url
    existing.deposit = summary.deposit if summary.deposit is not None else existing.deposit
    existing.rent = summary.rent if summary.rent is not None else existing.rent
    existing.size = summary.size if summary.size is not None else existing.size
    existing.rooms = summary.rooms if summary.rooms is not None else existing.rooms
    existing.age = summary.age if summary.age is not None else existing.age
    existing.latitude = summary.latitude or existing.latitude
    existing.longitude = summary.longitude or existing.longitude
    existing.approximate_location = summary.approximate_location
    existing.location_type = summary.location_type or existing.location_type
    existing.images = summary.images or existing.images
    existing.image_count = summary.image_count or existing.image_count
    existing.is_stock_image = summary.is_stock_image
    existing.advertiser = summary.advertiser or existing.advertiser
    existing.published_at = summary.published_at or existing.published_at
    existing.updated_at = summary.updated_at or existing.updated_at
    existing.raw_summary = summary.raw_summary
    return existing
