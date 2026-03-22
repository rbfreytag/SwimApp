# SwimApp

## Overview

A Dash web application for analysing swimming data from Garmin Connect. The app syncs activity data from Garmin, categorises sessions by distance and type (with configurable distance bucketing), corrects missed lane counts, and provides interactive visualisations for tracking pace, speed, and performance trends over time. The user can filter by date range, session category, and time of day, and toggle between different axis representations.

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

The processing pipeline runs in this order: **correct -> categorise -> extract**.

- `correct_missed_lengths(lengths: list[dict], pool_length: float) -> list[dict]` — Detects lengths with duration > 1.7x the median, splits them into two lengths with half the duration each. Returns corrected list. **Runs first** so that categorisation and extraction operate on accurate length counts.
- `categorise_activities(activities: list[dict]) -> None` — Two-pass categorisation. First pass: assign `"interval"`, `"open_water"`, or mark as continuous candidate with its corrected distance. Second pass: bucket all continuous candidates by distance (see Categorisation section), assigning labels like `"1500m"`, `"1000m"`, or `"other"`. Updates each activity's category in the DB. **Runs second** so distance bucketing uses corrected distances.
- `extract_continuous_block(lengths: list[dict], target_distance: float, pool_length: float) -> list[dict]` — For activities where the corrected distance exceeds the category target (e.g. 1550m in the 1500m bucket), extract the fastest contiguous block of lengths that sum to the target distance. Returns the trimmed length list. **Runs third** (triggered during pass 2 of categorisation) so it operates on corrected lengths and knows the target distance from bucketing.
- `process_activity(filepath: Path) -> dict` — Reads a raw JSON file, applies missed-length correction, returns a flat dict ready for DB insertion. Category and block extraction are applied later in the batch pass.
- `process_all_new(activity_ids: list[str]) -> int` — Orchestrates the full pipeline: for each new activity run `process_activity` (which applies correction), insert into DB, then run `categorise_activities` across all activities (which triggers extraction where needed). Returns count processed.

#### db.py
- `init_db() -> None` — Creates the SQLite schema if it doesn't exist.
- `get_activities(category: str | None, start_date: str | None, end_date: str | None) -> pd.DataFrame` — Query activity-level data with optional filters.
- `get_lengths(activity_ids: list[str]) -> pd.DataFrame` — Query individual length records for given activities.
- `get_categories() -> list[str]` — Return all distinct category values in the DB.
- `get_activity_dates(category: str | None) -> list[str]` — Return all dates that have an activity, optionally filtered by category (for calendar highlighting).
- `activity_exists(activity_id: str) -> bool` — Check if an activity is already processed.
- `insert_activity(activity: dict, lengths: list[dict]) -> None` — Insert one activity and its lengths.
- `update_activity_category(activity_id: str, category: str) -> None` — Update an activity's category.
- `update_activity_lengths(activity_id: str, lengths: list[dict]) -> None` — Replace an activity's lengths (after block extraction).

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
| category | TEXT | e.g. "1500m", "1000m", "interval", "open_water", "other" |
| total_distance_m | REAL | Total distance in metres (after block extraction) |
| total_duration_s | REAL | Total duration in seconds (after block extraction) |
| raw_distance_m | REAL | Original total distance before block extraction |
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
| length_index | INTEGER | 1-based position within the activity (after extraction) |
| distance_m | REAL | Length distance (typically 25 or 50) |
| duration_s | REAL | Duration in seconds |
| pace_100m | REAL | Pace in seconds per 100m |
| speed_ms | REAL | Speed in m/s |
| stroke_type | TEXT | FREESTYLE, BACKSTROKE, BREASTSTROKE, DRILL, MIXED, etc. |
| stroke_count | INTEGER | Number of strokes |
| hr | REAL | Heart rate (nullable) |
| is_corrected | BOOLEAN | True if this length was split from a missed count |

### Categorisation

Categorisation is a two-pass process across all activities:

**Pass 1 — Type classification (operates on corrected lengths):**
1. If `activityType.typeKey == "open_water_swimming"` -> `"open_water"`
2. If the activity has any Garmin lap with `swimStroke == "DRILL"` -> `"interval"`. Note: only DRILL triggers interval classification. Other stroke types like BUTTERFLY or MIXED after a main set do not — the session should still be categorised by its continuous distance.
3. Otherwise -> mark as continuous candidate with its corrected distance

