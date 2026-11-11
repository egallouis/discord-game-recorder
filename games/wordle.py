"""Wordle parsing, puzzle dates, scoring, and optional report statistics."""

import json
import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from functools import lru_cache
from urllib.request import urlopen

import numpy as np

from games.base import Game, ParsedResult, PlayerScore, Result
from periods import Period


class Wordle(Game):
    key = "wordle"
    display_name = "Wordle"

    _day_zero = date(2021, 6, 19)
    _header = re.compile(r"\AWordle ([0-9][0-9,.]*) ([1-7Xx])/6(\*?)[ \t]*\n")
    _colors = str.maketrans(
        {
            "\u2b1b": "0",
            "\u2b1c": "0",
            "\U0001f7e8": "1",
            "\U0001f7e6": "1",
            "\U0001f7e9": "2",
            "\U0001f7e7": "2",
            "\ufe0f": None,
        }
    )

    @classmethod
    def puzzle_id_to_date(cls, puzzle_id: int | str) -> date:
        return cls._day_zero + timedelta(days=int(puzzle_id))

    @classmethod
    def date_to_puzzle_id(cls, value: date | datetime) -> int:
        if isinstance(value, datetime):
            value = value.date()
        return (value - cls._day_zero).days

    @staticmethod
    @lru_cache(maxsize=1024)
    def get_solution_from_date(value: date) -> str:
        url = f"https://www.nytimes.com/svc/wordle/v2/{value:%Y-%m-%d}.json"
        with urlopen(url, timeout=15) as response:
            return json.load(response)["solution"]

    @classmethod
    def get_solution_from_puzzle_id(cls, puzzle_id: int | str) -> str:
        return cls.get_solution_from_date(cls.puzzle_id_to_date(puzzle_id))

    @staticmethod
    def check_hardmode(data: np.ndarray) -> bool:
        """Check whether correct-position tile counts never decrease."""
        greens_per_line = (data == 2).astype(int).sum(1)
        return np.all(greens_per_line[:-1] <= greens_per_line[1:])

    def parse(self, text: str) -> ParsedResult | None:
        normalized = text.replace("\r\n", "\n").strip().translate(self._colors)
        match = self._header.match(normalized)
        if match is None:
            return None

        puzzle_id, tries_text, hardmode = match.groups()
        tries = 7 if tries_text.upper() == "X" else int(tries_text)
        rows = []
        for line in normalized[match.end() :].splitlines():
            line = line.strip()
            if not line and not rows:
                continue
            if not re.fullmatch(r"[012]{5}", line):
                break
            rows.append(line)
        if len(rows) != min(tries, 6):
            return None

        puzzle_number = int(puzzle_id.replace(",", "").replace(".", ""))
        return ParsedResult(
            puzzle_id=str(puzzle_number),
            puzzle_date=self.puzzle_id_to_date(puzzle_number),
            payload={
                "tries": tries,
                "hardmode_enabled": bool(hardmode),
                "pattern": "".join(rows),
            },
        )

    def score(self, results: Sequence[Result]) -> list[PlayerScore]:
        scores = defaultdict(int)
        for result in results:
            scores[result.author_id] += 7 - result.payload["tries"]
        return [
            PlayerScore(author_id, scores[author_id]) for author_id in sorted(scores)
        ]

    def report_details(self, results: Sequence[Result], period: Period) -> str:
        if period.month is None or not results:
            return ""

        one_tries = [result for result in results if result.payload["tries"] == 1]
        one_tries_string = " ".join(
            f"{r.name}: Wordle {r.puzzle_id} on {r.puzzle_date:%Y-%m-%d} "
            f'"{self.get_solution_from_date(r.puzzle_date)}"'
            for r in one_tries
        )
        attempts = defaultdict(list)
        dates = {}
        for result in results:
            attempts[result.puzzle_id].append(result.payload["tries"])
            dates[result.puzzle_id] = result.puzzle_date
        means = {
            puzzle_id: sum(values) / len(values)
            for puzzle_id, values in attempts.items()
        }
        hardest = min(means, key=lambda key: (-means[key], dates[key]))
        easiest = min(means, key=lambda key: (means[key], dates[key]))

        return (
            f"First-try solves this month: {len(one_tries)}\n"
            f"{one_tries_string}\n\n"
            f'The hardest Wordle was "{self.get_solution_from_date(dates[hardest])}" '
            f"on {dates[hardest]:%Y-%m-%d}.\n"
            f"You needed an average of {means[hardest]:.2f} attempts.\n\n"
            f'The easiest Wordle was "{self.get_solution_from_date(dates[easiest])}" '
            f"on {dates[easiest]:%Y-%m-%d}.\n"
            f"You needed an average of just {means[easiest]:.2f} attempts."
        )
