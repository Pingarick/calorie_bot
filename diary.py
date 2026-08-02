"""Calorie diary backed by SQLite.

Stores each meal entry per Telegram user so we can show history and daily
totals (today / this week).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path

log = logging.getLogger("caloriebot.diary")

_DB = Path(__file__).with_name("diary.db")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    """Create the tables if they do not exist."""
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                created_at  TEXT NOT NULL,          -- ISO datetime
                dishes      TEXT NOT NULL,          -- JSON array of dishes
                note        TEXT,
                total_kcal  INTEGER,
                total_protein_g REAL,
                total_fat_g     REAL,
                total_carbs_g   REAL
            )
            """
        )
        # Per-user settings (currently just the daily kcal target).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                user_id     INTEGER PRIMARY KEY,
                daily_kcal  INTEGER
            )
            """
        )


def add_meal(
    user_id: int,
    dishes: list[dict],
    note: str | None,
) -> None:
    """Insert a meal with computed totals."""
    total_kcal = round(sum(float(d.get("kcal", 0)) for d in dishes))
    total_p = round(sum(float(d.get("protein_g", 0)) for d in dishes))
    total_f = round(sum(float(d.get("fat_g", 0)) for d in dishes))
    total_c = round(sum(float(d.get("carbs_g", 0)) for d in dishes))
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO meals
                (user_id, created_at, dishes, note,
                 total_kcal, total_protein_g, total_fat_g, total_carbs_g)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                datetime.now().isoformat(timespec="seconds"),
                json.dumps(dishes, ensure_ascii=False),
                note,
                total_kcal,
                total_p,
                total_f,
                total_c,
            ),
        )


def today_total(user_id: int) -> int:
    """Sum of kcal logged by this user today."""
    start = datetime.combine(date.today(), time.min).isoformat()
    with _conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(total_kcal), 0) AS s FROM meals "
            "WHERE user_id = ? AND created_at >= ?",
            (user_id, start),
        ).fetchone()
    return int(row["s"])


def week_total(user_id: int) -> int:
    """Sum of kcal logged by this user in the last 7 days."""
    since = (datetime.now() - timedelta(days=7)).isoformat()
    with _conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(total_kcal), 0) AS s FROM meals "
            "WHERE user_id = ? AND created_at >= ?",
            (user_id, since),
        ).fetchone()
    return int(row["s"])


def recent(user_id: int, limit: int = 5) -> list[sqlite3.Row]:
    """Most recent meals for this user, newest first."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT created_at, dishes, total_kcal FROM meals "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return rows


def delete_last(user_id: int) -> dict | None:
    """Delete the user's most recent meal and return what was removed, or None."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, total_kcal, created_at FROM meals "
            "WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM meals WHERE id = ?", (row["id"],))
    return {
        "kcal": row["total_kcal"],
        "created_at": row["created_at"].replace("T", " ")[:16],
    }


def set_target(user_id: int, kcal: int) -> None:
    """Set (or remove, if kcal is 0/None) the user's daily kcal target."""
    with _conn() as conn:
        if kcal:
            conn.execute(
                "INSERT INTO settings (user_id, daily_kcal) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET daily_kcal = excluded.daily_kcal",
                (user_id, kcal),
            )
        else:
            conn.execute("DELETE FROM settings WHERE user_id = ?", (user_id,))


def get_target(user_id: int) -> int | None:
    """Return the user's daily kcal target, or None if not set."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT daily_kcal FROM settings WHERE user_id = ?", (user_id,)
        ).fetchone()
    return int(row["daily_kcal"]) if row else None


def _day_totals(user_id: int, days: int) -> list[int]:
    """Kcal totals per day for the last ``days`` days, oldest first."""
    start = (date.today() - timedelta(days=days - 1))
    since = datetime.combine(start, time.min).isoformat()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT created_at, total_kcal FROM meals "
            "WHERE user_id = ? AND created_at >= ?",
            (user_id, since),
        ).fetchall()
    # Bucket by calendar day.
    totals = {i: 0 for i in range(days)}
    for r in rows:
        idx = (datetime.fromisoformat(r["created_at"]).date() - start).days
        if 0 <= idx < days:
            totals[idx] += int(r["total_kcal"] or 0)
    return [totals[i] for i in range(days)]
