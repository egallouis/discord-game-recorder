"""Command-line entry point for discord-game-recorder."""

import argparse

from ban_list import add_to_ban_list, remove_from_ban_list, show_ban_list
from evaluate import generate_period_message
from fetch import fetch_period
from games import GAMES
from periods import Period


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="discord-game-recorder",
        description="Read game results from Discord and generate leaderboards",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser(
        "fetch", help="Fetch messages and read all registered games"
    )
    fetch_parser.add_argument(
        "date", nargs="+", type=int, metavar="DATE", help="YEAR [MONTH]"
    )

    eval_parser = subparsers.add_parser("eval", help="Evaluate one game's results")
    eval_parser.add_argument(
        "date", nargs="+", type=int, metavar="DATE", help="YEAR [MONTH]"
    )
    eval_parser.add_argument("--game", choices=list(GAMES), default="wordle")

    ban_parser = subparsers.add_parser("ban_list", help="Manage excluded results")
    bans = ban_parser.add_subparsers(dest="subcommand", required=True)
    for action in ("add", "remove"):
        command_parser = bans.add_parser(action)
        command_parser.add_argument("author_id")
        command_parser.add_argument("game_id", help="Puzzle identifier")
        command_parser.add_argument("--game", choices=list(GAMES), default="wordle")
    show_parser = bans.add_parser("show")
    show_parser.add_argument("--game", choices=list(GAMES), default="wordle")

    args = parser.parse_args(argv)
    if args.command in ("fetch", "eval"):
        if len(args.date) not in (1, 2):
            parser.error("Specify YEAR and optionally MONTH")
        try:
            period = Period(args.date[0], args.date[1] if len(args.date) == 2 else None)
        except (ValueError, OverflowError) as error:
            parser.error(f"Invalid evaluation period: {error}")
        if args.command == "fetch":
            fetch_period(period)
        else:
            generate_period_message(period, args.game)
    elif args.subcommand == "add":
        add_to_ban_list(args.author_id, args.game_id, args.game)
    elif args.subcommand == "remove":
        remove_from_ban_list(args.author_id, args.game_id, args.game)
    else:
        show_ban_list(args.game)


if __name__ == "__main__":
    main()
