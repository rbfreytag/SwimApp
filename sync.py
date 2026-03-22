"""Sync swimming activity data from Garmin Connect."""

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from garminconnect import Garmin

load_dotenv()

logger = logging.getLogger(__name__)

TOKENS_DIR = Path("tokens")
RAW_DIR = Path("data/raw")
TOKENS_DIR.mkdir(exist_ok=True)
RAW_DIR.mkdir(parents=True, exist_ok=True)


def _get_client() -> Garmin:
    """Authenticate and return a Garmin client, caching tokens for reuse."""
    tokenstore = str(TOKENS_DIR)

    if (TOKENS_DIR / "oauth1_token.json").exists():
        logger.info("Loading saved tokens...")
        client = Garmin()
        client.login(tokenstore=tokenstore)
        return client

    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    if not email or not password or "example.com" in email:
        raise SystemExit(
            "Set GARMIN_EMAIL and GARMIN_PASSWORD in .env with your real credentials."
        )

    logger.info("Logging in as %s...", email)
    client = Garmin(email=email, password=password)
    client.login()
    client.garth.dump(tokenstore)
    logger.info("Authenticated and tokens saved.")
    return client


def _existing_activity_ids() -> set[str]:
    """Return set of activity IDs already downloaded as raw JSON."""
    ids = set()
    for f in RAW_DIR.glob("swim_*_*.json"):
        parts = f.stem.split("_")
        if len(parts) >= 2:
            ids.add(parts[1])
    return ids


def sync_activities(max_activities: int = 1000) -> list[str]:
    """Download any missing swimming activities from Garmin Connect.

    Returns list of newly downloaded activity IDs.
    """
    client = _get_client()

    logger.info("Fetching activity list from Garmin...")
    activities = client.get_activities(0, max_activities, activitytype="swimming")

    if not activities:
        logger.info("No swimming activities found on Garmin.")
        return []

    logger.info("Found %d swimming activities on Garmin.", len(activities))

    existing = _existing_activity_ids()
    new_ids = []

    for act in activities:
        activity_id = str(act["activityId"])
        if activity_id in existing:
            continue

        act_date = act.get("startTimeLocal", "unknown")[:10]
        logger.info("Downloading activity %s (%s)...", activity_id, act_date)

        try:
            detail = {"activity_list_entry": act}
            detail["summary"] = client.get_activity(activity_id)
            detail["splits"] = client.get_activity_splits(activity_id)
            detail["split_summaries"] = client.get_activity_split_summaries(activity_id)
            detail["details"] = client.get_activity_details(activity_id)
            try:
                detail["hr_zones"] = client.get_activity_hr_in_timezones(activity_id)
            except Exception:
                pass

            filepath = RAW_DIR / f"swim_{activity_id}_{act_date}.json"
            with open(filepath, "w") as f:
                json.dump(detail, f, indent=2, default=str)

            new_ids.append(activity_id)
            logger.info("  Saved %s", filepath)

        except Exception:
            logger.exception("Failed to download activity %s", activity_id)
            continue

    logger.info("Synced %d new activities.", len(new_ids))
    return new_ids


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ids = sync_activities()
    print(f"Synced {len(ids)} new activities.")
