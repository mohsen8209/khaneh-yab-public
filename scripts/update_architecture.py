from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
ARCHITECTURE_PATH = ROOT / "ARCHITECTURE.md"
MANIFEST_PATH = ROOT / ".architecture-manifest.json"

MODULE_DESCRIPTIONS = {
    "__init__.py": "مرز پکیج عمومی",
    "__main__.py": "ورودی اجرای `python -m divar_scout`",
    "auth.py": "حساب، scrypt با هم‌زمانی حافظه‌محدود، نشست، CSRF، تأیید و ممیزی",
    "cli.py": "فرمان‌های dashboard، scan، watch، admin-create و اعلان",
    "config.py": "بارگذاری و اعتبارسنجی تنظیمات مستقل عمومی",
    "dashboard.py": "Application service، HTTP routes، گیت دسترسی، API و سقف‌های حافظه",
    "db.py": "Schema، عملیات SQLite و ذخیره فشرده آگهی بدون پاسخ خام دیوار",
    "divar_client.py": "دریافت map/detail دیوار، تقسیم bbox و parsing شبکه",
    "models.py": "قراردادهای Listing و ScoreResult و payload فشرده ذخیره‌سازی",
    "neighborhoods.py": "بانک ثابت ۴۵۳ محله، normalization و lookup امتیاز",
    "notifier.py": "ارسال اعلان اختیاری ntfy",
    "personalization.py": "مدل pairwise محلی و انتخاب مقایسه آموزنده",
    "runtime.py": "ورودی production، Volume، ادمین bootstrap و اسکن پس‌زمینه PaaS",
    "scoring.py": "الک قطعی spam و زیرهمکف، شروط سخت و امتیاز پایه از ۲۰",
    "search_profile.py": "اعتبارسنجی پاسخ‌ها و ساخت جست‌وجوی دیوار برای پروفایل",
    "service.py": "هماهنگ‌کننده کم‌حافظه اسکن، retry، جزئیات، آرشیو، امتیاز و اعلان",
}

PAGE_DESCRIPTIONS = {
    "admin.html": "مدیریت و تأیید حساب‌ها (`admin.js`)",
    "auth.html": "ورود و ثبت‌نام (`auth.js`)",
    "calibration.html": "۱۲ مقایسه آغازین اجباری (`calibration.js`)",
    "index.html": "داشبورد نتایج و یادگیری ادامه‌دار (`app.js`)",
    "pending.html": "انتظار، رد یا تعلیق حساب (`pending.js`)",
    "setup.html": "ویزارد معیارها و انتخاب محله (`setup.js`)",
}

ROUTE_DESCRIPTIONS = {
    ("GET", "/"): ("داشبورد یا هدایت به مرحله لازم", "approved + profile + calibration"),
    ("GET", "/assets/*"): ("فایل‌های استاتیک همان پروژه", "public asset"),
    ("GET", "/healthz"): ("سلامت پردازش عمومی", "public"),
    ("GET", "/login"): ("صفحه ورود", "guest"),
    ("GET", "/register"): ("صفحه ثبت‌نام", "guest"),
    ("GET", "/pending"): ("وضعیت حساب تأییدنشده", "authenticated"),
    ("GET", "/admin"): ("پنل مدیریت", "admin"),
    ("GET", "/setup"): ("ویزارد پروفایل جست‌وجو", "approved user"),
    ("GET", "/calibration"): ("مرحله ۱۲ مقایسه", "approved + profile + required"),
    ("GET", "/api/auth/me"): ("وضعیت نشست و CSRF", "session optional"),
    ("GET", "/api/admin/users"): ("فهرست و آمار حساب‌ها", "admin"),
    ("GET", "/api/profile"): ("پروفایل و کاتالوگ محله", "approved"),
    ("GET", "/api/calibration"): ("زوج ثابت بعدی و پیشرفت", "profile required"),
    ("GET", "/api/listings"): ("نتایج شخصی‌شده", "calibration complete"),
    ("GET", "/api/scan/status"): ("وضعیت اسکن فعال، صف و پیشرفت واقعی", "profile required"),
    ("GET", "/api/stats"): ("آمار داشبورد", "calibration complete"),
    ("GET", "/api/config"): ("تنظیمات امن کاربر", "calibration complete"),
    ("GET", "/api/learning"): ("وضعیت موتور سلیقه", "calibration complete"),
    ("GET", "/api/comparison"): ("مقایسه ادامه‌دار داشبورد", "calibration complete"),
    ("POST", "/api/auth/register"): ("ساخت حساب pending", "guest"),
    ("POST", "/api/auth/login"): ("ساخت نشست", "guest"),
    ("POST", "/api/auth/logout"): ("ابطال نشست", "authenticated + CSRF"),
    ("POST", "/api/auth/impersonation/stop"): (
        "پایان مشاهده حساب کاربر و بازگشت به مدیر",
        "impersonated session + CSRF",
    ),
    ("POST", "/api/auth/password"): ("تغییر رمز و ابطال نشست", "authenticated + CSRF"),
    ("POST", "/api/admin/users/status"): ("تأیید، رد یا تعلیق", "admin + CSRF"),
    ("POST", "/api/admin/users/impersonate"): (
        "ورود موقت مدیر به حساب کاربر تأییدشده",
        "admin + CSRF",
    ),
    ("POST", "/api/profile"): ("ثبت پروفایل و شروع onboarding تازه", "approved + CSRF"),
    ("POST", "/api/calibration"): ("ثبت اتمیک یک انتخاب", "profile required + CSRF"),
    ("POST", "/api/scan"): ("اسکن متمرکز پروفایل کاربر", "profile required + CSRF"),
    ("POST", "/api/feedback"): ("ثبت بازخورد آگهی", "calibration complete + CSRF"),
    ("POST", "/api/comparison"): ("ثبت مقایسه ادامه‌دار", "calibration complete + CSRF"),
    ("POST", "/api/personalization/reset"): ("پاک‌کردن یادگیری همان کاربر", "calibration complete + CSRF"),
    ("POST", "/api/notify-test"): ("اعلان سراسری آزمایشی", "admin + CSRF"),
}

