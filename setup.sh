#!/usr/bin/env bash
# Sundriver one-shot setup.
#
# Idempotent: safe to re-run after you've gathered missing credentials.
# Does the mechanical bits (virtualenv, dependencies, directory layout,
# example file copies) and runs a connection check at the end. The parts
# that genuinely need a human in front of a browser (downloading the
# service-account JSON, getting an Ads refresh token) are listed as a
# printed checklist.
#
# Usage:
#   ./setup.sh              # full setup + smoke test
#   ./setup.sh --skip-ads   # set up and only verify Sheets (use this
#                             before you have your Ads refresh token)
#   ./setup.sh --no-check   # skip the smoke test entirely
#   ./setup.sh --install-cron  # also install an hourly crontab entry

set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"

# ---------- colours (degrade gracefully if not a TTY) ----------
if [ -t 1 ]; then
  B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; D=$'\033[0m'
else
  B=""; G=""; Y=""; R=""; D=""
fi
step() { printf "\n${B}==> %s${D}\n" "$*"; }
ok()   { printf "${G}    ok${D} — %s\n" "$*"; }
warn() { printf "${Y}    !!${D} %s\n" "$*"; }
die()  { printf "${R}    error:${D} %s\n" "$*" >&2; exit 1; }

# ---------- args ----------
SKIP_ADS=0; SKIP_CHECK=0; INSTALL_CRON=0
for arg in "$@"; do
  case "$arg" in
    --skip-ads)     SKIP_ADS=1 ;;
    --no-check)     SKIP_CHECK=1 ;;
    --install-cron) INSTALL_CRON=1 ;;
    -h|--help)
      sed -n '2,18p' "$0"; exit 0 ;;
    *) die "unknown arg: $arg" ;;
  esac
done

# ---------- 1. python ----------
step "Checking Python"
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  die "Python 3.9+ not found. Install from https://www.python.org/downloads/"
fi
PY_VER=$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])')
ok "found $PY ($PY_VER)"
"$PY" -c 'import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)' \
  || die "Need Python 3.9+, found $PY_VER"

# ---------- 2. virtualenv ----------
step "Creating virtualenv at .venv"
if [ ! -d .venv ]; then
  "$PY" -m venv .venv
  ok "created .venv"
else
  ok ".venv already exists"
fi
# shellcheck disable=SC1091
source .venv/bin/activate
ok "activated"

# ---------- 3. dependencies ----------
step "Installing Python dependencies"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
ok "requirements.txt installed"

# ---------- 4. secrets directory ----------
step "Preparing secrets/ directory"
mkdir -p secrets
chmod 700 secrets
ok "secrets/ exists with 0700 perms"

# ---------- 5. config files ----------
step "Materialising config files"
if [ ! -f .env ]; then
  cp .env.example .env
  ok "created .env from .env.example — edit it before next step"
  ENV_NEW=1
else
  ok ".env already exists"
  ENV_NEW=0
fi
if [ ! -f google-ads.yaml ]; then
  cp google-ads.yaml.example google-ads.yaml
  chmod 600 google-ads.yaml
  ok "created google-ads.yaml from example — edit it before next step"
  ADS_NEW=1
else
  chmod 600 google-ads.yaml
  ok "google-ads.yaml already exists"
  ADS_NEW=0
fi

# ---------- 6. manual checklist ----------
if [ "$ENV_NEW" = "1" ] || [ "$ADS_NEW" = "1" ]; then
  cat <<EOF

${B}>>> Manual steps required before the smoke test will pass <<<${D}

  1. Google Sheets service account
     - Create a project at https://console.cloud.google.com/
     - Enable the Google Sheets API and Google Drive API
     - Create a service account, download its JSON key
     - Save it to: secrets/sheets-service-account.json
     - Share your Google Sheet with the service account's email as Editor

  2. Edit .env
     - SPREADSHEET_ID = the long ID from your sheet URL
     - NOAA_USER_AGENT = your app name + your contact email
       (NOAA requires this — they will rate-limit a generic UA)

  3. Google Ads API
     - Apply for a developer token:
       https://developers.google.com/google-ads/api/docs/first-call/dev-token
     - Create an OAuth2 client + generate a refresh token (one-time browser flow)
     - Fill in google-ads.yaml with developer_token, client_id, client_secret,
       refresh_token, and login_customer_id
     - Put your numeric customer ID in .env as GOOGLE_ADS_CUSTOMER_ID

  When the above are done, re-run: ./setup.sh
EOF
fi

# ---------- 7. smoke test ----------
load_env() {
  # Export non-comment KEY=VALUE lines from .env. We parse manually (rather
  # than `source .env`) so values containing shell metacharacters like
  # parens — common in NOAA_USER_AGENT — don't break the script.
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    [[ "$line" != *=* ]] && continue
    name="${line%%=*}"
    val="${line#*=}"
    # strip optional surrounding quotes
    case "$val" in
      \"*\") val="${val#\"}"; val="${val%\"}" ;;
      \'*\') val="${val#\'}"; val="${val%\'}" ;;
    esac
    export "$name=$val"
  done < .env
}

if [ "$SKIP_CHECK" = "1" ]; then
  step "Skipping smoke test (--no-check)"
else
  step "Running smoke test"
  if [ -f .env ]; then load_env; fi

  CHECK_ARGS=()
  [ "$SKIP_ADS" = "1" ] && CHECK_ARGS+=(--skip-ads)

  if [ -z "${GOOGLE_SHEETS_CREDENTIALS:-}" ] || [ -z "${SPREADSHEET_ID:-}" ]; then
    warn "GOOGLE_SHEETS_CREDENTIALS / SPREADSHEET_ID not set in .env — finish the manual steps then re-run ./setup.sh"
  elif [ ! -f "${GOOGLE_SHEETS_CREDENTIALS}" ]; then
    warn "Service account JSON not found at ${GOOGLE_SHEETS_CREDENTIALS} — download it then re-run ./setup.sh"
  else
    if python src/check_setup.py "${CHECK_ARGS[@]}"; then
      ok "smoke test passed"
    else
      warn "smoke test failed — see messages above"
    fi
  fi
fi

# ---------- 8. optional cron ----------
if [ "$INSTALL_CRON" = "1" ]; then
  step "Installing hourly crontab entry"
  CRON_LINE="5 * * * * cd $ROOT && $ROOT/.venv/bin/python src/run_hourly.py >> $ROOT/sundriver.log 2>&1"
  EXISTING="$(crontab -l 2>/dev/null || true)"
  if echo "$EXISTING" | grep -Fq "$ROOT/src/run_hourly.py"; then
    ok "cron entry already present"
  else
    printf "%s\n%s\n" "$EXISTING" "$CRON_LINE" | crontab -
    ok "added: $CRON_LINE"
    ok "logs will go to $ROOT/sundriver.log"
  fi
fi

# ---------- 9. summary ----------
cat <<EOF

${B}Setup complete.${D} Next:

  - One-off dry run (decisions logged to sheet, no campaigns changed):
      source .venv/bin/activate
      python src/update_campaigns.py --dry-run

  - Real hourly run:
      python src/run_hourly.py

  - To schedule on this machine:
      ./setup.sh --install-cron

  - To schedule on GitHub Actions:
      add SHEETS_SA_JSON, GOOGLE_ADS_YAML, SPREADSHEET_ID,
      GOOGLE_ADS_CUSTOMER_ID, NOAA_USER_AGENT as repo secrets;
      the workflow in .github/workflows/hourly.yml runs every hour.
EOF
