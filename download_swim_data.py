"""Download swimming activity data from Garmin Connect."""

import json
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from garminconnect import Garmin

load_dotenv()

TOKENS_DIR = Path("tokens")
DATA_DIR = Path("data")
TOKENS_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)


def get_client() -> Garmin:
    """Authenticate and return a Garmin client, caching tokens for reuse."""
    tokenstore = str(TOKENS_DIR)

    # Try loading saved tokens first
    if (TOKENS_DIR / "oauth1_token.json").exists():
        print("Loading saved tokens...")
        client = Garmin()
        client.login(tokenstore=tokenstore)
        print("Authenticated from saved tokens.")
        return client

    # Fresh login with credentials
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    if not email or not password or "example.com" in email:
        raise SystemExit(
            "Set GARMIN_EMAIL and GARMIN_PASSWORD in .env with your real credentials."
        )

    print(f"Logging in as {email}...")
    client = Garmin(email=email, password=password)
    client.login()
    client.garth.dump(tokenstore)
    print("Authenticated and tokens saved.")
    return client


def download_swimming_activities(client: Garmin, max_activities: int = 50):
    """Fetch swimming activities and save detailed data."""
    print(f"\nFetching up to {max_activities} swimming activities...")

    # activityType "lap_swimming" and "open_water_swimming" are the main swim types
    activities = client.get_activities(0, max_activities, activitytype="swimming")

    if not activities:
        print("No swimming activities found.")
        return

    print(f"Found {len(activities)} swimming activities.\n")

    # Save activity list summary
    summary_path = DATA_DIR / "swim_activities_summary.json"
    with open(summary_path, "w") as f:
        json.dump(activities, f, indent=2, default=str)
    print(f"Saved activity summary to {summary_path}")

    # Print a quick overview
    print(f"\n{'Date':<22} {'Type':<22} {'Distance (m)':<14} {'Duration':<12} {'Strokes'}")
    print("-" * 85)

    for act in activities:
        date = act.get("startTimeLocal", "N/A")
        act_type = act.get("activityType", {}).get("typeKey", "unknown")
        distance = act.get("distance", 0)
        duration_secs = act.get("duration", 0)
        strokes = act.get("averageSwimCadenceInStrokesPerMinute", "N/A")

        mins, secs = divmod(int(duration_secs), 60)
        hours, mins = divmod(mins, 60)
        duration_str = f"{hours}h {mins:02d}m {secs:02d}s" if hours else f"{mins}m {secs:02d}s"

        print(f"{date:<22} {act_type:<22} {distance:<14.0f} {duration_str:<12} {strokes}")

    # Download detailed data for the most recent 5 activities
    detail_count = min(5, len(activities))
    print(f"\nDownloading detailed data for {detail_count} most recent activities...")

    for act in activities[:detail_count]:
        activity_id = str(act["activityId"])
        act_date = act.get("startTimeLocal", "unknown")[:10]
        print(f"\n  Activity {activity_id} ({act_date}):")

        detail = {}

        # Activity summary
        summary = client.get_activity(activity_id)
        detail["summary"] = summary
        print("    - Summary fetched")

        # Splits (lap-by-lap data)
        splits = client.get_activity_splits(activity_id)
        detail["splits"] = splits
        print("    - Splits fetched")

        # Split summaries
        split_summaries = client.get_activity_split_summaries(activity_id)
        detail["split_summaries"] = split_summaries
        print("    - Split summaries fetched")

        # Detailed metrics (HR, stroke data, etc.)
        details = client.get_activity_details(activity_id)
        detail["details"] = details
        print("    - Details fetched")

        # Heart rate zones
        try:
            hr_zones = client.get_activity_hr_in_timezones(activity_id)
            detail["hr_zones"] = hr_zones
            print("    - HR zones fetched")
        except Exception:
            print("    - HR zones not available")

        # Save per-activity detail
        detail_path = DATA_DIR / f"swim_{activity_id}_{act_date}.json"
        with open(detail_path, "w") as f:
            json.dump(detail, f, indent=2, default=str)
        print(f"    -> Saved to {detail_path}")

    print("\nDone! All data saved to the data/ directory.")


if __name__ == "__main__":
    client = get_client()
    download_swimming_activities(client)