TABLE_DESCRIPTIONS = {
    "auth_attempts": "محدودسازی تلاش ورود",
    "auth_audit_log": "ممیزی تغییرات امنیتی",
    "comparisons": "مقایسه legacy سراسری",
    "feedback": "بازخورد legacy سراسری",
    "listing_images": "اثر انگشت عکس برای تشخیص تکرار",
    "listings": "موجودی مشترک آگهی و امتیاز پایه",
    "personalization_model": "مدل legacy سراسری",
    "scans": "تاریخچه و نتیجه اسکن‌ها",
    "sessions": "نشست‌های امن کاربران",
    "user_calibrations": "پیشرفت و زوج جاری onboarding",
    "user_comparisons": "مقایسه‌های خصوصی هر کاربر",
    "user_feedback": "بازخورد خصوصی هر کاربر",
    "user_personalization_models": "مدل خصوصی نسخه‌بندی‌شده",
    "user_profiles": "معیارها و محدوده جست‌وجوی کاربر",
    "users": "هویت، نقش و وضعیت تأیید",
}

EXTRA_ROUTES = {("GET", "/assets/*")}


def architecture_sources() -> list[Path]:
    paths: set[Path] = set()
    paths.update((ROOT / "divar_scout").glob("*.py"))
    paths.update((ROOT / "divar_scout" / "static").glob("*.html"))
    paths.update((ROOT / "divar_scout" / "static").glob("*.js"))
    paths.update((ROOT / "scripts").glob("*.py"))
    for relative in (
        "AGENTS.md",
        ".env.example",
        ".gitignore",
        "app.py",
        "chabok.json",
        "config.json",
        "DEPLOY_CHABOKAN.md",
        "DEPLOY_PARSPACK.md",
        "INSTANCE.md",
        "pyproject.toml",
        "requirements.txt",
        "start.sh",
        "start_dashboard.cmd",
        "scan_once.cmd",
        "watch.cmd",
        "data/tehran_neighborhoods.json",
        "data/divar_tehran_districts.json",
    ):
        path = ROOT / relative
        if path.is_file():
            paths.add(path)
    return sorted(paths, key=lambda path: path.relative_to(ROOT).as_posix())


def source_manifest() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        for path in architecture_sources()
    }


def manifest_fingerprint(manifest: dict[str, str]) -> str:
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _literal_paths(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value} if node.value.startswith("/") else set()
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        return {value for item in node.elts for value in _literal_paths(item)}
    return set()


