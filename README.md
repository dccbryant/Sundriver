# Sundriver

Hourly weather/UV → Google Ads automation for a sunscreen brand, driven by a
list of Target stores in `config/targets.csv` (1,989 locations by default).

Two small Python scripts and one Google Sheet. A solo marketer manages
per-store thresholds in the Sheet; the scripts do the rest.

```
NOAA (api.weather.gov)   ┐
                          ├──►  fetch_weather.py  ──► Google Sheet (Weather Data, one row per store)
EPA UV (data.epa.gov)    ┘                                       │
                                                                 ▼
                                          update_campaigns.py ◄── Thresholds tab
                                                  │
                                                  ▼
                                          Google Ads API  (pause / enable)
                                                  │
                                                  ▼
                                          Campaign Log tab (audit trail)
```

## What it does

- Every hour, pulls **per-store** temperature + short forecast from the NOAA
  National Weather Service API and the UV index from the EPA Envirofacts UV
  hourly forecast API, for every store in `config/targets.csv`.
- Deduplicates calls aggressively: stores sharing a NOAA forecast grid only
  trigger one hourly-forecast call, and stores in the same `(state, city)`
  share one EPA UV call. NOAA `/points` lookups are cached to disk forever.
- Writes one row per store to the **Weather Data** tab of a Google Sheet.
- Reads the **Thresholds** tab (one row per store × campaign with min
  temperature °F, min UV index, and a kill switch).
- For each row, decides ENABLED or PAUSED, compares against the current
  status in Google Ads, and only sends a change when needed.
- Appends every decision to the **Campaign Log** tab.

Decision rule per Thresholds row:

| Conditions met? | Action |
|---|---|
| `temperature_f ≥ min_temp_f` **and** `uv_index ≥ min_uv` | ENABLED |
| Either threshold missed | PAUSED |
| `enabled = FALSE` in the sheet | Skipped (kill switch) |
| Weather data missing for that store | PAUSED (failsafe) |

Blank threshold cells are ignored (i.e. only the populated signal is
checked). If both are blank, the row is skipped with a warning so you
don't accidentally pause everything.

## Sheet layout

Create one Google Sheet and share it with the service account email as an
Editor. The scripts auto-create the tabs and header rows on first run.

**Weather Data** (auto-written, do not edit by hand)

| store_name | city | state | lat | lng | temperature_f | uv_index | short_forecast | observed_at_utc | source_notes |

**Thresholds** (you own this)

| store_name | campaign_id | campaign_name | min_temp_f | min_uv | enabled | notes |
|------------|-------------|---------------|-----------:|-------:|---------|-------|

`store_name` must match the value in Weather Data exactly (e.g. `Target
Bessemer`). You don't need a row for every store — just the ones with an
active campaign.

Example rows:

| store_name           | campaign_id | campaign_name             | min_temp_f | min_uv | enabled | notes                        |
|----------------------|-------------|---------------------------|-----------:|-------:|---------|------------------------------|
| Target Bessemer      | 1234567890  | SPF50 Banner — Bessemer   | 75         | 6      | TRUE    | summer banner                |
| Target Juneau        | 1234567891  | SPF50 Banner — Juneau     |            | 5      | TRUE    | UV-only trigger              |
| Target Sacramento    | 1234567892  | After-sun — Sacramento    | 80         |        | TRUE    | heat-driven, no UV gate      |
| Target Austin Mueller| 1234567893  | Test campaign — Austin    | 75         | 6      | FALSE   | paused while QAing creatives |

**Campaign Log** (auto-appended)

| timestamp_utc | store_name | campaign_id | action | reason |

## Setup

### 1. Google service account for Sheets

1. In Google Cloud Console, create a project and enable the Google Sheets
   API + Google Drive API.
2. Create a service account, generate a JSON key, download it.
3. Open your Google Sheet and Share it with the service account's email
   address as Editor.
4. Save the JSON file path to `GOOGLE_SHEETS_CREDENTIALS`.

### 2. Google Ads API access

1. Apply for a Google Ads developer token
   (https://developers.google.com/google-ads/api/docs/first-call/dev-token).
2. Set up an OAuth2 client and generate a refresh token for the user that
   has access to the Ads account.
3. Fill in `google-ads.yaml` (see `google-ads.yaml.example`).
4. Note your customer ID (10-digit number, no dashes).

### 3. Local install

The fastest path is `./setup.sh`, which creates the virtualenv, installs
dependencies, sets up the `secrets/` directory, copies the example config
files, runs a connection check, and prints a checklist of any human steps
that still need doing. It's idempotent — re-run it as you finish each
manual step.

```bash
./setup.sh                # full setup + smoke test
./setup.sh --skip-ads     # before you have an Ads refresh token
./setup.sh --install-cron # also install an hourly crontab entry
./setup.sh --no-check     # skip the smoke test
```

Once setup passes:

```bash
source .venv/bin/activate

# dry-run the ads step — decisions logged to the sheet, no mutations
python src/update_campaigns.py --dry-run

# real run
python src/run_hourly.py
```

The first run does roughly 1,989 NOAA `/points` lookups and caches them to
`config/.noaa-points-cache.json`; subsequent runs only re-fetch the hourly
forecast per unique grid (~300–500 calls) plus UV per unique `(state, city)`
(~1,400 calls). Plan for ~10 minutes cold start, ~3–5 minutes warm.

To re-verify credentials at any time without making changes:

```bash
python src/check_setup.py
```

### 4. Schedule it

- **GitHub Actions**: `.github/workflows/hourly.yml` runs every hour. Add
  these repo secrets:
  - `SHEETS_SA_JSON` — full contents of the service account JSON
  - `GOOGLE_ADS_YAML` — full contents of google-ads.yaml
  - `SPREADSHEET_ID`, `GOOGLE_ADS_CUSTOMER_ID`, `NOAA_USER_AGENT`
- **Cron** (any always-on box):
  ```
  5 * * * * cd /opt/sundriver && /opt/sundriver/.venv/bin/python src/run_hourly.py >> /var/log/sundriver.log 2>&1
  ```

## Standalone dashboard

`dashboard.html` is a single self-contained HTML file showing all 1,989
stores in a sortable, filterable table — open it directly from your
desktop. It auto-refreshes every hour, can save the snapshot as Excel,
and can optionally push the snapshot to a Google Sheet (Settings panel
holds the OAuth client ID, Spreadsheet ID, tab name, and an auto-update
checkbox). See the in-page banner for the one CORS caveat when opening
from `file://`.

## Notes & limits

- NOAA's API is keyless but requires a descriptive `User-Agent` per their
  policy — set `NOAA_USER_AGENT` to something that identifies you and
  includes a contact.
- The Google Ads campaign IDs in the Thresholds tab must already be
  geo-targeted to the matching store's catchment — this tool only flips
  ENABLED / PAUSED, it doesn't manage targeting.
- All times in the sheet are UTC.
- Failsafe is biased toward pausing: if weather data is missing or the
  API call fails, campaigns get paused rather than left running blind.
- To update the target store list, edit `config/targets.csv` and rerun.
  The dashboard inlines the store list — if you change the CSV, regenerate
  the inlined block in `dashboard.html` (or just keep using the CSV via
  the Python pipeline).
