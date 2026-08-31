# Deploying to Chabokan with the CLI

This project uses Chabokan's raw Python runtime. Chabokan starts `start.sh`, which
executes `app.py`. Keep exactly one running service instance while SQLite is in use.

## 1. Prepare the Python service

In the Chabokan service panel:

1. Select Python `3.12`.
2. Set the service/default port to `8766`.
3. The first deploy does not require environment variables. `start.sh` supplies
   bootstrap-safe defaults:

```text
KHANEYAB_RUNTIME=paas
KHANEYAB_HOST=0.0.0.0
KHANEYAB_PORT=8766
KHANEYAB_DATA_DIR=/app/runtime-data
KHANEYAB_WATCH_ENABLED=false
```

The first deployment is only used to activate Chabokan's permanent-path controls.
It deliberately starts without an administrator or background scans.

## 2. Log in and deploy

The token shown in screenshots or chat must be regenerated before use. Run these
commands in Git Bash and replace `YOUR_NEW_TOKEN` locally. Never commit the token.

```bash
cd "/c/Users/Mohsen/Desktop/divar/public-house-finder"
npm install -g @chabokan.net/cli
chabok -v
chabok login -t "YOUR_NEW_TOKEN"
chabok deploy
```

Choose the existing Python service when the CLI asks for the deployment target.
The CLI uploads the current project; `.gitignore` excludes `.venv`, local databases,
cache files, browser artifacts, and generated output.

## 3. Make the database persistent

After the first successful deploy, open the service's permanent-path settings and
add this exact path:

```text
/app/runtime-data
```

Do not mount `/app/data`: that directory contains the versioned Tehran neighborhood
catalog and must continue to be replaced by deployments.

Now add or update these environment variables:

```text
KHANEYAB_RUNTIME=paas
KHANEYAB_HOST=0.0.0.0
KHANEYAB_PORT=8766
KHANEYAB_DATA_DIR=/app/runtime-data
KHANEYAB_WATCH_ENABLED=true
KHANEYAB_ADMIN_USERNAME=root_admin
KHANEYAB_ADMIN_DISPLAY_NAME=مدیر اصلی
KHANEYAB_ADMIN_PASSWORD=REPLACE_WITH_A_STRONG_PASSWORD
CHBK_PYTHON_SAVE=true
PIP_INDEX_URL=https://mirror2.chabokan.net/pypi/simple/
```

The password must be at least 10 characters and include at least one letter and one
digit. Restart the service after saving the permanent path and variables. Once the
administrator has been created successfully, remove `KHANEYAB_ADMIN_PASSWORD` from
the environment and restart again; the account remains in SQLite.

## 4. Verify the release

Open the Chabokan domain and then `/healthz`. A healthy release returns JSON similar
to:

```json
{"ok": true, "release": "0.1.6"}
```

Useful CLI commands:

```bash
chabok service list
chabok service logs
chabok service restart
```

For an authenticated production check, pass the Chabokan domain explicitly:

```bash
export KHANEYAB_SMOKE_USERNAME="your-approved-user"
read -s -p "Password: " KHANEYAB_SMOKE_PASSWORD; echo
export KHANEYAB_SMOKE_PASSWORD
python scripts/smoke_production.py \
  --base-url "https://YOUR-CHABOKAN-DOMAIN" \
  --expected-release 0.1.6 \
  --read-only
unset KHANEYAB_SMOKE_PASSWORD
```

Remove `--read-only` only when that account has a complete search profile and a real
scan is intended.
