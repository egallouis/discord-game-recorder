import io
import json
import sqlite3
import tempfile
import unittest
from collections import defaultdict
from contextlib import closing, redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import polars as pl

import database
import discord_game_recorder as cli
from ban_list import add_to_ban_list, remove_from_ban_list, show_ban_list
from evaluate import generate_period_message
from fetch import export_messages, fetch_period, parse_messages
from games import GAMES, Game, ParsedResult, PlayerScore, Result, Wordle, get_game
from periods import Period


class OtherGame(Game):
    """Test-only game: different fields, nonnumeric IDs, and lower scores win."""

    key = "other"
    display_name = "Other Game"
    higher_is_better = False

    def parse(self, text):
        if not text.startswith("Other "):
            return None
        _, puzzle_id, puzzle_date, distance = text.split()
        return ParsedResult(puzzle_id, date.fromisoformat(puzzle_date), {
            "distance": float(distance), "symbols": ["fish"], "extra": {"complete": True},
        })

    def score(self, results):
        distances = defaultdict(list)
        for result in results:
            distances[result.author_id].append(result.payload["distance"])
        return [PlayerScore(author, sum(values) / len(values)) for author, values in distances.items()]


def wordle_text(puzzle_id=1716, tries=2, hardmode=False):
    count = 6 if tries == "X" else min(tries, 6)
    rows = ["00000"] * (count - 1) + ["22222" if tries != "X" else "00000"]
    return f"Wordle {puzzle_id} {tries}/6{'*' if hardmode else ''}\n\n" + "\n".join(rows)


def message(content, message_id="100", timestamp="2026-03-01T12:00:00+00:00", author_id="1"):
    return {
        "id": message_id, "content": content, "timestamp": timestamp,
        "author": {"id": author_id, "nickname": None, "name": "Alice"},
    }


def result(author="1", name="Alice", puzzle_date=date(2026, 3, 1), tries=2):
    return Result(
        game_key="wordle", author_id=author, name=name,
        puzzle_id=str(Wordle.date_to_puzzle_id(puzzle_date)), puzzle_date=puzzle_date,
        payload={"tries": tries, "hardmode_enabled": False, "pattern": "00000" * min(tries, 6)},
    )


class DatabaseCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="discord-game-recorder-tests-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "test.db"
        self.patch = patch.object(database, "db_path", str(self.path))
        self.patch.start()
        self.addCleanup(self.patch.stop)


class ParserTests(unittest.TestCase):
    def test_success_localized_numbers_and_hardmode(self):
        for identifier in ("1,351", "1.351", "1351"):
            with self.subTest(identifier=identifier):
                parsed = Wordle().parse(wordle_text(identifier, hardmode=True))
                self.assertEqual(parsed.puzzle_id, "1351")
                self.assertEqual(parsed.puzzle_date, date(2025, 3, 1))
                self.assertEqual(parsed.payload, {
                    "tries": 2, "hardmode_enabled": True, "pattern": "0000022222",
                })

    def test_emojis_including_high_contrast_and_variation_selectors(self):
        text = "Wordle 1 2/6\r\n\r\n\u2b1b\ufe0f\u2b1c\U0001f7e8\U0001f7e7\U0001f7e6\r\n" + "\U0001f7e9" * 5
        self.assertEqual(Wordle().parse(text).payload["pattern"], "0012122222")
        solved = "Wordle 1 1/6\n\n" + "\U0001f7e7" * 5
        self.assertEqual(Wordle().parse(solved).payload["pattern"], "22222")

    def test_failure_and_legacy_failure_number(self):
        for text in (wordle_text(1, "X"), wordle_text(1, 7)):
            self.assertEqual(Wordle().parse(text).payload["tries"], 7)

    def test_invalid_and_unrelated_posts_are_ignored(self):
        for text in ("hello", "Krillion #118", "Wordle 1 2/6", "Wordle 1 1/6\n\n2222", "Wordle 1 2/6\n\n22222"):
            with self.subTest(text=text):
                self.assertIsNone(Wordle().parse(text))

    def test_date_conversion_supports_dates_and_datetimes(self):
        self.assertEqual(Wordle.puzzle_id_to_date(0), date(2021, 6, 19))
        for value in (date(2026, 3, 1), datetime(2026, 3, 1, 20, tzinfo=UTC)):
            self.assertEqual(Wordle.puzzle_id_to_date(Wordle.date_to_puzzle_id(value)), date(2026, 3, 1))

    def test_dispatch_reads_every_game_and_keeps_source_metadata(self):
        content = wordle_text()
        messages = [message(content, timestamp="2026-03-01T01:30:00+02:00"),
                    message("Other A 2026-03-01 3.5", "101"), message("hello")]
        results = parse_messages(messages, [Wordle(), OtherGame()])
        self.assertEqual([r.game_key for r in results], ["wordle", "other"])
        self.assertEqual(results[0].raw_content, content)
        self.assertEqual(results[0].name, "Alice")
        self.assertEqual(results[0].posted_at, datetime(2026, 2, 28, 23, 30, tzinfo=UTC))
        self.assertEqual(results[1].puzzle_id, "A")

    def test_ambiguous_posts_are_reported_and_skipped(self):
        class Mirror(Wordle):
            key = "mirror"
        with self.assertLogs("fetch", level="WARNING") as logs:
            self.assertEqual(parse_messages([message(wordle_text())], [Wordle(), Mirror()]), [])
        self.assertIn("Ambiguous", logs.output[0])

    def test_invalid_source_metadata_is_reported_and_skipped(self):
        malformed = message(wordle_text())
        del malformed["timestamp"]
        with self.assertLogs("fetch", level="WARNING"):
            self.assertEqual(parse_messages([malformed]), [])

