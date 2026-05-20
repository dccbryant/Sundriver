# Sundriver

Hourly weather/UV → Google Ads automation for a sunscreen brand.

Two small Python scripts and one Google Sheet. A solo marketer manages
thresholds in the Sheet; the scripts do the rest.

```
NOAA (api.weather.gov)   ┐
                          ├──►  fetch_weather.py  ──► Google Sheet (Weather Data)
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

- Every hour, pulls temperature + short forecast from the NOAA National
  Weather Service API and the UV index from the EPA Envirofacts UV hourly
  forecast API for one representative point per US state
  (`config/states.json` — state capitals by default).
- Writes the latest snapshot to the **Weather Data** tab of a Google Sheet.
- Reads the **Thresholds** tab (one row per state × campaign with min
  temperature °F, min UV index, and a kill switch) and the Weather Data
  tab.
- For each row, decides ENABLED or PAUSED, compares against the current
  status in Google Ads, and only sends a change when needed.
- Appends every decision to the **Campaign Log** tab.

Decision rule per Thresholds row:

| Conditions met? | Action |
|---|---|
| `temperature_f ≥ min_temp_f` **and** `uv_index ≥ min_uv` | ENABLED |
| Either threshold missed | PAUSED |
| `enabled = FALSE` in the sheet | Skipped (kill switch) |
| Weather data missing for that state | PAUSED (failsafe) |

Blank threshold cells are ignored (i.e. only the populated signal is
checked). If both are blank, the row is skipped with a warning so you
don't accidentally pause everything.

## Sheet layout

Create one Google Sheet and share it with the service account email as an
Editor. The scripts auto-create the tabs and header rows on first run.

**Weather Data** (auto-written, do not edit by hand)

| state | name | city | lat | lon | zip | temperature_f | uv_index | short_forecast | observed_at_utc | source_notes |

**Thresholds** (you own this)

| state | campaign_id | campaign_name | min_temp_f | min_uv | enabled | notes |
|-------|-------------|---------------|-----------:|-------:|---------|-------|

Example rows:

| state | campaign_id | campaign_name        | min_temp_f | min_uv | enabled | notes                        |
|-------|-------------|----------------------|-----------:|-------:|---------|------------------------------|
| FL    | 1234567890  | SPF50 Banner — FL    | 75         | 6      | TRUE    | summer banner                |
| AK    | 1234567891  | SPF50 Banner — AK    |            | 5      | TRUE    | UV-only trigger              |
| CA    | 1234567892  | After-sun — CA       | 80         |        | TRUE    | heat-driven, no UV gate      |
| TX    | 1234567893  | Test campaign        | 75         | 6      | FALSE   | paused while QAing creatives |

**Campaign Log** (auto-appended)

| timestamp_utc | state | campaign_id | action | reason |

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

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env             # edit values
cp google-ads.yaml.example google-ads.yaml   # edit values

# one-shot, no API calls — verifies sheet read/write only
python src/fetch_weather.py

# dry-run the ads step — decisions logged to the sheet, no mutations
python src/update_campaigns.py --dry-run

# real run
python src/run_hourly.py
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

## Notes & limits

- NOAA's API is keyless but requires a descriptive `User-Agent` per their
  policy — set `NOAA_USER_AGENT` to something that identifies you and
  includes a contact.
- One point per state is a reasonable proxy for state-level Google Ads
  geo targeting; to go finer (DMA, metro, ZIP), add more rows to
  `config/states.json` and use a richer key than `state` in the Sheet.
- The Google Ads campaign IDs in the Thresholds tab must already be
  geo-targeted to the matching state — this tool only flips ENABLED /
  PAUSED, it doesn't manage targeting.
- All times in the sheet are UTC.
- Failsafe is biased toward pausing: if weather data is missing or the
  API call fails, campaigns get paused rather than left running blind.
