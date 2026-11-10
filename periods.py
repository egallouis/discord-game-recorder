"""Calendar periods shared by importing and evaluation."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta


@dataclass(frozen=True)
class Period:
    year: int
    month: int | None = None

    def __post_init__(self):
        date(self.year, self.month if self.month is not None else 1, 1)
        # Both the next boundary and the fetch grace period must be representable.
        self.fetch_start
        self.fetch_end

    @property
    def start(self) -> date:
        return date(self.year, self.month if self.month is not None else 1, 1)

    @property
    def end(self) -> date:
        """Exclusive end of the puzzle-date range."""
        if self.month is None or self.month == 12:
            return date(self.year + 1, 1, 1)
        return date(self.year, self.month + 1, 1)

    @property
    def fetch_start(self) -> datetime:
        return datetime.combine(self.start, datetime.min.time(), UTC) - timedelta(days=2)

    @property
    def fetch_end(self) -> datetime:
        return datetime.combine(self.end, datetime.min.time(), UTC) + timedelta(days=2)