class StorageTests(DatabaseCase):
    def test_latest_result_wins_across_imports_and_roundtrips(self):
        original = result()
        older = replace(original, posted_at=datetime(2026, 3, 1, 10, tzinfo=UTC), message_id="9", raw_content="old")
        newer = replace(older, payload={**older.payload, "tries": 3},
                        posted_at=datetime(2026, 3, 1, 11, tzinfo=UTC), message_id="10", raw_content="new")
        database.save_results([newer, older])
        database.save_results([newer, older])
        self.assertEqual(database.get_results(), [newer])

    def test_same_timestamp_uses_numeric_message_id_and_same_message_can_update(self):
        first = replace(result(), posted_at=datetime(2026, 3, 1, tzinfo=UTC), message_id="9")
        second = replace(first, message_id="10", raw_content="new")
        database.save_results([second, first])
        self.assertEqual(database.get_results(), [second])
        edited = replace(second, payload={**second.payload, "tries": 4})
        database.save_results([edited])
        self.assertEqual(database.get_results(), [edited])

    def test_known_timestamp_replaces_legacy_but_not_the_reverse(self):
        legacy = result()
        sourced = replace(legacy, posted_at=datetime(2026, 3, 1, tzinfo=UTC), message_id="1")
        database.save_results([legacy, sourced, legacy])
        self.assertEqual(database.get_results(), [sourced])

    def test_same_puzzle_number_and_bans_are_isolated_by_game(self):
        wordle = result()
        other = replace(wordle, game_key="other", payload={"distance": 3.5, "symbols": ["fish"], "extra": {"ok": True}})
        database.save_results([wordle, other])
        add_to_ban_list("1", wordle.puzzle_id)
        add_to_ban_list("1", wordle.puzzle_id)
        self.assertEqual(database.get_results(), [])
        self.assertEqual(database.get_results("other"), [other])
        self.assertEqual(database.get_results(include_banned=True), [wordle])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(show_ban_list().height, 1)
            self.assertTrue(show_ban_list("other").is_empty())
        remove_from_ban_list("1", wordle.puzzle_id, "other")
        self.assertEqual(database.get_results(), [])
        remove_from_ban_list("1", wordle.puzzle_id)
        self.assertEqual(database.get_results(), [wordle])

    def test_evaluation_uses_only_puzzle_date_even_for_very_late_posts(self):
        march = replace(result(), posted_at=datetime(2026, 12, 1, tzinfo=UTC), message_id="1")
        february = result(puzzle_date=date(2026, 2, 28))
        april = result(puzzle_date=date(2026, 4, 1))
        database.save_results([march, february, april])
        self.assertEqual(database.get_results(period=Period(2026, 3)), [march])
        self.assertEqual(len(database.get_results(period=Period(2026))), 3)
        self.assertEqual(database.get_results(period=Period(2025)), [])

    def test_failed_import_rolls_back_all_results_and_players(self):
        invalid = replace(result(author="2"), payload={"not_json": object()})
        with self.assertRaises(TypeError):
            database.save_results([result(), invalid])
        self.assertEqual(database.get_results(), [])
        with closing(database.get_connection()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM PLAYERS").fetchone()[0], 0)

    def test_timestamp_requires_timezone(self):
        with self.assertRaises(ValueError):
            database.save_results([replace(result(), posted_at=datetime(2026, 3, 1))])

class MigrationTests(DatabaseCase):
    def create_legacy(self, invalid=False):
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute("""CREATE TABLE GAMES (
                author_id TEXT, name TEXT, game_id INTEGER, tries INTEGER,
                hardmode_enabled BOOLEAN, pattern TEXT, PRIMARY KEY(author_id, game_id))""")
            con.execute("CREATE TABLE BAN_LIST (author_id TEXT, game_id INTEGER, PRIMARY KEY(author_id, game_id))")
            puzzle_id = "invalid" if invalid else Wordle.date_to_puzzle_id(date(2026, 3, 1))
            con.execute("INSERT INTO GAMES VALUES (?, ?, ?, ?, ?, ?)", ("1", "Alice", puzzle_id, 2, 1, "0000022222"))
            con.execute("INSERT INTO BAN_LIST VALUES (?, ?)", ("1", puzzle_id))

    def test_legacy_migration_backs_up_preserves_data_and_runs_once(self):
        self.create_legacy()
        self.assertEqual(database.get_results(), [])
        rows = database.get_results(include_banned=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].payload, {"tries": 2, "hardmode_enabled": True, "pattern": "0000022222"})
        self.assertEqual(rows[0].puzzle_date, date(2026, 3, 1))
        self.assertIsNone(rows[0].posted_at)
        self.assertIsNone(rows[0].message_id)
        self.assertIsNone(rows[0].raw_content)
        backups = list(self.path.parent.glob("test.db.pre-v1-*.bak"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as backup:
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(backup.execute("SELECT COUNT(*) FROM GAMES").fetchone()[0], 1)
        with closing(database.get_connection()) as con:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM GAMES").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT game_key FROM GAME_BANS").fetchone()[0], "wordle")
        remove_from_ban_list("1", rows[0].puzzle_id)
        self.assertEqual(database.get_results(), rows)
        self.assertEqual(list(self.path.parent.glob("test.db.pre-v1-*.bak")), backups)

    def test_failed_migration_rolls_back_schema_and_preserves_legacy(self):
        self.create_legacy(invalid=True)
        with self.assertRaises(ValueError):
            database.get_connection()
        with closing(sqlite3.connect(self.path)) as con:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM GAMES").fetchone()[0], 1)
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("RESULTS", tables)
            self.assertNotIn("GAME_BANS", tables)
        self.assertEqual(len(list(self.path.parent.glob("*.bak"))), 1)

    def test_newer_schema_is_rejected_without_changes(self):
        with closing(sqlite3.connect(self.path)) as con:
            con.execute("PRAGMA user_version = 2")
        with self.assertRaisesRegex(RuntimeError, "newer"):
            database.get_connection()
        with closing(sqlite3.connect(self.path)) as con:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 2)


