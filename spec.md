# SwimApp

## Overview

A Dash web application for analysing swimming data from Garmin Connect. The app syncs activity data from Garmin, categorises sessions (continuous, interval, open water), corrects missed lane counts, and provides interactive visualisations for tracking pace, speed, and performance trends over time. The user can filter by date range, session category, and time of day, and toggle between different axis representations.

## Technical Stack

- **Language:** Python 3.12
- **Framework:** Dash (Plotly)
- **Data:** Garmin Connect API via `garminconnect` library
- **Storage:** JSON files on disk (one per activity), SQLite for processed/corrected data
- **Dependencies:** dash, plotly, pandas, garminconnect, python-dotenv

## Architecture

### System Concept

The app has three layers:

1. **Sync layer** — on startup, fetches the activity list from Garmin, compares against locally stored activities, and downloads any missing detailed data as JSON files.
2. **Processing layer** — reads raw JSON, applies categorisation heuristics and missed-length correction, and writes normalised records into a SQLite database. This runs after sync and whenever new data arrives.
3. **Presentation layer** — a Dash app serving a single-page UI with sidebar controls (calendar, category dropdown, axis options) and a main chart area with summary statistics.

Data flows: Garmin API -> `data/raw/*.json` -> processing -> `data/swim.db` -> Dash callbacks -> Plotly charts.

### File Structure

```
SwimApp/
├── app.py                     # Dash app entry point, layout, and callbacks
├── sync.py                    # Garmin Connect sync logic (replaces download_swim_data.py)
├── processing.py              # Categorisation, correction, DB ingestion
├── db.py                      # SQLite schema, read/write helpers
├── requirements.txt           # All dependencies
├── .env                       # Garmin credentials (gitignored)
├── .gitignore
├── spec.md
├── tokens/                    # Garmin auth tokens (gitignored)
├── data/
│   ├── raw/                   # Raw JSON per activity (one file each)
│   └── swim.db                # Processed SQLite database
└── assets/
    └── style.css              # Custom Dash CSS overrides
```

### Module Interfaces

#### sync.py
- `sync_activities() -> list[str]` — Connects to Garmin, compares remote activity list against `data/raw/`, downloads missing activities. Returns list of newly downloaded activity IDs. Reuses token caching from `tokens/`.

#### processing.py
- `categorise_activity(activity: dict) -> str` — Returns one of: `"continuous"`, `"interval"`, `"open_water"`. Heuristic based on activity type key, lap count, and stroke variation.
- `correct_missed_lengths(lengths: list[dict], pool_length: float) -> list[dict]` — Detects lengths with duration > 1.7x the median, splits them into two lengths with half the duration each. Returns corrected list.
- `process_activity(filepath: Path) -> dict` — Reads a raw JSON file, applies categorisation and correction, returns a flat dict ready for DB insertion.
- `process_all_new(activity_ids: list[str]) -> int` — Processes specified activities into the DB. Returns count processed.

#### db.py
- `init_db() -> None` — Creates the SQLite schema if it doesn't exist.
- `get_activities(category: str | None, start_date: str | None, end_date: str | None) -> pd.DataFrame` — Query activity-level data with optional filters.
- `get_lengths(activity_ids: list[str]) -> pd.DataFrame` — Query individual length records for given activities.
- `activity_exists(activity_id: str) -> bool` — Check if an activity is already processed.
- `insert_activity(activity: dict, lengths: list[dict]) -> None` — Insert one activity and its lengths.

#### app.py
- Dash layout and callbacks. No business logic — delegates to `db.py` for data and uses Plotly for charts.

### Data Models

#### SQLite Schema

**activities table:**
| Column | Type | Description |
|---|---|---|
| activity_id | TEXT PK | Garmin activity ID |
| date | TEXT | ISO date (YYYY-MM-DD) |
| start_time_local | TEXT | ISO datetime |
| time_of_day_minutes | INTEGER | Minutes since midnight (for time-of-day analysis) |
| category | TEXT | "continuous", "interval", "open_water" |
| total_distance_m | REAL | Total distance in metres |
| total_duration_s | REAL | Total duration in seconds |
| avg_pace_100m | REAL | Average pace in seconds per 100m |
| avg_speed_ms | REAL | Average speed in m/s |
| avg_hr | REAL | Average heart rate (nullable) |
| pool_length_m | REAL | Pool length in metres (nullable for open water) |
| total_strokes | INTEGER | Total stroke count |
| avg_swolf | REAL | Average SWOLF score |

