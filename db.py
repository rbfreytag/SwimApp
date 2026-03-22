"""SQLite database schema and helpers for SwimApp."""

import sqlite3
from pathlib import Path

import pandas as pd

DB_PATH = Path("data/swim.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    activity_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    start_time_local TEXT NOT NULL,
    time_of_day_minutes INTEGER NOT NULL,
    category TEXT NOT NULL DEFAULT 'other',
    total_distance_m REAL NOT NULL,
    total_duration_s REAL NOT NULL,
    raw_distance_m REAL NOT NULL,
    avg_pace_100m REAL NOT NULL,
    avg_speed_ms REAL NOT NULL,
    avg_hr REAL,
    pool_length_m REAL,
    total_strokes INTEGER,
    avg_swolf REAL
);

CREATE TABLE IF NOT EXISTS lengths (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id TEXT NOT NULL REFERENCES activities(activity_id),
    length_index INTEGER NOT NULL,
    distance_m REAL NOT NULL,
    duration_s REAL NOT NULL,
    pace_100m REAL NOT NULL,
    speed_ms REAL NOT NULL,
    stroke_type TEXT,
    stroke_count INTEGER,
    hr REAL,
    is_corrected BOOLEAN NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_lengths_activity ON lengths(activity_id);
CREATE INDEX IF NOT EXISTS idx_activities_category ON activities(category);
CREATE INDEX IF NOT EXISTS idx_activities_date ON activities(date);
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(SCHEMA)


def activity_exists(activity_id: str) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM activities WHERE activity_id = ?", (activity_id,)
        ).fetchone()
        return row is not None


def insert_activity(activity: dict, lengths: list[dict]) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO activities
            (activity_id, date, start_time_local, time_of_day_minutes,
             category, total_distance_m, total_duration_s, raw_distance_m,
             avg_pace_100m, avg_speed_ms, avg_hr, pool_length_m,
             total_strokes, avg_swolf)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                activity["activity_id"],
                activity["date"],
                activity["start_time_local"],
                activity["time_of_day_minutes"],
                activity.get("category", "other"),
                activity["total_distance_m"],
                activity["total_duration_s"],
                activity["raw_distance_m"],
                activity["avg_pace_100m"],
                activity["avg_speed_ms"],
                activity.get("avg_hr"),
                activity.get("pool_length_m"),
                activity.get("total_strokes"),
                activity.get("avg_swolf"),
            ),
        )
        # Clear existing lengths for this activity (for re-processing)
        conn.execute(
            "DELETE FROM lengths WHERE activity_id = ?",
            (activity["activity_id"],),
        )
        conn.executemany(
            """INSERT INTO lengths
            (activity_id, length_index, distance_m, duration_s,
             pace_100m, speed_ms, stroke_type, stroke_count, hr, is_corrected)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    activity["activity_id"],
                    l["length_index"],
                    l["distance_m"],
                    l["duration_s"],
                    l["pace_100m"],
                    l["speed_ms"],
                    l.get("stroke_type"),
                    l.get("stroke_count"),
                    l.get("hr"),
                    l.get("is_corrected", False),
                )
                for l in lengths
            ],
        )


def update_activity_category(activity_id: str, category: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE activities SET category = ? WHERE activity_id = ?",
            (category, activity_id),
        )


def update_activity_lengths(
    activity_id: str, lengths: list[dict], total_distance_m: float, total_duration_s: float
) -> None:
    """Replace an activity's lengths and update its stats (after block extraction)."""
    avg_pace = (total_duration_s / total_distance_m) * 100 if total_distance_m > 0 else 0
    avg_speed = total_distance_m / total_duration_s if total_duration_s > 0 else 0

    with _conn() as conn:
        conn.execute(
            """UPDATE activities
            SET total_distance_m = ?, total_duration_s = ?,
                avg_pace_100m = ?, avg_speed_ms = ?
            WHERE activity_id = ?""",
            (total_distance_m, total_duration_s, avg_pace, avg_speed, activity_id),
        )
        conn.execute("DELETE FROM lengths WHERE activity_id = ?", (activity_id,))
        conn.executemany(
            """INSERT INTO lengths
            (activity_id, length_index, distance_m, duration_s,
             pace_100m, speed_ms, stroke_type, stroke_count, hr, is_corrected)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    activity_id,
                    l["length_index"],
                    l["distance_m"],
                    l["duration_s"],
                    l["pace_100m"],
                    l["speed_ms"],
                    l.get("stroke_type"),
                    l.get("stroke_count"),
                    l.get("hr"),
                    l.get("is_corrected", False),
                )
                for l in lengths
            ],
        )


def get_activities(
    category: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    clauses = []
    params: list = []
    if category:
        clauses.append("category = ?")
        params.append(category)
    if start_date:
        clauses.append("date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("date <= ?")
        params.append(end_date)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"SELECT * FROM activities {where} ORDER BY date"

    with _conn() as conn:
        return pd.read_sql_query(query, conn, params=params)


def get_lengths(activity_ids: list[str]) -> pd.DataFrame:
    if not activity_ids:
        with _conn() as conn:
            return pd.read_sql_query(
                "SELECT * FROM lengths ORDER BY activity_id, length_index", conn
            )
    placeholders = ",".join("?" for _ in activity_ids)
    query = f"SELECT * FROM lengths WHERE activity_id IN ({placeholders}) ORDER BY activity_id, length_index"
    with _conn() as conn:
        return pd.read_sql_query(query, conn, params=activity_ids)


def get_categories() -> list[str]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT category FROM activities ORDER BY category"
        ).fetchall()
        return [r[0] for r in rows]


def get_activity_dates(category: str | None = None) -> list[str]:
    if category:
        query = "SELECT DISTINCT date FROM activities WHERE category = ? ORDER BY date"
        params = [category]
    else:
        query = "SELECT DISTINCT date FROM activities ORDER BY date"
        params = []
    with _conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [r[0] for r in rows]
