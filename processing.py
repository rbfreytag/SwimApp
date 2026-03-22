"""Processing pipeline: correct -> categorise -> extract."""

import json
import logging
import os
import statistics
from pathlib import Path

from dotenv import load_dotenv

import db

load_dotenv()

logger = logging.getLogger(__name__)

RAW_DIR = Path("data/raw")

DISTANCE_MARGIN = float(os.getenv("SWIM_DISTANCE_MARGIN_M", "50"))
MIN_CATEGORY_COUNT = int(os.getenv("SWIM_MIN_CATEGORY_COUNT", "3"))


# ---------------------------------------------------------------------------
# Stage 1: Missed length correction
# ---------------------------------------------------------------------------

def correct_missed_lengths(lengths: list[dict], pool_length: float) -> list[dict]:
    """Split anomalously long lengths (likely missed lane counts).

    A length with duration > 1.7x the median gets split into two half-duration
    lengths, each with distance = pool_length.
    """
    if not lengths or pool_length <= 0:
        return lengths

    durations = [l["duration_s"] for l in lengths]
    median_dur = statistics.median(durations)
    threshold = median_dur * 1.7

    corrected = []
    for l in lengths:
        if l["duration_s"] > threshold:
            half_dur = l["duration_s"] / 2
            for _ in range(2):
                new_l = dict(l)
                new_l["duration_s"] = half_dur
                new_l["distance_m"] = pool_length
                new_l["pace_100m"] = (half_dur / pool_length) * 100
                new_l["speed_ms"] = pool_length / half_dur
                new_l["is_corrected"] = True
                corrected.append(new_l)
        else:
            corrected.append(l)

    # Re-index
    for i, l in enumerate(corrected):
        l["length_index"] = i + 1

    return corrected


# ---------------------------------------------------------------------------
# Stage 2: Categorisation
# ---------------------------------------------------------------------------

def _get_activity_type_key(data: dict) -> str:
    """Extract the activity type key from raw JSON data."""
    # New format: has activity_list_entry
    entry = data.get("activity_list_entry", {})
    type_key = entry.get("activityType", {}).get("typeKey", "")
    if type_key:
        return type_key

    # Fallback: check summary file for old-format files
    summary_path = RAW_DIR / "swim_activities_summary.json"
    if summary_path.exists():
        activity_id = str(data.get("summary", {}).get("activityId", ""))
        with open(summary_path) as f:
            summary_list = json.load(f)
        for a in summary_list:
            if str(a.get("activityId")) == activity_id:
                return a.get("activityType", {}).get("typeKey", "")

    return ""


def _has_drill_laps(data: dict) -> bool:
    """Check if activity has any DRILL laps."""
    for lap in data.get("splits", {}).get("lapDTOs", []):
        if lap.get("swimStroke") == "DRILL":
            return True
    return False


def _bucket_distance(distance: float, margin: float) -> float:
    """Assign a distance to a bucket.

    Uses pool-length-aware bucketing: distances within ±margin of a
    round number are assigned to that bucket. E.g. with margin=75,
    1450-1575 all go to the 1500 bucket.
    """
    # Round to nearest pool-friendly distance (multiples of 50m)
    base = round(distance / 50) * 50
    # Then round to nearest 100 or 500 to get cleaner categories
    # Try candidate targets at multiples of 100
    candidates = [round(distance / 100) * 100]
    # Also try multiples of 50
    candidates.append(round(distance / 50) * 50)
    # Pick the candidate closest to the distance
    best = min(candidates, key=lambda c: abs(distance - c))
    return best


