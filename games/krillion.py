import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime, timedelta

from games.base import Game, ParsedResult, PlayerScore, Result
from periods import Period


class Krillion(Game):
    key = "krillion"
    display_name = "Krillion"

    _day_zero = date(2026, 7, 15)
    _header = re.compile(r"\AKrillion #([0-9]+)[ \t]+\U0001f990[ \t]*\n")
    _points = {
        "\u2b1b": 0,
        "\U0001fae7": 10,
        "\U0001f921": 15,
        "\U0001f41f": 30,
        "\U0001f991": 60,
        "\U0001f3ee": 85,
        "\U0001f31f": 100,
    }

    @classmethod
    def puzzle_id_to_date(cls, puzzle_id: int | str) -> date:
        return cls._day_zero + timedelta(days=int(puzzle_id))

    @classmethod
    def date_to_puzzle_id(cls, value: date | datetime) -> int:
        if isinstance(value, datetime):
            value = value.date()
        return (value - cls._day_zero).days

    def parse(self, text: str) -> ParsedResult | None:
        """Read a daily share's total score and seven ordered prompt scores."""
        normalized = text.replace("\r\n", "\n").replace("\ufe0f", "").strip()
        match = self._header.match(normalized)
        if match is None:
            return None

        lines = [line.strip() for line in normalized[match.end() :].splitlines() if line.strip()]
        if len(lines) != 2:
            return None
        score_text, symbols = lines
        if re.fullmatch(r"[0-9]{1,3}", score_text) is None or len(symbols) != 7:
            return None
        try:
            pattern = [self._points[symbol] for symbol in symbols]
        except KeyError:
            return None
        score = int(score_text)
        if score != sum(pattern):
            return None

        try:
            puzzle_number = int(match.group(1))
            puzzle_date = self.puzzle_id_to_date(puzzle_number)
        except (ValueError, OverflowError):
            return None

        return ParsedResult(
            puzzle_id=str(puzzle_number),
            puzzle_date=puzzle_date,
            payload={"score": score, "pattern": pattern},
        )

    def score(self, results: Sequence[Result]) -> list[PlayerScore]:
        """Sum each player's points across the selected period's results."""
        scores = defaultdict(int)
        for result in results:
            scores[result.author_id] += result.payload["score"]
        return [
            PlayerScore(author_id, scores[author_id]) for author_id in sorted(scores)
        ]

    def report_details(self, results: Sequence[Result], period: Period) -> str:
        """Celebrate the highest individual daily score in the selected period."""
        if not results:
            return ""

        best_score = max(result.payload["score"] for result in results)
        best_results = sorted(
            (result for result in results if result.payload["score"] == best_score),
            key=lambda result: (result.puzzle_date, result.author_id, result.puzzle_id),
        )
        names = self._display_names(results)
        performances = "\n".join(
            f"{names[result.author_id]} on {result.puzzle_date:%Y-%m-%d} "
            f"(Krillion #{result.puzzle_id})"
            for result in best_results
        )
        return f"Best single-day performance: {best_score} points\n{performances}"
