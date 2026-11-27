"""SQLite-хранилище: пользователи, путешествия, расходы, справочник стран."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime

from countries import ALIASES, COUNTRIES


def normalize(text: str) -> str:
    return " ".join(text.strip().lower().replace("ё", "е").split())


class TravelWalletDB:
    def __init__(self, path: str = "travel_wallet.db"):
        self.path = path
        self._init_schema()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self):
        with self.get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    active_trip_id INTEGER,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trips (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(user_id),
                    home_country TEXT NOT NULL,
                    home_currency TEXT NOT NULL,
                    dest_country TEXT NOT NULL,
                    dest_currency TEXT NOT NULL,
                    rate REAL NOT NULL,
                    initial_amount REAL NOT NULL,
                    balance_home REAL NOT NULL,
                    balance_dest REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
                    amount_dest REAL NOT NULL,
                    amount_home REAL NOT NULL,
                    rate REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS countries_currencies (
                    country TEXT PRIMARY KEY,
                    country_key TEXT NOT NULL UNIQUE,
                    currency TEXT NOT NULL,
                    currency_name TEXT NOT NULL
                );
                """
            )
            count = conn.execute("SELECT COUNT(*) FROM countries_currencies").fetchone()[0]
            if count == 0:
                conn.executemany(
                    "INSERT INTO countries_currencies (country, country_key, currency, currency_name) "
                    "VALUES (?, ?, ?, ?)",
                    [(name, normalize(name), code, title) for name, code, title in COUNTRIES],
                )

    # --- справочник стран ---

    def find_country(self, text: str) -> dict | None:
        """Ищет страну по названию, синониму или коду валюты (EUR)."""
        key = normalize(text)
        key = normalize(ALIASES.get(key, key))
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT country, currency, currency_name FROM countries_currencies WHERE country_key = ?",
                (key,),
            ).fetchone()
            if row is None and len(key) == 3 and key.isalpha():
                row = conn.execute(
                    "SELECT country, currency, currency_name FROM countries_currencies "
                    "WHERE currency = ? ORDER BY rowid LIMIT 1",
                    (key.upper(),),
                ).fetchone()
        return dict(row) if row else None

    # --- пользователи и путешествия ---

    def get_or_create_user(self, user_id: int) -> dict:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if row:
                return dict(row)
            conn.execute(
                "INSERT INTO users (user_id, active_trip_id, created_at) VALUES (?, NULL, ?)",
                (user_id, _now()),
            )
        return {"user_id": user_id, "active_trip_id": None}

    def create_trip(self, user_id, home_country, home_currency, dest_country, dest_currency,
                    rate, initial_amount, initial_dest) -> int:
        self.get_or_create_user(user_id)
        with self.get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO trips (user_id, home_country, home_currency, dest_country, dest_currency, "
                "rate, initial_amount, balance_home, balance_dest, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, home_country, home_currency, dest_country, dest_currency,
                 rate, initial_amount, initial_amount, initial_dest, _now()),
            )
            trip_id = cur.lastrowid
            conn.execute("UPDATE users SET active_trip_id = ? WHERE user_id = ?", (trip_id, user_id))
        return trip_id

    def get_trips(self, user_id: int) -> list[dict]:
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM trips WHERE user_id = ? ORDER BY id", (user_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_trip(self, user_id: int, trip_id: int) -> dict | None:
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM trips WHERE id = ? AND user_id = ?", (trip_id, user_id)
            ).fetchone()
        return dict(row) if row else None

    def get_active_trip(self, user_id: int) -> dict | None:
        user = self.get_or_create_user(user_id)
        if not user.get("active_trip_id"):
            return None
        return self.get_trip(user_id, user["active_trip_id"])

    def set_active_trip(self, user_id: int, trip_id: int) -> bool:
        if not self.get_trip(user_id, trip_id):
            return False
        with self.get_connection() as conn:
            conn.execute("UPDATE users SET active_trip_id = ? WHERE user_id = ?", (trip_id, user_id))
        return True

    def update_rate(self, user_id: int, trip_id: int, rate: float) -> None:
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE trips SET rate = ? WHERE id = ? AND user_id = ?", (rate, trip_id, user_id)
            )

    def add_expense(self, user_id: int, trip_id: int, amount_dest: float, amount_home: float,
                    rate: float) -> dict:
        with self.get_connection() as conn:
            conn.execute(
                "INSERT INTO expenses (trip_id, amount_dest, amount_home, rate, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (trip_id, amount_dest, amount_home, rate, _now()),
            )
            conn.execute(
                "UPDATE trips SET balance_dest = balance_dest - ?, balance_home = balance_home - ? "
                "WHERE id = ? AND user_id = ?",
                (amount_dest, amount_home, trip_id, user_id),
            )
        return self.get_trip(user_id, trip_id)

    def get_expenses(self, trip_id: int, limit: int = 10) -> list[dict]:
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM expenses WHERE trip_id = ? ORDER BY id DESC LIMIT ?",
                (trip_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
