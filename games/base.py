"""Game contract and inherited leaderboard/report formatting."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import polars as pl

from periods import Period


@dataclass(frozen=True)
class ParsedResult:
    puzzle_id: str
    puzzle_date: date
    payload: dict[str, Any]


@dataclass(frozen=True)
class Result(ParsedResult):
    game_key: str
    author_id: str
    name: str
    message_id: str | None = None
    posted_at: datetime | None = None
    raw_content: str | None = None


@dataclass(frozen=True)
class PlayerScore:
    author_id: str
    score: int | float


class Game(ABC):
    key: str
    display_name: str
    higher_is_better: bool = True

    _month_names = (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    )
    _code_fence = "\x60\x60\x60"

    @abstractmethod
    def parse(self, text: str) -> ParsedResult | None:
        """Extract one result, or return None for an unrelated/invalid message."""

    @abstractmethod
    def score(self, results: Sequence[Result]) -> list[PlayerScore]:
        """Calculate scores by player ID from this game's selected results."""

    @staticmethod
    def _display_names(results: Sequence[Result]) -> dict[str, str]:
        ordered = sorted(
            results,
            key=lambda result: (
                result.posted_at is not None,
                result.posted_at
                or datetime.combine(result.puzzle_date, datetime.min.time(), UTC),
                int(result.message_id or 0),
            ),
        )
        return {result.author_id: result.name for result in ordered}

    def build_leaderboard(self, results: Sequence[Result]) -> pl.DataFrame:
        """Score players and rank them with dense ties in this game's direction."""
        scores = self.score(results)
        names = self._display_names(results)
        rows = [
            (score.author_id, names[score.author_id], score.score) for score in scores
        ]
        score_type = (
            pl.Float64
            if any(isinstance(score.score, float) for score in scores)
            else pl.Int64
        )
        dataframe = pl.DataFrame(
            rows,
            orient="row",
            schema={
                "author_id": pl.String,
                "name": pl.String,
                "score": score_type,
            },
        )
        return dataframe.sort(
            "score", descending=self.higher_is_better, maintain_order=True
        ).with_columns(
            pl.col("score")
            .rank("dense", descending=self.higher_is_better)
            .cast(pl.Int64)
            .alias("rank")
        )

    def build_leaderboard_string(self, leaderboard: pl.DataFrame) -> str:
        """Default leaderboard text; games may override its presentation."""
        if leaderboard.is_empty():
            return "No results for this period."
        message = ""
        medals = ["\U0001f947", "\U0001f948", "\U0001f949"]
        separated_podium = False
        for rank, score, author in leaderboard.select(
            "rank", "score", "name"
        ).iter_rows():
            if 1 <= rank <= 3:
                author = f"{medals[rank - 1]}  {author}"
            elif rank == 4 and not separated_podium:
                message += "\n"
                separated_podium = True
            message += f"{rank:>2}.  {score:<3g}| {author}\n"
        winners = leaderboard.filter(pl.col("rank") == 1)["name"]
        message += f"\nCongratulations to: {', '.join(winners)}"
        return message

    def build_period_message(self, results: Sequence[Result], period: Period) -> str:
        """Build a report using this game's scoring, formatting, and detail hooks."""
        monthly = period.month is not None
        prize = "Monthly Award" if monthly else "Annual Award"
        label = (
            f"{self._month_names[period.month - 1]} {period.year}"
            if monthly
            else str(period.year)
        )
        separator = "-" * 60
        leaderboard = self.build_leaderboard_string(self.build_leaderboard(results))
        details = self.report_details(results, period) if results else ""
        message = f"**{self.display_name} {prize} - {label}**\n{self._code_fence}\n"
        message += f"{separator}\n{leaderboard}\n{separator}\n"
        if details:
            message += f"\n{details}\n{separator}\n"
        next_period = "month" if monthly else "year"
        message += f"Have fun next {next_period}!\n{self._code_fence}"
        return message

    def report_details(self, results: Sequence[Result], period: Period) -> str:
        """Optional game-specific content beneath the shared leaderboard."""
        return ""
