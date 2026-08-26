from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any


class SmokeFailure(RuntimeError):
    pass


class ProductionClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cookie_jar)
        )

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        csrf_token: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode("utf-8")
        if csrf_token:
            headers["X-CSRF-Token"] = csrf_token
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                content = json.loads(response.read().decode("utf-8"))
                return content, dict(response.headers.items())
        except urllib.error.HTTPError as error:
            body_text = error.read().decode("utf-8", errors="replace")[:1000]
            raise SmokeFailure(
                f"{method} {path} returned HTTP {error.code}: {body_text}"
            ) from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise SmokeFailure(f"{method} {path} failed: {error}") from error


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def event(name: str, **values: Any) -> None:
    print(json.dumps({"event": name, **values}, ensure_ascii=False), flush=True)


def normalized_text(value: Any) -> str:
    return (
        str(value or "")
        .translate(str.maketrans({"ي": "ی", "ك": "ک"}))
        .replace("\u200c", " ")
        .lower()
    )


def floor_number(value: Any) -> int | None:
    text = normalized_text(value).translate(
        str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    )
    if text.strip() in {"همکف", "هم کف"}:
        return 0
    match = re.search(r"-?\d+", text)
    return int(match.group()) if match else None


def validate_listing_contract(listing: dict[str, Any], criteria: dict[str, Any]) -> None:
    token = str(listing.get("token") or "unknown")

    def check(condition: bool, message: str) -> None:
        require(condition, f"Listing {token} violates the profile contract: {message}")

    check(str(listing.get("url") or "").startswith("https://divar.ir/v/"), "invalid Divar URL")
    check(int(listing.get("image_count") or 0) >= 2, "fewer than two photos")

    text = normalized_text(f"{listing.get('title', '')} {listing.get('description', '')}")
    compact = re.sub(r"\s+", "", text)
    check("همخونه" not in compact and "همخانه" not in compact, "shared housing")
    check(
        not re.search(r"(?:پارکینگ|پارک|p)\s*(?:فقط\s*)?(?:برای\s*)?موتور", text),
        "motorcycle-only parking",
    )

    deposit = listing.get("deposit")
    rent = listing.get("rent")
    size = listing.get("size")
    rooms = listing.get("rooms")
    age = listing.get("age")
    check(deposit is not None and rent is not None, "unverifiable price")
    check(size is not None and rooms is not None, "unverifiable size or rooms")
    check(int(deposit) <= int(criteria["stretch_max_deposit"]), "deposit above hard limit")
    check(int(rent) <= int(criteria["stretch_max_rent"]), "rent above hard limit")
    check(int(size) >= int(criteria["min_size"]), "size below minimum")
    if criteria.get("max_size") is not None:
        check(int(size) <= int(criteria["max_size"]), "size above maximum")
    check(int(rooms) >= int(criteria["min_rooms"]), "rooms below minimum")
    if criteria.get("max_rooms") is not None:
        check(int(rooms) <= int(criteria["max_rooms"]), "rooms above maximum")
    if criteria.get("hard_max_age") is not None:
        check(age is not None, "unverifiable building age")
        check(int(age) <= int(criteria["hard_max_age"]), "building age above maximum")

    minimum_floor = criteria.get("min_floor")
    maximum_floor = criteria.get("max_floor")
    if minimum_floor is not None or maximum_floor is not None:
        floor = floor_number(listing.get("floor"))
        check(floor is not None, "unverifiable floor")
        if minimum_floor is not None:
            check(floor >= int(minimum_floor), "floor below minimum")
        if maximum_floor is not None:
            check(floor <= int(maximum_floor), "floor above maximum")

    for criterion, field, label in (
        ("max_building_floors", "building_floors", "building floors"),
        ("max_units_per_floor", "units_per_floor", "units per floor"),
    ):
        if criteria.get(criterion) is not None:
            check(listing.get(field) is not None, f"unverifiable {label}")
            check(
                int(listing[field]) <= int(criteria[criterion]),
                f"{label} above maximum",
            )

    for criterion, field, label in (
        ("parking", "has_parking", "parking"),
        ("elevator", "has_elevator", "elevator"),
        ("storage", "has_storage", "storage"),
        ("balcony", "has_balcony", "balcony"),
    ):
        if criteria.get(criterion) == "required":
            check(listing.get(field) is True, f"required {label} is not confirmed")

    if criteria.get("district_scope") == "selected":
        check(
            listing.get("district") in set(criteria.get("district_names") or []),
            "district is outside the selected neighborhoods",
        )