class ReportTests(unittest.TestCase):
    def test_wordle_scores_dense_ties_and_failure_points(self):
        results = [result("1", "Alice", tries=1), result("2", "Bob", tries=1),
                   result("3", "Carol", tries=2), result("4", "Dave", tries=7)]
        board = Wordle().build_leaderboard(results)
        self.assertEqual(board["score"].to_list(), [6, 6, 5, 0])
        self.assertEqual(board["rank"].to_list(), [1, 1, 2, 3])

    def test_player_identity_survives_display_name_changes(self):
        first = result("1", "Old", tries=1)
        second = replace(result("1", "New", date(2026, 3, 2), tries=2),
                         posted_at=datetime(2026, 3, 2, tzinfo=UTC), message_id="2")
        board = Wordle().build_leaderboard([second, first])
        self.assertEqual(board.select("author_id", "name", "score").rows(), [("1", "New", 11)])

    def test_monthly_wordle_report_preserves_statistics_and_uses_english_text(self):
        results = [result("1", "Alice", tries=1), result("2", "Bob", tries=5),
                   result("1", "Alice", date(2026, 3, 2), tries=7)]
        with patch.object(Wordle, "get_solution_from_date", side_effect=lambda day: f"word-{day.day}"):
            text = Wordle().build_period_message(results, Period(2026, 3))
        self.assertIn("Wordle Monthly Award - March 2026", text)
        self.assertIn("Congratulations to: Alice", text)
        self.assertIn("First-try solves this month: 1", text)
        self.assertIn('Alice: Wordle 1716 on 2026-03-01 "word-1"', text)
        self.assertIn('The hardest Wordle was "word-2" on 2026-03-02', text)
        self.assertIn("an average of 7.00 attempts", text)
        self.assertIn('The easiest Wordle was "word-1" on 2026-03-01', text)
        self.assertIn("an average of just 3.00 attempts", text)
        self.assertIn("Have fun next month!", text)

    def test_yearly_and_empty_reports_do_not_lookup_solutions(self):
        with patch.object(Wordle, "get_solution_from_date") as lookup:
            yearly = Wordle().build_period_message([result()], Period(2026))
            empty = Wordle().build_period_message([], Period(2026, 3))
        lookup.assert_not_called()
        self.assertIn("Wordle Annual Award - 2026", yearly)
        self.assertIn("Have fun next year!", yearly)
        self.assertNotIn("hardest", yearly)
        self.assertIn("No results for this period.", empty)
        self.assertNotIn("Congratulations", empty)

    def test_different_game_can_score_and_report_without_wordle_fields(self):
        base = replace(result(), game_key="other", puzzle_id="A", payload={"distance": 2.5})
        results = [base, replace(base, author_id="2", name="Bob", payload={"distance": 1.0})]
        board = OtherGame().build_leaderboard(results)
        self.assertEqual(board["name"].to_list(), ["Bob", "Alice"])
        self.assertEqual(board["rank"].to_list(), [1, 2])
        text = OtherGame().build_period_message(results, Period(2026, 3))
        self.assertIn("Other Game Monthly Award", text)
        self.assertNotIn("Wordle", text)

    def test_subclass_formatting_and_detail_overrides_appear_in_inherited_report(self):
        class CustomGame(OtherGame):
            def build_leaderboard_string(self, leaderboard: pl.DataFrame) -> str:
                return f'Winner: {leaderboard["name"][0]}'

            def report_details(self, results, period):
                return f"Custom details: {len(results)} results"

        base = replace(result(), game_key="other", puzzle_id="A", payload={"distance": 2.5})
        results = [base, replace(base, author_id="2", name="Bob", payload={"distance": 1.0})]
        text = CustomGame().build_period_message(results, Period(2026, 3))
        self.assertIn("Other Game Monthly Award", text)
        self.assertIn("Winner: Bob", text)
        self.assertIn("Custom details: 2 results", text)
        self.assertNotIn("Congratulations", text)


