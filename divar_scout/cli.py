from __future__ import annotations

import argparse
import getpass
import json
import sys

from .auth import AuthService
from .config import load_config
from .dashboard import serve
from .db import Database
from .models import Listing, ScoreResult
from .notifier import NtfyNotifier
from .service import ScoutService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="khaneh-yab")
    parser.add_argument("--config", help="Path to config.json")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Run one controlled scan")
    scan.add_argument("--detail-limit", type=int, default=None)
    subparsers.add_parser("watch", help="Keep scanning at the configured interval")
    subparsers.add_parser("dashboard", help="Start the local dashboard")
    subparsers.add_parser("topic", help="Print the ntfy subscription URL")
    subparsers.add_parser("notify-test", help="Send one ntfy test notification")
    admin = subparsers.add_parser(
        "admin-create", help="Create the first approved root administrator"
    )
    admin.add_argument("--username", default="root")
    admin.add_argument("--display-name", default="مدیر اصلی")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    if args.command == "scan":
        result = ScoutService(config).run_once(detail_limit=args.detail_limit)
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    elif args.command == "watch":
        ScoutService(config).watch()
    elif args.command == "dashboard":
        serve(config)
    elif args.command == "topic":
        settings = config.notifications
        print(settings["server"].rstrip("/") + "/" + settings["topic"])
    elif args.command == "notify-test":
        listing = Listing(
            token="notification-test",
            title="اعلان آزمایشی خانه‌یاب",
            url="https://divar.ir",
            district="تهران",
            deposit=300_000_000,
            rent=30_000_000,
        )
        score = ScoreResult(
            total=18.0, location=7.0, value=4.0, property=2.5,
            trust_points=3.0, freshness=1.0, trust=100,
            location_confidence=100, status="alert",
            reasons=["ارتباط اعلان گوشی برقرار است"], risks=[]
        )
        NtfyNotifier(config).send(listing, score, force=True)
        print("Notification sent")
    elif args.command == "admin-create":
        password = getpass.getpass("Admin password: ")
        confirmation = getpass.getpass("Repeat password: ")
        if password != confirmation:
            raise ValueError("Password confirmation does not match")
        user = AuthService(Database()).create_admin(
            args.username, args.display_name, password
        )
        print(json.dumps(user.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
