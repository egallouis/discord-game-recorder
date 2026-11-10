"""Explicit registry: add a Game implementation here to enable another game."""

from games.base import Game, ParsedResult, PlayerScore, Result
from games.wordle import Wordle


GAMES: dict[str, Game] = {"wordle": Wordle()}


def get_game(key: str = "wordle") -> Game:
    try:
        return GAMES[key]
    except KeyError:
        raise ValueError(f"Unknown game: {key}. Available games: {', '.join(GAMES)}") from None


__all__ = ["GAMES", "Game", "ParsedResult", "PlayerScore", "Result", "Wordle", "get_game"]