class EvaluationTests(DatabaseCase):
    def test_coordinator_passes_selected_results_to_games_report_override(self):
        class CustomGame(OtherGame):
            def build_period_message(self, results, period):
                return f"{period.start.isoformat()}: {', '.join(r.name for r in results)}"

        base = replace(result(), game_key="other", puzzle_id="A", payload={"distance": 2.5})
        database.save_results([
            base, replace(base, author_id="2", name="Bob"),
            replace(base, puzzle_id="B", puzzle_date=date(2026, 2, 28)), result(),
        ])
        add_to_ban_list("2", "A", "other")
        output = io.StringIO()
        with patch.dict(GAMES, {"other": CustomGame()}), redirect_stdout(output):
            text = generate_period_message(Period(2026, 3), "other")
        self.assertEqual(text, "2026-03-01: Alice")
        self.assertEqual(output.getvalue(), text + "\n")


class PeriodAndExportTests(DatabaseCase):
    def test_utc_grace_boundaries_for_month_year_and_leap_year(self):
        for period, start, end in (
            (Period(2026, 3), "2026-02-27T00:00:00+00:00", "2026-04-03T00:00:00+00:00"),
            (Period(2026), "2025-12-30T00:00:00+00:00", "2027-01-03T00:00:00+00:00"),
            (Period(2024, 2), "2024-01-30T00:00:00+00:00", "2024-03-03T00:00:00+00:00"),
            (Period(2026, 12), "2026-11-29T00:00:00+00:00", "2027-01-03T00:00:00+00:00"),
        ):
            with self.subTest(period=period):
                self.assertEqual(period.fetch_start.isoformat(), start)
                self.assertEqual(period.fetch_end.isoformat(), end)

    def test_fetch_exports_once_and_imports_all_registered_games(self):
        messages = [message(wordle_text()), message("Other A 2026-03-01 3.5", "101")]
        period = Period(2026, 3)
        with patch.dict(GAMES, {"other": OtherGame()}), patch("fetch.export_messages", return_value=messages) as export:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(fetch_period(period), 2)
        export.assert_called_once_with(period)
        self.assertEqual(len(database.get_results("wordle")), 1)
        self.assertEqual(len(database.get_results("other")), 1)

    def test_export_passes_explicit_utc_boundaries_and_reads_json(self):
        config = self.path.parent / "test.env"
        config.write_text('fake-token\nchannel\n"C:/Exporter Path/export.exe"\n', encoding="utf-8")

        def run(command, **kwargs):
            self.assertEqual(command[0], "C:/Exporter Path/export.exe")
            self.assertEqual(command[command.index("--after") + 1], "2026-02-27T00:00:00+00:00")
            self.assertEqual(command[command.index("--before") + 1], "2026-04-03T00:00:00+00:00")
            self.assertNotIn("--game", command)
            Path(command[command.index("-o") + 1]).write_text(json.dumps({"messages": [{"content": "hello"}]}))
            return SimpleNamespace(returncode=0)

        with patch("fetch.subprocess.run", side_effect=run):
            self.assertEqual(export_messages(Period(2026, 3), config), [{"content": "hello"}])

    def test_export_failure_does_not_expose_token(self):
        config = self.path.parent / "test.env"
        config.write_text("fake-token\nchannel\nexport.exe\n", encoding="utf-8")
        with patch("fetch.subprocess.run", return_value=SimpleNamespace(returncode=1)):
            with self.assertRaises(RuntimeError) as error:
                export_messages(Period(2026), config)
        self.assertNotIn("fake-token", str(error.exception))