**Pass 2 — Distance bucketing (continuous swims only, using corrected distances):**
1. Collect all continuous swim corrected distances.
2. Round each distance to the nearest `DISTANCE_MARGIN` (default: 50m, configurable via env var `SWIM_DISTANCE_MARGIN_M`).
3. Group by rounded distance. Any group with >= `MIN_CATEGORY_COUNT` swims (default: 3, configurable via env var `SWIM_MIN_CATEGORY_COUNT`) becomes its own category, labelled by the target distance (e.g. `"1500m"`, `"1000m"`).
4. Activities whose corrected distance exceeds the bucket target: run `extract_continuous_block` on the already-corrected lengths to take the fastest contiguous block matching the target, then update the stored lengths and recalculate stats.
5. Activities that don't fit any bucket -> `"other"`.

**Example:** With margin=50m and min_count=3, if there are 40 swims at 1475-1550m, they all bucket to `"1500m"`. The 1550m swims get trimmed to the fastest 60 lengths (60 x 25m = 1500m). If there are only 2 swims at ~1000m, those go to `"other"` until a third appears.

### Pipeline Order

The three processing stages must run in this order:

**Stage 1: Missed Length Correction** (per activity, on ingest)

For each activity's lengths (within a single Garmin lap of active swimming):
1. Compute the median duration of all lengths in that lap.
2. If a length's duration > 1.7x the median, replace it with two lengths, each having half the duration and the same distance as the pool length.
3. Mark the replacement lengths with `is_corrected = True`.

This must run first because corrected lengths change the total distance count, which affects categorisation.

**Stage 2: Categorisation** (batch, across all activities)

Uses the corrected distances and lap data to classify each activity (see Categorisation section above). This must run before block extraction because we need the category's target distance.

**Stage 3: Fastest Block Extraction** (per activity, triggered during categorisation pass 2)

When a continuous swim's corrected distance exceeds its category target (e.g. 1550m in the 1500m bucket):
1. Calculate how many lengths make up the target distance: `n = target / pool_length`.
2. Slide a window of size `n` across the activity's already-corrected lengths.
3. Pick the window with the lowest total duration (fastest block).
4. Store only those lengths (re-indexed from 1) and update the activity's `total_distance_m`, `total_duration_s`, and derived stats.
5. Preserve `raw_distance_m` as the original corrected distance before extraction.

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

### Step 2: Sync module

Refactor `download_swim_data.py` into `sync.py`. It should: authenticate (with token caching), fetch the full activity list filtered to swimming, check which activity IDs already have files in `data/raw/`, and download detailed data for any missing ones.

**Automated verification:**
- [ ] `python -c "from sync import sync_activities; ids = sync_activities(); print(f'Synced {len(ids)} new')"` runs without error (may return 0 if all data is present)
- [ ] All existing JSON files are accessible in `data/raw/`

### Step 3: Processing module (categorisation + correction)

Implement `processing.py` with:
- `correct_missed_lengths` — split anomalous lengths
- `extract_continuous_block` — fastest block extraction for over-distance swims
- `categorise_activities` — two-pass type + distance bucketing
- `process_activity` / `process_all_new` — orchestration

**Automated verification:**
- [ ] Run processing on all existing data and print a category summary table:
  ```
  python -c "
  from processing import process_all_new
  from db import init_db, get_activities
  import os
  init_db()
  ids = [f.split('_')[1] for f in os.listdir('data/raw') if f.startswith('swim_')]
  n = process_all_new(ids)
  df = get_activities()
  print(df[['date','category','total_distance_m','raw_distance_m','avg_pace_100m']].to_string())
  print()
  print(df.groupby('category').size())
  "
  ```
- [ ] Verify corrected lengths: `python -c "from db import get_lengths; df = get_lengths([]); print(f'Corrected: {df.is_corrected.sum()} of {len(df)}')"` shows some corrected lengths
- [ ] Generate and push a verification chart showing the categorisation and correction results:
  ```
  python verify_processing.py
  ```
  This script (created as part of this step) produces `data/verify_processing.html` — a static Plotly chart with:
  - Panel 1: all activities plotted by date, coloured by category, y-axis = distance
  - Panel 2: length durations for the 2026-03-06 activity before/after correction (showing the 56s/55s splits)

  Commit and push this chart so it can be reviewed on GitHub or downloaded to phone.

