# استقرار خانه‌یاب عمومی روی PaaS پارس‌پک

این پروژه برای اجرای مستقیم با Python PaaS آماده است. معماری production عمداً یک Pod،
یک پردازش Python و یک SQLite روی Volume دائمی دارد. داشبورد و اسکن زمان‌بندی‌شده در
همان پردازش اجرا می‌شوند و از یک قفل اسکن مشترک استفاده می‌کنند.

## قرارداد قطعی سرویس

- Runtime: Python 3.11 یا جدیدتر
- Entry point: `app.py`
- Context directory: `.`
- Application port: `8766`
- Health check: `/healthz`
- Replica/Pod count: دقیقاً `1`
- Persistent volume mount: `/data`
- Database: `/data/divar_scout.sqlite3`

SQLite برای این نسخه به سرویس دیتابیس جدا نیاز ندارد. افزایش Pod به بیشتر از یک، بدون
مهاجرت به PostgreSQL مجاز نیست.

## متغیرهای محیطی

این مقادیر را به‌صورت ENV معمولی تعریف کنید:

| نام | مقدار |
|---|---|
| `KHANEYAB_RUNTIME` | `paas` |
| `KHANEYAB_HOST` | `0.0.0.0` |
| `KHANEYAB_PORT` | `8766` |
| `KHANEYAB_DATA_DIR` | `/data` |
| `KHANEYAB_WATCH_ENABLED` | `true` |
| `KHANEYAB_ADMIN_USERNAME` | نام کاربری لاتین مدیر |
| `KHANEYAB_ADMIN_DISPLAY_NAME` | نام نمایشی مدیر |

`KHANEYAB_ADMIN_PASSWORD` را فقط به‌صورت Secret و با رمز حداقل ۱۰ نویسه شامل حرف و
عدد تعریف کنید. در اولین اجرا و تنها در صورت نبودن مدیر ساخته می‌شود. پس از ورود موفق
مدیر، Secret بوت‌استرپ را از محیط حذف کنید؛ ادمین داخل دیتابیس دائمی باقی می‌ماند.

برنامه در حالت PaaS بدون مسیر صریح دیتابیس دائمی عمداً اجرا نمی‌شود تا اطلاعات کاربران
روی فایل‌سیستم موقت کانتینر قرار نگیرد.

## ساخت اولیه در پنلی که Volume را بعد از ایجاد نشان می‌دهد

اگر اپلیکیشن هنوز ساخته نشده و بخش مدیریت Volume در دسترس نیست، اولین بوت را فقط برای
بازشدن منوهای مدیریتی با دیتابیس موقت و اسکن خاموش انجام دهید. در فرم ساخت اولیه و بخش
متغیرهای زمان اجرا این دو مقدار را وارد کنید:

| نام | مقدار موقت |
|---|---|
| `KHANEYAB_DATA_DIR` | `/tmp/khane-yab-bootstrap` |
| `KHANEYAB_WATCH_ENABLED` | `false` |

در این مرحله `KHANEYAB_ADMIN_PASSWORD` تعریف نکنید و هیچ کاربر واقعی نسازید. پس از
موفق‌شدن ساخت اپلیکیشن:

1. از «ویرایش پیکربندی»، یک Volume با mount path دقیق `/data` اضافه کنید.
2. `KHANEYAB_DATA_DIR` را از مقدار موقت به `/data` تغییر دهید.
3. `KHANEYAB_WATCH_ENABLED` را به `true` تغییر دهید.
4. Secret مربوط به `KHANEYAB_ADMIN_PASSWORD` را اضافه کنید.
5. اپلیکیشن را restart کنید و سلامت `/healthz` و لاگ ساخت ادمین را بررسی کنید.

دیتابیس `/tmp` عمداً دورریختنی است. مسیر دائمی تنها `/data/divar_scout.sqlite3` است.

## دیتابیس تازه یا انتقال دیتابیس فعلی

برای شروع تازه، Volume خالی کافی است و Schema خودکار ساخته می‌شود.

برای انتقال امن کاربران، یادگیری و آگهی‌های فعلی، فایل SQLite را داخل Git قرار ندهید.
ابتدا در کامپیوتر خود یک snapshot سازگار بسازید:

```powershell
cd C:\Users\Mohsen\Desktop\divar\public-house-finder
python scripts\backup_database.py
```

خروجی در `output/deploy/divar_scout.sqlite3` ساخته و integrity آن بررسی می‌شود. اپلیکیشن
PaaS را متوقف کنید، فایل را از File Manager روی مسیر
`/data/divar_scout.sqlite3` بارگذاری کنید و سپس اپلیکیشن را روشن کنید.

## گیت و استقرار

مخزن را Private نگه دارید. `.gitignore` دیتابیس، WAL، لاگ، خروجی تست، محیط مجازی و
فایل‌های Secret را حذف می‌کند. پیش از هر push اجرا کنید:

```powershell
python scripts/update_architecture.py --check
python -m unittest discover -s tests -v
git status --short
```

در پنل پارس‌پک، Python را انتخاب کنید، منبع را با OAuth به مخزن خصوصی GitHub و branch
`main` وصل کنید، Volume را روی `/data` بسازید و مقادیر این سند را عیناً وارد کنید.
پس از اولین استقرار، Webhook مربوط به branch `main` را برای deploy خودکار pushها فعال کنید.

## کنترل بعد از استقرار

1. `/healthz` باید علاوه بر `ok`، نسخه جاری برنامه را در `release` برگرداند؛ پاسخ بدون
   `release` یعنی هنوز یک build یا Pod قدیمی در حال سرویس‌دهی است. هدر
   `X-Khaneyab-Release` نیز باید همین نسخه را نشان دهد.
2. در Logs باید پیام شروع داشبورد و نتیجه اسکن پس‌زمینه دیده شود.
3. مدیر باید بتواند وارد `/admin` شود.
4. یک ثبت‌نام آزمایشی باید pending بماند تا مدیر آن را تأیید کند.
5. با restart سرویس، حساب‌ها و آگهی‌ها باید باقی بمانند.

پس از هر deploy، تست واقعی API را با یک حساب تأییدشده اجرا کنید. این تست علاوه بر
سلامت فنی، منطبق‌بودن داده‌ها با قرارداد پروفایل و وجود ظرفیت کامل ۱۲ مقایسه را
کنترل می‌کند:

```bash
export KHANEYAB_SMOKE_USERNAME="your-approved-user"
read -s -p "Password: " KHANEYAB_SMOKE_PASSWORD; echo
export KHANEYAB_SMOKE_PASSWORD
python scripts/smoke_production.py --expected-release 0.1.5
unset KHANEYAB_SMOKE_PASSWORD
```

اگر Build موفق بود ولی سایت باز نشد، اول host و port را کنترل کنید. اگر برنامه با خطای
persistent volume متوقف شد، Volume یا `KHANEYAB_DATA_DIR=/data` تنظیم نشده است.
اگر GitHub روی commit جدید است ولی `/healthz` هنوز نسخه قبلی یا پاسخ بدون `release`
می‌دهد، deployment جدید ساخته نشده است: Webhook شاخه `main` را فعال کنید یا از پنل
یک Deploy/Rebuild تازه بزنید و مطمئن شوید تنها Pod قدیمی هم پس از build موفق جایگزین شده است.