class CommandTests(unittest.TestCase):
    def test_evaluation_defaults_to_wordle_and_supports_years(self):
        with patch.object(cli, "generate_period_message") as generate:
            cli.main(["eval", "2026"])
            generate.assert_called_once_with(Period(2026), "wordle")

    def test_evaluation_selects_registered_game(self):
        with patch.dict(GAMES, {"other": OtherGame()}), patch.object(cli, "generate_period_message") as generate:
            cli.main(["eval", "2026", "3", "--game", "other"])
            generate.assert_called_once_with(Period(2026, 3), "other")

    def test_fetch_accepts_year_and_month_without_game_selection(self):
        for arguments, period in ((["fetch", "2026"], Period(2026)), (["fetch", "2026", "3"], Period(2026, 3))):
            with patch.object(cli, "fetch_period") as fetch:
                cli.main(arguments)
                fetch.assert_called_once_with(period)

    def test_ban_commands_keep_default_and_allow_game_selection(self):
        with patch.object(cli, "add_to_ban_list") as add:
            cli.main(["ban_list", "add", "1", "1716"])
            add.assert_called_once_with("1", "1716", "wordle")
        with patch.dict(GAMES, {"other": OtherGame()}), patch.object(cli, "remove_from_ban_list") as remove:
            cli.main(["ban_list", "remove", "1", "A", "--game", "other"])
            remove.assert_called_once_with("1", "A", "other")

    def test_invalid_periods_and_unknown_games_fail_before_database_access(self):
        for arguments in (["eval", "2026", "0"], ["fetch", "2026", "13"], ["eval", "2026", "3", "1"],
                          ["eval", "2026", "--game", "missing"]):
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                cli.main(arguments)
            self.assertEqual(error.exception.code, 2)
        with self.assertRaisesRegex(ValueError, "Unknown game"):
            get_game("missing")


if __name__ == "__main__":
    unittest.main()