**lengths table:**
| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| activity_id | TEXT FK | References activities |
| length_index | INTEGER | 1-based position within the activity |
| distance_m | REAL | Length distance (typically 25 or 50) |
| duration_s | REAL | Duration in seconds |
| pace_100m | REAL | Pace in seconds per 100m |
| speed_ms | REAL | Speed in m/s |
| stroke_type | TEXT | FREESTYLE, BACKSTROKE, BREASTSTROKE, DRILL, MIXED, etc. |
| stroke_count | INTEGER | Number of strokes |
| hr | REAL | Heart rate (nullable) |
| is_corrected | BOOLEAN | True if this length was split from a missed count |

### Categorisation Heuristic

1. If `activityType.typeKey == "open_water_swimming"` -> `"open_water"`
2. If the activity has multiple Garmin laps with DRILL or MIXED stroke types -> `"interval"`
3. Otherwise -> `"continuous"`

### Missed Length Correction

For each activity's lengths (within a single Garmin lap of active swimming):
1. Compute the median duration of all lengths in that lap.
2. If a length's duration > 1.7x the median, replace it with two lengths, each having half the duration and the same distance as the pool length.
3. Mark the replacement lengths with `is_corrected = True`.

### Error Handling Strategy

- Garmin sync errors: log and continue — partial sync is fine, missing activities will be retried next time.
- Processing errors on individual activities: log, skip, and continue with remaining activities.
- DB errors: surface to user via a Dash alert banner at the top of the page.

## Implementation Steps

### Step 1: Project scaffolding, requirements, and database schema

Set up `requirements.txt` with all dependencies. Implement `db.py` with schema creation and all query/insert helpers. Move existing raw JSON files into `data/raw/`.

**Automated verification:**
- [ ] `source .venv/bin/activate && pip install -r requirements.txt` installs cleanly
- [ ] `python -c "from db import init_db; init_db()"` creates `data/swim.db` with both tables
- [ ] `python -c "from db import get_activities; print(get_activities())"` returns empty DataFrame with correct columns

**Manual verification:**
- [ ] Inspect `data/swim.db` schema with `sqlite3 data/swim.db '.schema'`

### Step 2: Sync module

Refactor `download_swim_data.py` into `sync.py`. It should: authenticate (with token caching), fetch the full activity list filtered to swimming, check which activity IDs already have files in `data/raw/`, and download detailed data for any missing ones.

**Automated verification:**
- [ ] `python -c "from sync import sync_activities; ids = sync_activities(); print(f'Synced {len(ids)} new')"` runs without error (may return 0 if all data is present)
- [ ] All existing JSON files are accessible in `data/raw/`

**Manual verification:**
- [ ] Confirm `data/raw/` contains one JSON file per activity

### Step 3: Processing module (categorisation + correction)

Implement `processing.py`. The `categorise_activity` function classifies based on the heuristic above. The `correct_missed_lengths` function applies the split logic. The `process_activity` function ties it together and returns data ready for DB insertion. `process_all_new` processes a batch into the DB.

**Automated verification:**
- [ ] `python -c "from processing import process_all_new; from db import init_db, get_activities; import os; init_db(); ids = [f.replace('.json','').split('_')[1] for f in os.listdir('data/raw') if f.startswith('swim_')]; n = process_all_new(ids); print(f'Processed {n}'); df = get_activities(); print(df[['date','category','total_distance_m','avg_pace_100m']].to_string())"` processes all activities and prints a table
- [ ] Verify corrected lengths exist: `python -c "from db import get_lengths; df = get_lengths([]); print(f'Corrected: {df.is_corrected.sum()} of {len(df)}')"` shows some corrected lengths

**Manual verification:**
- [ ] Check that the 56s and 55s lengths in the 2026-03-06 activity got split into ~28s pairs
- [ ] Confirm open_water_swimming activity (2025-07-24) is categorised as "open_water"
- [ ] Confirm multi-lap drill sessions are categorised as "interval"