def categorise_activities() -> None:
    """Two-pass categorisation across all activities in the DB.

    Pass 1: classify as interval, open_water, or continuous candidate.
    Pass 2: bucket continuous candidates by distance using tolerance window.
    """
    activities_df = db.get_activities()
    if activities_df.empty:
        return

    continuous_candidates = []  # (activity_id, corrected_distance, pool_length)

    for _, row in activities_df.iterrows():
        activity_id = row["activity_id"]
        raw_data = _load_raw_data(activity_id)

        if raw_data is None:
            db.update_activity_category(activity_id, "other")
            continue

        type_key = _get_activity_type_key(raw_data)

        # Pass 1
        if type_key == "open_water_swimming":
            db.update_activity_category(activity_id, "open_water")
        elif _has_drill_laps(raw_data):
            db.update_activity_category(activity_id, "interval")
        else:
            continuous_candidates.append((activity_id, row["total_distance_m"], row.get("pool_length_m", 25.0)))

    # Pass 2: distance bucketing with tolerance
    if not continuous_candidates:
        return

    margin = DISTANCE_MARGIN

    # Group by nearest "clean" target distance.
    # Try progressively finer multiples (500, 100, 50) and pick the roundest
    # target that is within margin of the distance.
    buckets: dict[float, list[tuple[str, float, float]]] = {}
    for activity_id, dist, pool_length in continuous_candidates:
        target = None
        for step in [500, 100, 50]:
            candidate = round(dist / step) * step
            if abs(dist - candidate) <= margin:
                target = candidate
                break
        if target is None:
            target = dist

        buckets.setdefault(target, []).append((activity_id, dist, pool_length))

    # Determine valid categories
    for bucket_target, items in buckets.items():
        if len(items) >= MIN_CATEGORY_COUNT:
            category = f"{int(bucket_target)}m"
            for activity_id, dist, pool_length in items:
                db.update_activity_category(activity_id, category)
                # Stage 3: extract fastest block if over-distance
                if pool_length and dist > bucket_target:
                    _extract_and_update(activity_id, bucket_target, pool_length)
        else:
            for activity_id, _, _ in items:
                db.update_activity_category(activity_id, "other")


def _load_raw_data(activity_id: str) -> dict | None:
    """Load raw JSON data for an activity."""
    for f in RAW_DIR.glob(f"swim_{activity_id}_*.json"):
        with open(f) as fh:
            return json.load(fh)
    return None


# ---------------------------------------------------------------------------
# Stage 3: Fastest block extraction
# ---------------------------------------------------------------------------

def extract_continuous_block(
    lengths: list[dict], target_distance: float, pool_length: float
) -> list[dict]:
    """Extract the fastest contiguous block of lengths summing to target_distance."""
    n = int(target_distance / pool_length)
    if n <= 0 or n >= len(lengths):
        return lengths

    best_start = 0
    best_duration = float("inf")

    for start in range(len(lengths) - n + 1):
        window = lengths[start : start + n]
        total_dur = sum(l["duration_s"] for l in window)
        if total_dur < best_duration:
            best_duration = total_dur
            best_start = start

    block = [dict(l) for l in lengths[best_start : best_start + n]]
    for i, l in enumerate(block):
        l["length_index"] = i + 1

    return block


def _extract_and_update(activity_id: str, target_distance: float, pool_length: float) -> None:
    """Extract fastest block and update DB."""
    lengths_df = db.get_lengths([activity_id])
    if lengths_df.empty:
        return

    lengths = lengths_df.to_dict("records")
    extracted = extract_continuous_block(lengths, target_distance, pool_length)

    total_dist = sum(l["distance_m"] for l in extracted)
    total_dur = sum(l["duration_s"] for l in extracted)

    db.update_activity_lengths(activity_id, extracted, total_dist, total_dur)


# ---------------------------------------------------------------------------
# Activity processing (Stage 1 applied per activity)
# ---------------------------------------------------------------------------

