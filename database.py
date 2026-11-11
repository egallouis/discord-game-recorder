"""Shared result persistence and current-schema initialization."""

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

from games.base import Result
from periods import Period

db_path = "database.db"
SCHEMA_VERSION = 1

_UPSERT_RESULT = """
    INSERT INTO RESULTS (
        game_key, author_id, name, puzzle_id, puzzle_date, payload_json,
        message_id, posted_at, raw_content
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(game_key, author_id, puzzle_id) DO UPDATE SET
        name = excluded.name,
        puzzle_date = excluded.puzzle_date,
        payload_json = excluded.payload_json,
        message_id = excluded.message_id,
        posted_at = excluded.posted_at,
        raw_content = excluded.raw_content
    WHERE
        (excluded.posted_at IS NOT NULL AND (
            RESULTS.posted_at IS NULL OR excluded.posted_at > RESULTS.posted_at OR (
                excluded.posted_at = RESULTS.posted_at AND
                COALESCE(CAST(excluded.message_id AS INTEGER), -1) >=
                COALESCE(CAST(RESULTS.message_id AS INTEGER), -1)
            )
        )) OR (excluded.posted_at IS NULL AND RESULTS.posted_at IS NULL)
"""


def _result_values(result: Result) -> tuple:
    posted_at = result.posted_at
    if posted_at is not None:
        if posted_at.tzinfo is None or posted_at.utcoffset() is None:
            raise ValueError("Result posting timestamps must include a timezone")
        posted_at = posted_at.astimezone(UTC).isoformat(timespec="microseconds")
    return (
        result.game_key,
        str(result.author_id),
        result.name,
        str(result.puzzle_id),
        result.puzzle_date.isoformat(),
        json.dumps(result.payload, ensure_ascii=False, allow_nan=False),
        result.message_id,
        posted_at,
        result.raw_content,
    )


def _initialize(con: sqlite3.Connection) -> None:
    """Open the supported schema, or create it in an empty database."""
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema version {version} is newer than this application supports"
        )
    if version == SCHEMA_VERSION:
        return

    con.execute("BEGIN IMMEDIATE")
    try:
        # Another process may have initialized while we waited for the lock.
        version = con.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            con.commit()
            return
        if version != 0:
            raise RuntimeError(f"Unsupported database schema version: {version}")
        if con.execute(
            "SELECT 1 FROM sqlite_master WHERE name NOT GLOB 'sqlite_*' LIMIT 1"
        ).fetchone():
            raise RuntimeError(
                "Unsupported database schema: unversioned database is not empty. "
                f"Use a schema version {SCHEMA_VERSION} database or a new database path."
            )
        con.execute("""
            CREATE TABLE RESULTS (
                game_key TEXT NOT NULL,
                author_id TEXT NOT NULL,
                name TEXT NOT NULL,
                puzzle_id TEXT NOT NULL,
                puzzle_date TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                message_id TEXT,
                posted_at TEXT,
                raw_content TEXT,
                PRIMARY KEY (game_key, author_id, puzzle_id)
            )
        """)
        con.execute("CREATE INDEX results_by_period ON RESULTS(game_key, puzzle_date)")
        con.execute("""
            CREATE TABLE GAME_BANS (
                game_key TEXT NOT NULL,
                author_id TEXT NOT NULL,
                puzzle_id TEXT NOT NULL,
                PRIMARY KEY (game_key, author_id, puzzle_id)
            )
        """)
        con.execute("""
            CREATE TABLE PLAYERS (
                author_id TEXT NOT NULL PRIMARY KEY,
                name TEXT NOT NULL
            )
        """)
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        con.commit()
    except Exception:
        con.rollback()
        raise


def get_connection(path: str | Path | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path if path is None else path))
    con.row_factory = sqlite3.Row
    try:
        _initialize(con)
    except Exception:
        con.close()
        raise
    return con


def save_results(results: Iterable[Result], path: str | Path | None = None) -> None:
    """Upsert atomically; older exports cannot replace newer submissions."""
    with closing(get_connection(path)) as con, con:
        for result in results:
            cursor = con.execute(_UPSERT_RESULT, _result_values(result))
            if cursor.rowcount:
                con.execute(
                    """
                    INSERT INTO PLAYERS(author_id, name) VALUES (?, ?)
                    ON CONFLICT(author_id) DO UPDATE SET name = excluded.name
                """,
                    (str(result.author_id), result.name),
                )


def get_results(
    game_key: str = "wordle",
    period: Period | None = None,
    path: str | Path | None = None,
    *,
    include_banned: bool = False,
) -> list[Result]:
    query = "SELECT r.* FROM RESULTS r WHERE r.game_key = ?"
    parameters = [game_key]
    if period is not None:
        query += " AND r.puzzle_date >= ? AND r.puzzle_date < ?"
        parameters.extend([period.start.isoformat(), period.end.isoformat()])
    if not include_banned:
        query += """ AND NOT EXISTS (
            SELECT 1 FROM GAME_BANS b WHERE b.game_key = r.game_key
            AND b.author_id = r.author_id AND b.puzzle_id = r.puzzle_id
        )"""
    query += " ORDER BY r.puzzle_date, r.author_id, r.puzzle_id"
    with closing(get_connection(path)) as con:
        rows = con.execute(query, parameters).fetchall()
    return [
        Result(
            game_key=row["game_key"],
            author_id=row["author_id"],
            name=row["name"],
            puzzle_id=row["puzzle_id"],
            puzzle_date=date.fromisoformat(row["puzzle_date"]),
            payload=json.loads(row["payload_json"]),
            message_id=row["message_id"],
            posted_at=datetime.fromisoformat(row["posted_at"])
            if row["posted_at"]
            else None,
            raw_content=row["raw_content"],
        )
        for row in rows
    ]
