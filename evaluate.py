"""Load a game's results and print the report supplied by its implementation."""

from database import get_results
from games import get_game
from periods import Period


def generate_period_message(period: Period, game_key: str = "wordle") -> str:
    game = get_game(game_key)
    message = game.build_period_message(get_results(game.key, period), period)
    print(message)
    return message