def route_inventory() -> list[tuple[str, str]]:
    tree = ast.parse((ROOT / "divar_scout" / "dashboard.py").read_text(encoding="utf-8"))
    handler = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "DashboardHandler"
    )
    routes = set(EXTRA_ROUTES)
    for function_name, method in (("do_GET", "GET"), ("do_POST", "POST")):
        function = next(
            node for node in handler.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        for node in ast.walk(function):
            if not isinstance(node, ast.Compare) or not isinstance(node.left, ast.Name):
                continue
            if node.left.id != "path":
                continue
            for comparator in node.comparators:
                routes.update((method, path) for path in _literal_paths(comparator))
    return sorted(routes, key=lambda item: (item[1], item[0]))


def module_inventory() -> list[tuple[str, str, str]]:
    rows = []
    for path in sorted((ROOT / "divar_scout").glob("*.py"), key=lambda item: item.name):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        symbols = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
            and not node.name.startswith("_")
        ]
        rows.append((path.name, MODULE_DESCRIPTIONS.get(path.name, ""), ", ".join(symbols[:6])))
    return rows


def page_inventory() -> list[tuple[str, str]]:
    return [
        (path.name, PAGE_DESCRIPTIONS.get(path.name, ""))
        for path in sorted((ROOT / "divar_scout" / "static").glob("*.html"), key=lambda item: item.name)
    ]