def run(args: argparse.Namespace) -> None:
    username = args.username or os.environ.get("KHANEYAB_SMOKE_USERNAME", "")
    password = args.password or os.environ.get("KHANEYAB_SMOKE_PASSWORD", "")
    require(bool(username and password), "Smoke username and password are required")
    client = ProductionClient(args.base_url, args.request_timeout)
    csrf_token = ""
    logged_in = False
    try:
        health, headers = client.request("/healthz")
        release = str(health.get("release") or headers.get("X-Khaneyab-Release") or "")
        require(health.get("ok") is True, "Health check did not return ok=true")
        if args.expected_release:
            require(
                release == args.expected_release,
                f"Expected release {args.expected_release}, production is {release}",
            )
        event("health", release=release)

        login, _ = client.request(
            "/api/auth/login",
            method="POST",
            payload={"username": username, "password": password},
        )
        require(login.get("ok") is True, "Login did not return ok=true")
        logged_in = True

        me, _ = client.request("/api/auth/me")
        csrf_token = str(me.get("csrf_token") or "")
        require(me.get("authenticated") is True, "Session is not authenticated")
        require(me.get("approved") is True, "Smoke account is not approved")
        require(me.get("profile_completed") is True, "Smoke profile is incomplete")
        require(bool(csrf_token), "Authenticated response has no CSRF token")
        event("auth", username=username, calibration_required=me.get("calibration_required"))

        profile_payload, _ = client.request("/api/profile")
        profile = profile_payload.get("profile") or {}
        criteria = profile.get("criteria") or {}
        require(profile.get("completed") is True, "Profile endpoint returned an incomplete profile")
        event(
            "profile",
            district_scope=criteria.get("district_scope"),
            district_count=len(criteria.get("district_ids") or []),
        )

        calibration_before, _ = client.request("/api/calibration")
        event(
            "calibration_before",
            ready=calibration_before.get("ready"),
            available=calibration_before.get("available", 0),
            completed=(calibration_before.get("calibration") or {}).get("completed"),
        )

        if not args.read_only:
            accepted, _ = client.request(
                "/api/scan",
                method="POST",
                payload={},
                csrf_token=csrf_token,
            )
            require(accepted.get("accepted") is True, "Scan request was not accepted")
            deadline = time.monotonic() + args.scan_timeout
            while True:
                status, _ = client.request("/api/scan/status")
                last_scan = status.get("last_scan") or {}
                event(
                    "scan_poll",
                    running=status.get("running"),
                    queued=status.get("queued"),
                    summaries=last_scan.get("summary_count", 0),
                    details=last_scan.get("detail_count", 0),
                    filtered=last_scan.get("filtered_count", 0),
                )
                require(not status.get("last_error"), f"Scan failed: {status.get('last_error')}")
                if not status.get("running"):
                    break
                require(time.monotonic() < deadline, "Scan exceeded the smoke-test timeout")
                time.sleep(args.poll_seconds)

            result = status.get("last_result") or {}
            require(int(result.get("summary_count") or 0) > 0, "Scan returned no summaries")
            require(
                int(result.get("filtered_count") or 0) < int(result.get("summary_count") or 0),
                "Every summary was filtered; production inventory is unusable",
            )
            event("scan_complete", **result)

        calibration_after, _ = client.request("/api/calibration")
        calibration_state = calibration_after.get("calibration") or {}
        calibration_usable = bool(
            calibration_state.get("complete")
            or (
                calibration_after.get("ready")
                and calibration_after.get("left")
                and calibration_after.get("right")
            )
        )
        require(calibration_usable, "Calibration has no usable comparison pair")
        if not calibration_state.get("complete"):
            remaining = int(calibration_state.get("remaining") or 0)
            available_pairs = int(calibration_after.get("available_pairs") or 0)
            require(
                available_pairs >= remaining,
                f"Calibration needs {remaining} pairs but only {available_pairs} remain",
            )
            left = calibration_after.get("left") or {}
            right = calibration_after.get("right") or {}
            require(left.get("token") != right.get("token"), "Calibration pair is not unique")
            validate_listing_contract(left, criteria)
            validate_listing_contract(right, criteria)
        event(
            "calibration_after",
            ready=calibration_after.get("ready"),
            complete=calibration_state.get("complete"),
            available=calibration_after.get("available", 0),
            available_pairs=calibration_after.get("available_pairs", 0),
        )

        if calibration_state.get("complete"):
            listings, _ = client.request("/api/listings")
            stats, _ = client.request("/api/stats")
            learning, _ = client.request("/api/learning")
            require(isinstance(listings, list), "Listings endpoint did not return a list")
            require(isinstance(stats, dict), "Stats endpoint did not return an object")
            require(isinstance(learning, dict), "Learning endpoint did not return an object")
            for listing in listings:
                validate_listing_contract(listing, criteria)
            event("dashboard", listings=len(listings), total=stats.get("total"))

        event("success", release=release)
    finally:
        if logged_in and csrf_token:
            try:
                client.request(
                    "/api/auth/logout",
                    method="POST",
                    payload={},
                    csrf_token=csrf_token,
                )
            except SmokeFailure:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run authenticated Khaneh-yab API smoke checks against production."
    )
    parser.add_argument(
        "--base-url",
        default="https://khaneh-yab.apps.teh2.abrhapaas.com",
    )
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--expected-release")
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--request-timeout", type=float, default=30)
    parser.add_argument("--scan-timeout", type=float, default=300)
    parser.add_argument("--poll-seconds", type=float, default=3)
    return parser


def main() -> int:
    try:
        run(build_parser().parse_args())
    except SmokeFailure as error:
        event("failure", error=str(error))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