### Step 4: Dash app layout

Build the Dash app layout in `app.py` with:
- **Sidebar** (left, ~300px):
  - Date range picker (calendar-style)
  - Category dropdown (All / Continuous / Interval / Open Water)
  - View mode radio: "Performance Over Time" vs "Within-Swim Profile"
  - X-axis toggle: Length # vs Elapsed Time
  - Y-axis toggle: Pace (min:ss/100m) vs Speed (m/s)
  - Time-of-day filter: All / Morning (<12:00) / Afternoon (>=12:00)
- **Main area** (right):
  - Graph (Plotly figure, full width)
  - Stats panel below the graph: Average pace, Fastest length, Slowest length, Std deviation, Number of sessions

Add `assets/style.css` for basic layout styling.

**Automated verification:**
- [ ] `python app.py` starts without errors and serves on `http://localhost:8050`

**Manual verification:**
- [ ] Open browser, confirm sidebar controls are visible and the chart area is present
- [ ] All dropdowns/toggles render and are clickable (no data needed yet)

### Step 5: Callbacks — Performance Over Time view

Implement the "Performance Over Time" view. When selected:
- X-axis: date
- Y-axis: average pace or speed per session
- Each dot is one swim session (filtered by category and date range)
- Add a trend line (rolling average, window=5)
- Stats panel shows: overall average pace, fastest session, slowest session, std dev, count

**Automated verification:**
- [ ] App loads and renders the chart with real data from the DB
- [ ] Changing category dropdown updates the chart

**Manual verification:**
- [ ] Chart shows sessions over time with correct dates
- [ ] Hovering shows date, pace, distance
- [ ] Stats panel updates when filters change
- [ ] Time-of-day filter correctly separates morning vs afternoon sessions

### Step 6: Callbacks — Within-Swim Profile view

Implement the "Within-Swim Profile" view. When selected:
- X-axis: length number (or elapsed time, per toggle)
- Y-axis: pace or speed per length (per toggle)
- If a single date is selected: show that swim as a single trace
- If a date range is selected: show the average pace at each length position across all selected swims, with a shaded std deviation band
- Each individual activity can also appear as a faint trace behind the average
- Stats panel shows: average pace, fastest length, slowest length, std dev across selected range

**Automated verification:**
- [ ] App renders within-swim chart with length-level data
- [ ] Switching between length # and elapsed time on x-axis works

**Manual verification:**
- [ ] Single-swim view shows per-length pace variation
- [ ] Multi-swim average view shows mean line with deviation band
- [ ] X-axis toggle switches between length count and elapsed time
- [ ] Y-axis toggle switches between pace (mm:ss) and speed (m/s)
- [ ] Corrected lengths are visually indistinguishable from normal ones (no artefacts)

### Step 7: Startup sync integration

Wire `sync.py` and `processing.py` into `app.py` startup. When the app starts:
1. Run `sync_activities()` to download any new data
2. Run `process_all_new()` on any newly synced activities
3. Then start the Dash server

Add a startup banner/log message showing sync results.

**Automated verification:**
- [ ] `python app.py` performs sync, processes new data, and starts the server
- [ ] Running it a second time immediately shows "0 new activities" (no duplicate processing)

**Manual verification:**
- [ ] After adding a new swim on Garmin and restarting the app, the new activity appears in the chart

## Configuration

| Variable | Source | Default | Description |
|---|---|---|---|
| `GARMIN_EMAIL` | `.env` | — | Garmin Connect email |
| `GARMIN_PASSWORD` | `.env` | — | Garmin Connect password |
| `DASH_DEBUG` | `.env` | `false` | Enable Dash debug mode |
| `PORT` | `.env` | `8050` | Dash server port |

## Future Considerations

- FIT file download and parsing for stroke-level analysis (stroke rate curves, distance per stroke)
- Export charts as PNG/PDF
- Comparison mode: overlay two specific sessions side-by-side
- SWOLF analysis view
- Heart rate zone analysis per swim segment
- Goal setting and progress tracking
- Docker deployment for easy sharing
