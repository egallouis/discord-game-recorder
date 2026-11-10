"""Discord export and message dispatch shared by every registered game."""

import json
import logging
import subprocess
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from database import save_results
from games import GAMES, Game, Result
from periods import Period


logger = logging.getLogger(__name__)


def parse_messages(messages: Iterable[dict], games: Iterable[Game] | None = None) -> list[Result]:
    parsers = tuple(GAMES.values() if games is None else games)
    results = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, str):
            continue
        matches = []
        for game in parsers:
            try:
                parsed = game.parse(content)
            except (ValueError, TypeError, OverflowError):
                logger.warning("Could not parse message %s as %s", message.get("id"), game.key)
                continue
            if parsed is not None:
                matches.append((game, parsed))
        if len(matches) > 1:
            logger.warning("Ambiguous game result in message %s; skipping", message.get("id"))
            continue
        if not matches:
            continue
        game, parsed = matches[0]
        try:
            author = message["author"]
            author_id = str(author["id"])
            message_id = str(message["id"])
            if not author.get("id") or not message_id.isdecimal():
                raise ValueError("Missing Discord identity")
            posted_at = datetime.fromisoformat(message["timestamp"])
            if posted_at.tzinfo is None or posted_at.utcoffset() is None:
                raise ValueError("Missing timestamp timezone")
            name = author.get("nickname") or author.get("name") or author_id
            results.append(Result(
                game_key=game.key, author_id=author_id, name=name,
                puzzle_id=parsed.puzzle_id, puzzle_date=parsed.puzzle_date, payload=parsed.payload,
                message_id=message_id, posted_at=posted_at.astimezone(UTC), raw_content=content,
            ))
        except (KeyError, ValueError, TypeError):
            logger.warning("Missing or invalid source metadata in message %s; skipping", message.get("id"))
    return results


def export_messages(period: Period, config_path: str | Path = ".env") -> list[dict]:
    with open(config_path, encoding="utf-8-sig") as config:
        lines = config.read().splitlines()
    if len(lines) != 3 or not all(line.strip() for line in lines):
        raise ValueError("The .env file must contain token, channel ID, and exporter path on three lines")
    token, channel, location = (line.strip() for line in lines)
    location = location.strip('"')

    with tempfile.TemporaryDirectory(prefix="discord-game-recorder-export-") as directory:
        output_path = Path(directory) / "messages.json"
        command = [
            location, "export", "-t", token, "-c", channel,
            "--after", period.fetch_start.isoformat(),
            "--before", period.fetch_end.isoformat(),
            "-f", "Json", "-o", str(output_path), "--fuck-russia",
        ]
        try:
            completed = subprocess.run(command, capture_output=True, check=False)
        except OSError:
            raise RuntimeError("Could not start DiscordChatExporter; check its path in .env") from None
        if completed.returncode:
            raise RuntimeError(f"Discord export failed with exit code {completed.returncode}")
        if not output_path.is_file():
            raise RuntimeError("DiscordChatExporter did not produce a message export")
        with output_path.open(encoding="utf-8-sig") as source:
            return json.load(source)["messages"]


def fetch_period(period: Period) -> int:
    now = datetime.now(UTC)
    if period.fetch_start > now:
        print("Cannot fetch data from the future")
        return 0
    if period.fetch_end > now:
        print("Fetching partial data from the current period")
    print(f"Fetching game data from {period.fetch_start} to {period.fetch_end}")
    results = parse_messages(export_messages(period))
    save_results(results)
    print(f"Read {len(results)} game result(s)")
    return len(results)