def _extract_lengths_from_raw(data: dict) -> tuple[list[dict], float]:
    """Extract flat list of active lengths from raw JSON, plus pool length."""
    pool_length = data.get("summary", {}).get("summaryDTO", {}).get("poolLength", 25.0)
    unit = data.get("summary", {}).get("summaryDTO", {}).get("unitOfPoolLength", {})
    # Garmin stores pool length in cm with factor 100
    if unit.get("unitKey") == "meter" and pool_length > 100:
        pool_length = pool_length / 100

    lengths = []
    idx = 1
    for lap in data.get("splits", {}).get("lapDTOs", []):
        for length in lap.get("lengthDTOs", []):
            dist = length.get("distance", 0)
            dur = length.get("duration", 0)
            if dist <= 0 or dur <= 0:
                continue
            lengths.append({
                "length_index": idx,
                "distance_m": dist,
                "duration_s": dur,
                "pace_100m": (dur / dist) * 100,
                "speed_ms": dist / dur,
                "stroke_type": length.get("swimStroke"),
                "stroke_count": length.get("totalNumberOfStrokes"),
                "hr": length.get("averageHR"),
                "is_corrected": False,
            })
            idx += 1

    return lengths, pool_length


def process_activity(filepath: Path) -> tuple[dict, list[dict]] | None:
    """Process a single raw JSON file. Applies missed-length correction.

    Returns (activity_dict, lengths_list) or None on error.
    """
    try:
        with open(filepath) as f:
            data = json.load(f)
    except Exception:
        logger.exception("Failed to read %s", filepath)
        return None

    summary = data.get("summary", {})
    summary_dto = summary.get("summaryDTO", {})
    activity_id = str(summary.get("activityId", ""))
    if not activity_id:
        logger.warning("No activityId in %s", filepath)
        return None

    lengths, pool_length = _extract_lengths_from_raw(data)

    # Stage 1: correct missed lengths
    lengths = correct_missed_lengths(lengths, pool_length)

    total_distance = sum(l["distance_m"] for l in lengths)
    total_duration = sum(l["duration_s"] for l in lengths)

    start_time = summary_dto.get("startTimeLocal", "")
    date = start_time[:10] if start_time else ""

    # Parse time of day
    time_of_day_minutes = 0
    if len(start_time) >= 16:
        try:
            hours = int(start_time[11:13])
            minutes = int(start_time[14:16])
            time_of_day_minutes = hours * 60 + minutes
        except ValueError:
            pass

    activity = {
        "activity_id": activity_id,
        "date": date,
        "start_time_local": start_time,
        "time_of_day_minutes": time_of_day_minutes,
        "category": "other",  # Will be set in categorisation pass
        "total_distance_m": total_distance,
        "total_duration_s": total_duration,
        "raw_distance_m": total_distance,  # Before block extraction
        "avg_pace_100m": (total_duration / total_distance * 100) if total_distance > 0 else 0,
        "avg_speed_ms": (total_distance / total_duration) if total_duration > 0 else 0,
        "avg_hr": summary_dto.get("averageHR"),
        "pool_length_m": pool_length,
        "total_strokes": summary_dto.get("totalNumberOfStrokes"),
        "avg_swolf": summary_dto.get("averageSWOLF"),
    }

    return activity, lengths


def process_all_new(activity_ids: list[str] | None = None) -> int:
    """Process activities and run full categorisation pipeline.

    If activity_ids is None, discovers all raw files.
    Returns count of newly processed activities.
    """
    db.init_db()

    if activity_ids is None:
        files = list(RAW_DIR.glob("swim_*_*.json"))
        # Exclude summary file
        files = [f for f in files if "summary" not in f.name]
    else:
        files = []
        for aid in activity_ids:
            matches = list(RAW_DIR.glob(f"swim_{aid}_*.json"))
            files.extend(matches)

    count = 0
    for filepath in files:
        # Extract activity_id from filename
        parts = filepath.stem.split("_")
        if len(parts) < 2:
            continue
        aid = parts[1]

        if db.activity_exists(aid):
            continue

        result = process_activity(filepath)
        if result is None:
            continue

        activity, lengths = result
        db.insert_activity(activity, lengths)
        count += 1
        logger.info("Processed %s (%s)", aid, activity["date"])

    # Run categorisation across all activities
    categorise_activities()

    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = process_all_new()
    print(f"Processed {n} activities.")

    df = db.get_activities()
    print(df[["date", "category", "total_distance_m", "raw_distance_m", "avg_pace_100m"]].to_string())
    print()
    print(df.groupby("category").size())