### Step 4: Dash app layout

Build the Dash app layout in `app.py` with:
- **Sidebar** (left, ~300px):
  - Date range picker (calendar-style) — highlights dates that have activities in the selected category
  - Category dropdown — dynamically populated from DB categories (e.g. "All", "1500m", "1000m", "interval", "open_water", "other")
  - View mode radio: "Performance Over Time" vs "Within-Swim Profile"
  - X-axis toggle: Length # vs Elapsed Time (only visible in Within-Swim view)
  - Y-axis toggle: Pace (min:ss/100m) vs Speed (m/s)
  - Time-of-day filter: All / Morning (<12:00) / Afternoon (>=12:00)
  - "Show average + deviation" toggle (only visible in Within-Swim view, off by default)
  - "Top N fastest" input + button (only visible in Within-Swim view) — select the N fastest activities in the current category and plot them
- **Main area** (right):
  - Graph (Plotly figure, full width)
  - Stats panel below the graph: Average pace, Fastest, Slowest, Std deviation, Number of sessions

Add `assets/style.css` for basic layout styling.

**Automated verification:**
- [ ] `python app.py &` starts without errors and `curl -s http://localhost:8050 | head -20` returns HTML

### Step 5: Callbacks — Performance Over Time view

Implement the "Performance Over Time" view. When selected:
- X-axis: date
- Y-axis: average pace or speed per session (per y-axis toggle)
- Each dot is one swim session (filtered by category, date range, time of day)
- Add a trend line (rolling average, window=5)
- Stats panel shows: overall average pace, fastest session, slowest session, std dev, count
- Selecting a category highlights matching dates on the calendar

**Automated verification:**
- [ ] Generate a static version of the Performance Over Time chart for the "1500m" category and save as `data/verify_over_time.html`. Commit and push for review.

### Step 6: Callbacks — Within-Swim Profile view

Implement the "Within-Swim Profile" view. When selected:
- X-axis: length number (or elapsed time, per toggle)
- Y-axis: pace or speed per length (per toggle)
- **Default mode:** one trace per activity for all selected dates. Each trace labelled by date.
- **Average mode** (toggled on): show the mean pace at each length position across selected swims, with a shaded std deviation band. Individual traces become faint behind the average.
- **Top N mode:** when the user enters N and clicks the button, select the N fastest activities (by avg_pace_100m) from the current category and date range, and plot those as individual traces.
- Stats panel shows: average pace, fastest length, slowest length, std dev across selected range

**Automated verification:**
- [ ] Generate a static version of the Within-Swim chart showing all "1500m" activities as individual traces, save as `data/verify_in_swim.html`. Commit and push for review.

### Step 7: Startup sync integration

Wire `sync.py` and `processing.py` into `app.py` startup. When the app starts:
1. Run `sync_activities()` to download any new data
2. Run `process_all_new()` on any newly synced activities
3. Then start the Dash server

Add a startup banner/log message showing sync results.

**Automated verification:**
- [ ] `python app.py` performs sync, processes new data, and starts the server
- [ ] Running it a second time immediately shows "0 new activities" (no duplicate processing)

## Configuration

| Variable | Source | Default | Description |
|---|---|---|---|
| `GARMIN_EMAIL` | `.env` | — | Garmin Connect email |
| `GARMIN_PASSWORD` | `.env` | — | Garmin Connect password |
| `SWIM_DISTANCE_MARGIN_M` | `.env` | `50` | Distance margin in metres for bucketing continuous swims into categories |
| `SWIM_MIN_CATEGORY_COUNT` | `.env` | `3` | Minimum number of swims at a distance before it becomes its own category |
| `DASH_DEBUG` | `.env` | `false` | Enable Dash debug mode |
| `PORT` | `.env` | `8050` | Dash server port |

## Future Considerations

- FIT file download and parsing for stroke-level analysis (stroke rate curves, distance per stroke)
- Export charts as PNG/PDF
- SWOLF analysis view
- Heart rate zone analysis per swim segment
- Goal setting and progress tracking
- Docker deployment for easy sharing