def schema_inventory() -> list[dict[str, Any]]:
    from divar_scout.db import Database

    with tempfile.TemporaryDirectory() as directory:
        database = Database(Path(directory) / "architecture.sqlite3")
        connection = sqlite3.connect(database.path)
        connection.row_factory = sqlite3.Row
        try:
            names = [
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            tables = []
            for name in names:
                columns = [dict(row) for row in connection.execute(f'PRAGMA table_info("{name}")')]
                foreign_keys = [
                    dict(row) for row in connection.execute(f'PRAGMA foreign_key_list("{name}")')
                ]
                tables.append({"name": name, "columns": columns, "foreign_keys": foreign_keys})
            return tables
        finally:
            connection.close()


def validate_registries(
    modules: list[tuple[str, str, str]],
    pages: list[tuple[str, str]],
    routes: list[tuple[str, str]],
    tables: list[dict[str, Any]],
) -> None:
    missing_modules = [name for name, description, _symbols in modules if not description]
    missing_pages = [name for name, description in pages if not description]
    missing_routes = [f"{method} {path}" for method, path in routes if (method, path) not in ROUTE_DESCRIPTIONS]
    missing_tables = [table["name"] for table in tables if table["name"] not in TABLE_DESCRIPTIONS]
    missing = {
        "module descriptions": missing_modules,
        "page descriptions": missing_pages,
        "route descriptions": missing_routes,
        "table descriptions": missing_tables,
    }
    problems = [f"{label}: {', '.join(values)}" for label, values in missing.items() if values]
    if problems:
        raise RuntimeError("Architecture registry is incomplete; " + " | ".join(problems))


def _route_table(routes: list[tuple[str, str]]) -> str:
    lines = ["| روش | مسیر | مسئولیت | دسترسی |", "|---|---|---|---|"]
    for method, path in routes:
        purpose, access = ROUTE_DESCRIPTIONS[(method, path)]
        lines.append(f"| `{method}` | `{path}` | {purpose} | `{access}` |")
    return "\n".join(lines)


def _module_table(modules: list[tuple[str, str, str]]) -> str:
    lines = ["| ماژول | مسئولیت | ورودی‌های اصلی |", "|---|---|---|"]
    for name, description, symbols in modules:
        lines.append(f"| `{name}` | {description} | `{symbols or '-'}` |")
    return "\n".join(lines)


def _page_table(pages: list[tuple[str, str]]) -> str:
    lines = ["| صفحه | مسئولیت |", "|---|---|"]
    lines.extend(f"| `{name}` | {description} |" for name, description in pages)
    return "\n".join(lines)


def _schema_table(tables: list[dict[str, Any]]) -> str:
    lines = ["| جدول | مسئولیت | کلید | ستون‌ها | ارتباط‌ها |", "|---|---|---|---|---|"]
    for table in tables:
        columns = table["columns"]
        primary = ", ".join(column["name"] for column in columns if column["pk"]) or "-"
        column_names = ", ".join(column["name"] for column in columns)
        references = ", ".join(
            f"{foreign['from']}→{foreign['table']}.{foreign['to']}"
            for foreign in table["foreign_keys"]
        ) or "-"
        lines.append(
            f"| `{table['name']}` | {TABLE_DESCRIPTIONS[table['name']]} | "
            f"`{primary}` | `{column_names}` | `{references}` |"
        )
    return "\n".join(lines)


def _er_diagram(tables: list[dict[str, Any]]) -> str:
    relationships: dict[tuple[str, str], list[str]] = {}
    for table in tables:
        primary_columns = [
            column["name"]
            for column in sorted(table["columns"], key=lambda item: item["pk"])
            if column["pk"]
        ]
        for foreign in table["foreign_keys"]:
            key = (foreign["table"], table["name"])
            marker = "o|" if primary_columns == [foreign["from"]] else "o{"
            relationships.setdefault((key[0], key[1], marker), []).append(foreign["from"])
    lines = ["erDiagram"]
    for (parent, child, marker), columns in sorted(relationships.items()):
        lines.append(f'    {parent} ||--{marker} {child} : "{", ".join(sorted(columns))}"')
    return "\n".join(lines)


def render_document(manifest: dict[str, str] | None = None) -> str:
    manifest = manifest or source_manifest()
    modules = module_inventory()
    pages = page_inventory()
    routes = route_inventory()
    tables = schema_inventory()
    validate_registries(modules, pages, routes, tables)
    fingerprint = manifest_fingerprint(manifest)

    return f"""# معماری خانه‌یاب عمومی

> این سند خودکار از کد واقعی تولید شده است. منبع ویرایش `scripts/update_architecture.py` است.  
> اثرانگشت ورودی‌های معماری: `{fingerprint}`

## روش استفاده برای توسعه

1. پیش از تغییر، این سند و سپس فقط ماژول‌های مرتبط را بخوان.
2. `python scripts/update_architecture.py --check` فایل‌های معماری تغییرکرده را مشخص می‌کند.
3. پس از تغییر قرارداد یا جریان، Mermaidهای مرجع را در generator بازبینی کن.
4. `--write` را اجرا کن، خروجی را بخوان و بعد کل تست‌ها را اجرا کن.

## مرز سیستم

```mermaid
flowchart LR
    User[کاربر] --> HTTP[DashboardHandler :8766]
    Admin[مدیر] --> HTTP
    PaaS[PaaS start.sh to app.py + one instance] --> Runtime[Production Runtime]
    Runtime --> HTTP
    Runtime --> Worker[Background Scan Loop]
    Runtime --> Volume[(Persistent SQLite Volume)]
    HTTP --> Auth[AuthService]
    HTTP --> App[DashboardApp]
    App --> Profile[Search Profile]
    App --> Taste[Personalization Engine]
    App --> Scan[ScoutService]
    Scan --> Divar[DivarClient]
    Divar --> Remote[(divar.ir)]
    Scan --> Score[Spam Filter + Scoring]
    Score --> Atlas[(453-neighborhood Atlas)]
    Auth --> DB[(Public SQLite)]
    App --> DB
    Scan --> DB
    Worker --> App
    DB --> Volume
    Scan --> Notify[ntfy optional]
    DB --> Shared[Shared listing inventory]
    DB --> Private[Per-user private data]
```

- ریشه نوشتن فقط همین پروژه است؛ پروژه شخصی والد و پورت `8765` خارج از محدوده‌اند.
- اجرای محلی روی `127.0.0.1:8766` و اجرای PaaS روی `0.0.0.0:8766` است.
- production دقیقاً یک نمونه و یک SQLite روی Volume دائمی دارد؛ پارس‌پک از `/data` و چابکان از `/app/runtime-data` استفاده می‌کند. چند نمونه نیازمند PostgreSQL است.
- آگهی‌ها مشترک‌اند؛ پروفایل، feedback، comparison، calibration و model با `user_id` جدا می‌شوند.

## چرخه کاربر

```mermaid
stateDiagram-v2
    [*] --> Pending: ثبت‌نام
    Pending --> Setup: تأیید مدیر
    Pending --> Blocked: رد یا تعلیق
    Setup --> Calibration: اولین پروفایل کامل
    Calibration --> Calibration: انتخاب 1..11
    Calibration --> Dashboard: انتخاب 12
    Dashboard --> Dashboard: بازخورد و مقایسه ادامه‌دار
    Dashboard --> Setup: ویرایش معیارها
    Dashboard --> Blocked: تعلیق مدیر
```

- onboarding دقیقاً ۱۲ پاسخ و بدون skip است؛ زوج جاری پایدار و ثبت پاسخ اتمیک است.
- شروع اثر شخصی‌سازی در ۱۲ سیگنال و قدرت کامل در ۴۰ سیگنال است.
- کاربران کامل‌شده پیش از افزودن calibration، به‌علت نداشتن رکورد `user_calibrations` قفل نمی‌شوند.

## جریان اسکن و رتبه‌بندی

```mermaid
flowchart TD
    Requests[درخواست هم‌زمان کاربران] --> Queue[اشتراک فقط برای پروفایل همسان + صف مستقل پروفایل متفاوت]
    Queue --> Profiles[پروفایل همان کاربر و بودجه کامل جزئیات]
    Profiles --> URLs[قطعه‌های حداکثر 15 محله فعال دیوار]
    URLs --> Map[Map API + bbox splitting]
    Map --> Merge[ادغام و حذف token تکراری]
    Merge --> ResponseGate{{حداقل یک خلاصه غیر spam؟}}
    ResponseGate -->|خیر| Retry[یک retry کامل]
    Retry --> ResponseGate
    ResponseGate -->|دومین شکست| Preserve[خطای شفاف + حفظ موجودی قبلی]
    ResponseGate -->|بله| Early
    Early[الک قطعی خلاصه]
    Early --> Details[اولویت جزئیات تازه‌ها تا detail limit]
    Details --> HardSpam[الک قطعی جزئیات]
    HardSpam --> CalibrationGate{{ظرفیت 12 مقایسه آماده است؟}}
    CalibrationGate -->|خیر + پیشرفت واقعی| Details
    CalibrationGate -->|بله یا سقف 3 batch| Store
    Store[(listings مشترک)]
    Store --> Base[امتیاز پایه 20 = محله 8 + ارزش 5 + ملک 3 + اعتبار 3 + تازگی 1]
    Base --> UserScope[شروط سخت پروفایل در زمان درخواست]
    UserScope --> Personal[اصلاح تدریجی حداکثر ±2]
    Personal --> Dashboard[فیلتر، مرتب‌سازی و نمایش]
    Base --> NotifyGate[تازه + alert + trust/location gate]
    NotifyGate --> Ntfy[اعلان یک‌باره]
    Map --> Archive[آرشیو missing فقط در اسکن کامل و معتبر]
```

- spam و طبقات زیرهمکف پیش از «همه»، آمار، مقایسه و اعلان حذف می‌شوند؛ درخواست‌های دیوار نیز حداقل طبقه صفر دارند.
- درخواست‌های هم‌زمان رد نمی‌شوند؛ پروفایل همسان به اسکن جاری ملحق و هر پروفایل متفاوت به‌صورت یک job مستقل با سهم کامل جزئیات اجرا می‌شود.
- Map API دیوار بیش از ۱۵ محله یا دو شناسه ناسازگار شناخته‌شده را بی‌صدا نادیده می‌گیرد؛ محله‌های انتخابی در قطعه‌های امن کرال و سپس با token ادغام می‌شوند.
- پاسخ خالی، تماماً spam، ناقص یا دارای خطای یکی از قطعه‌های محله یک‌بار کامل retry می‌شود؛ تکرار آن موفقیت محسوب نمی‌شود و موجودی قبلی را تغییر نمی‌دهد.
- اسکن محدود کاربر هرگز آگهی خارج از محدوده خودش را منقضی نمی‌کند و شمار خلاصه/جزئیات حین اجرا در جدول `scans` به‌روز می‌شود.
- onboarding در صورت کمبود فایل معتبر، تا سه batch دارای پیشرفت را خودکار ادامه می‌دهد و کاربر را برای تکمیل ظرفیت ۱۲ مقایسه مجبور به اسکن دستی دوباره نمی‌کند.
- برای پروفایل کاربر، مقدار نامشخص در یک معیار سخت نیز خارج از قرارداد است؛ شخصی‌سازی نمی‌تواند رد سخت، بودجه نهایی، محله انتخابی یا الزامات امکانات را نجات دهد.
- آگهی منقضی غیرفعال می‌شود؛ تاریخچه برای یادگیری باقی می‌ماند.
- نسخه عمومی AI تصویری ندارد.

## جریان یادگیری

```mermaid
sequenceDiagram
    participant UI as Calibration/Dashboard UI
    participant API as DashboardApp
    participant DB as SQLite
    participant Model as Pairwise Logistic Regression
    UI->>API: درخواست زوج آموزنده
    API->>DB: active + scoped + unseen pairs
    DB-->>API: آگهی‌های معتبر
    API-->>UI: زوج ثابت جاری
    UI->>API: winner_token
    API->>DB: BEGIN IMMEDIATE + comparison + progress
    API->>Model: retrain(user_id)
    Model->>DB: versioned coefficients + signature
    Model-->>UI: maturity, preferences, remaining
```

## نقشه ماژول‌ها

{_module_table(modules)}

## صفحات اصلی

{_page_table(pages)}

## Routeهای واقعی

این جدول از AST متدهای `do_GET` و `do_POST` ساخته شده است؛ Route بدون توضیح تولید سند را متوقف می‌کند.

{_route_table(routes)}

## مدل داده واقعی

```mermaid
{_er_diagram(tables)}
```

{_schema_table(tables)}

## راهنمای اثر تغییر

| حوزه تغییر | نقطه شروع | تست‌های اصلی | کنترل تکمیلی |
|---|---|---|---|
| حساب و تأیید | `auth.py`, `dashboard.py`, `db.py` | `test_auth.py` | CSRF، ابطال نشست، route gate |
| معیار و محله | `search_profile.py`, `setup.*`, neighborhood data | `test_search_profile.py`, `test_scoring.py` | لیست و نقشه موبایل/دسکتاپ |
| crawler و انقضا | `divar_client.py`, `service.py`, `db.py`, `search_profile.py` | `test_parser.py`, `test_service.py`, `test_search_profile.py` | قطعه محله، قیمت detail، progress و archive محدود |
| spam و امتیاز | `scoring.py`, `neighborhoods.py` | `test_scoring.py`, `test_dashboard.py` | «همه»، آمار و comparison باید پاک باشند |
| سلیقه و onboarding | `personalization.py`, `dashboard.py`, `db.py`, `calibration.*` | `test_personalization.py`, `test_calibration.py` | ۱۲ انتخاب، replay، refresh و redirect |
| داشبورد و sort | `app.js`, `index.html`, `dashboard.py` | `test_dashboard.py` | Playwright در desktop/mobile |
| اعلان | `notifier.py`, `service.py`, `config.json` | `test_service.py` | فقط آگهی تازه و یک‌بار |
| PaaS و پایداری | `start.sh`, `app.py`, `runtime.py`, `config.py`, `db.py`, `scripts/smoke_production.py` | `test_runtime.py`, `test_backup_database.py`, `test_production_smoke.py` | یک نمونه، Volume دائمی، Secret، health و smoke واقعی قرارداد |
| معماری | `scripts/update_architecture.py`, `AGENTS.md` | `test_architecture.py` | `--check` باید سبز باشد |

## اجرای محلی و گیت نهایی

```powershell
python scripts/update_architecture.py --check
python -m unittest discover -s tests -v
python -m divar_scout dashboard
```

- عمومی: `http://127.0.0.1:8766`
- مدیریت: `http://127.0.0.1:8766/admin`
- شخصی، خارج از محدوده تغییر: `http://127.0.0.1:8765`
- قرارداد production چابکان: `DEPLOY_CHABOKAN.md`
- قرارداد production پارس‌پک: `DEPLOY_PARSPACK.md`
"""


def changed_sources(old: dict[str, str], current: dict[str, str]) -> list[str]:
    changed = []
    for path in sorted(set(old) | set(current)):
        if path not in old:
            changed.append(f"added: {path}")
        elif path not in current:
            changed.append(f"removed: {path}")
        elif old[path] != current[path]:
            changed.append(f"changed: {path}")
    return changed


def check_current() -> tuple[bool, str]:
    current = source_manifest()
    if not MANIFEST_PATH.is_file() or not ARCHITECTURE_PATH.is_file():
        return False, "Architecture artifacts are missing; run --write"
    try:
        stored = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False, "Architecture manifest is invalid; run --write"
    changes = changed_sources(stored, current)
    expected = render_document(current)
    document_changed = ARCHITECTURE_PATH.read_text(encoding="utf-8") != expected
    if changes or document_changed:
        details = "; ".join(changes) if changes else "generated document differs"
        return False, f"Architecture is stale ({details}); review flows and run --write"
    return True, f"Architecture is current ({manifest_fingerprint(current)})"


def write_artifacts() -> None:
    manifest = source_manifest()
    ARCHITECTURE_PATH.write_text(
        render_document(manifest), encoding="utf-8", newline="\n"
    )
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and verify public architecture map")
    parser.add_argument("--write", action="store_true", help="regenerate architecture artifacts")
    parser.add_argument("--check", action="store_true", help="verify architecture freshness")
    args = parser.parse_args()
    if args.write and args.check:
        parser.error("choose only one of --write or --check")
    if args.write:
        write_artifacts()
        print(f"Architecture updated: {ARCHITECTURE_PATH}")
        return 0
    ok, message = check_current()
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
