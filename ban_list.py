"""Bans exclude one player's result for one game and puzzle."""

from contextlib import closing

import polars as pl

from database import get_connection


def add_to_ban_list(author_id: str, game_id: int | str, game_key: str = "wordle"):
    with closing(get_connection()) as con, con:
        con.execute(
            "INSERT OR IGNORE INTO GAME_BANS(game_key, author_id, puzzle_id) VALUES (?, ?, ?)",
            (game_key, str(author_id), str(game_id)),
        )


def remove_from_ban_list(author_id, game_id, game_key: str = "wordle"):
    with closing(get_connection()) as con, con:
        con.execute(
            "DELETE FROM GAME_BANS WHERE game_key = ? AND author_id = ? AND puzzle_id = ?",
            (game_key, str(author_id), str(game_id)),
        )


def show_ban_list(game_key: str = "wordle"):
    with closing(get_connection()) as con:
        rows = con.execute(
            "SELECT game_key, author_id, puzzle_id FROM GAME_BANS WHERE game_key = ? ORDER BY author_id, puzzle_id",
            (game_key,),
        ).fetchall()
    frame = pl.DataFrame([tuple(row) for row in rows], orient="row", schema={
        "game_key": pl.String, "author_id": pl.String, "puzzle_id": pl.String,
    })
    print(frame)
    return frame
