import io
import json
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import database
import discord_game_recorder as cli
from ban_list import add_to_ban_list
from evaluate import generate_period_message
from fetch import parse_messages
from games import Krillion, Result, get_game
from periods import Period


def krillion_result(
    puzzle_date=date(2026, 11, 11), author="1", name="Alice",
    pattern=(60, 60, 30, 0, 85, 85, 30),
):
    return Result(
        game_key="krillion", author_id=author, name=name,
        puzzle_id=str(Krillion.date_to_puzzle_id(puzzle_date)), puzzle_date=puzzle_date,
        payload={"score": sum(pattern), "pattern": list(pattern)},
    )


class KrillionParserTests(unittest.TestCase):
    sample = "Krillion #119 \U0001f990\n350\n\n\U0001f991\U0001f991\U0001f41f\u2b1b\U0001f3ee\U0001f3ee\U0001f41f"

    def setUp(self):
        self.game = Krillion()

    def test_shared_result(self):
        parsed = self.game.parse(self.sample)
        self.assertEqual(parsed.puzzle_id, "119")
        self.assertEqual(parsed.puzzle_date, date(2026, 11, 11))
        self.assertEqual(parsed.payload, {
            "score": 350, "pattern": [60, 60, 30, 0, 85, 85, 30],
        })

    def test_all_tiers_keep_their_order(self):
        text = "Krillion #1 \U0001f990\n300\n\n\u2b1b\U0001fae7\U0001f921\U0001f41f\U0001f991\U0001f3ee\U0001f31f"
        self.assertEqual(self.game.parse(text).payload, {
            "score": 300, "pattern": [0, 10, 15, 30, 60, 85, 100],
        })

    def test_whitespace_windows_newlines_and_variation_selectors(self):
        text = "  \r\n" + self.sample.replace("\n", "\r\n").replace("\u2b1b", "\u2b1b\ufe0f") + "\r\n  "
        self.assertEqual(self.game.parse(text), self.game.parse(self.sample))

    def test_minimum_and_maximum_scores(self):
        for points, symbol in ((0, "\u2b1b"), (700, "\U0001f31f")):
            with self.subTest(points=points):
                parsed = self.game.parse(f"Krillion #119 \U0001f990\n{points}\n\n{symbol * 7}")
                self.assertEqual(parsed.payload["score"], points)

    def test_invalid_and_unrelated_posts_are_ignored(self):
        for text in (
            "hello", "Krillion #119", "Wordle 119 1/6\n\n22222",
            self.sample.replace("350", "351"),
            self.sample.replace("350", "-350"),
            self.sample.replace("350", "7000"),
            self.sample.replace("350", "three hundred fifty"),
            self.sample.replace("\u2b1b", ""),
            self.sample + "\u2b1b",
            self.sample.replace("\u2b1b", "?"),
            self.sample + "\n\U0001f991",
            self.sample.replace("#119", "#119oops"),
            self.sample.replace("#119", "#" + "9" * 5000),
            self.sample.replace("#119", "#999999999"),
            self.sample.replace("#119", "\U0001f4da #119"),
        ):
            with self.subTest(text=text[:100]):
                self.assertIsNone(self.game.parse(text))

    def test_puzzle_date_conversion(self):
        self.assertEqual(Krillion.puzzle_id_to_date(0), date(2026, 7, 15))
        for value in (date(2026, 11, 11), datetime(2026, 11, 11, 20, tzinfo=UTC)):
            self.assertEqual(Krillion.date_to_puzzle_id(value), 119)
            self.assertEqual(Krillion.puzzle_id_to_date("119"), value.date() if isinstance(value, datetime) else value)


class KrillionLeaderboardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="krillion-tests-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "test.db"
        self.database_patch = patch.object(database, "db_path", str(self.path))
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        self.game = get_game("krillion")

    def test_sums_scores_by_player_and_ranks_highest_first_with_ties(self):
        results = [
            krillion_result(),
            krillion_result(date(2026, 11, 12), name="Alice renamed"),
            krillion_result(author="2", name="Bob", pattern=(100,) * 7),
            krillion_result(author="3", name="Alice"),
            krillion_result(author="4", name="Dave", pattern=(0,) * 7),
        ]
        board = self.game.build_leaderboard(results)
        self.assertEqual(board.select("author_id", "name", "score", "rank").rows(), [
            ("1", "Alice renamed", 700, 1), ("2", "Bob", 700, 1),
            ("3", "Alice", 350, 2), ("4", "Dave", 0, 3),
        ])

    def test_registered_parser_saves_score_and_ordered_pattern_in_database(self):
        messages = [{
            "id": "100", "timestamp": "2026-11-11T12:00:00+00:00",
            "author": {"id": "1", "name": "Alice"},
            "content": KrillionParserTests.sample,
        }]
        results = parse_messages(messages)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].game_key, "krillion")
        database.save_results(results)
        self.assertEqual(database.get_results("krillion"), results)
        with closing(database.get_connection()) as con:
            payload = con.execute(
                "SELECT payload_json FROM RESULTS WHERE game_key = 'krillion'"
            ).fetchone()[0]
        self.assertEqual(json.loads(payload), {
            "score": 350, "pattern": [60, 60, 30, 0, 85, 85, 30],
        })

    def test_monthly_and_yearly_totals_respect_puzzle_dates_and_bans(self):
        banned = krillion_result(date(2026, 11, 15), pattern=(100,) * 7)
        database.save_results([
            krillion_result(date(2026, 10, 31)),
            krillion_result(date(2026, 11, 1)),
            krillion_result(date(2026, 11, 30)),
            krillion_result(date(2026, 12, 1)),
            krillion_result(date(2027, 1, 1)),
            banned,
            replace(krillion_result(), game_key="wordle"),
        ])
        add_to_ban_list("1", banned.puzzle_id, "krillion")
        for period, expected_total in ((Period(2026, 11), 700), (Period(2026), 1400)):
            with self.subTest(period=period):
                selected = database.get_results("krillion", period)
                self.assertEqual(self.game.build_leaderboard(selected)["score"].to_list(), [expected_total])
                with redirect_stdout(io.StringIO()):
                    report = generate_period_message(period, "krillion")
                self.assertIn(f"{expected_total}|", report)
                self.assertIn("Congratulations to: Alice", report)
                self.assertIn("Best single-day performance: 350 points", report)
                self.assertNotIn("2027-01-01", report)
                if period.month is not None:
                    self.assertNotIn("2026-10-31", report)
                    self.assertNotIn("2026-12-01", report)

    def test_report_celebrates_best_daily_score_instead_of_highest_period_total(self):
        results = [
            krillion_result(),
            krillion_result(date(2026, 11, 12)),
            krillion_result(author="2", name="Bob", pattern=(85,) * 7),
        ]
        for period in (Period(2026, 11), Period(2026)):
            with self.subTest(period=period):
                report = self.game.build_period_message(results, period)
                self.assertIn("Congratulations to: Alice", report)
                self.assertIn(
                    "Best single-day performance: 595 points\n"
                    "Bob on 2026-11-11 (Krillion #119)",
                    report,
                )

    def test_best_daily_performance_includes_all_ties_and_latest_display_names(self):
        results = [
            krillion_result(date(2026, 11, 12), name="Alice renamed", pattern=(100,) * 7),
            krillion_result(author="2", name="Bob", pattern=(100,) * 7),
            krillion_result(name="Alice old", pattern=(100,) * 7),
        ]
        self.assertEqual(self.game.report_details(results, Period(2026, 11)), (
            "Best single-day performance: 700 points\n"
            "Alice renamed on 2026-11-11 (Krillion #119)\n"
            "Bob on 2026-11-11 (Krillion #119)\n"
            "Alice renamed on 2026-11-12 (Krillion #120)"
        ))

    def test_cli_selects_krillion_and_handles_empty_period(self):
        output = io.StringIO()
        with redirect_stdout(output):
            cli.main(["eval", "2026", "11", "--game", "krillion"])
        self.assertIn("Krillion Monthly Award - November 2026", output.getvalue())
        self.assertIn("No results for this period.", output.getvalue())
        self.assertNotIn("Best single-day performance", output.getvalue())
        self.assertEqual(self.game.report_details([], Period(2026, 11)), "")


if __name__ == "__main__":
    unittest.main()
