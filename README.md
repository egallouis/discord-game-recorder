# discord-game-recorder

Read game results from a Discord channel and generate monthly or yearly leaderboards.
Wordle is implemented. The shared framework supports adding other game types with
their own parsing, scoring, and optional report statistics.

## Setup

Use Python 3.12 or newer and [install uv](https://docs.astral.sh/uv/getting-started/installation/).
From the project directory, install the app with:

~~~powershell
uv tool install .
~~~

You can then run `dgr` as shown below. If the command is not found, follow uv's
instructions to add its tool executable directory to your PATH. Reinstall with
`uv tool install --force .` after changing the app's code.

Create an application in the [Discord Developer Portal](https://discord.com/developers/applications),
then set up its bot and obtain its bot token from the **Bot** page. Enable the
**Message Content Intent** on that page. Invite the bot to your server using the
application's installation settings, with **View Channel** and **Read Message
History** permissions for the channel you want to export. Check channel-specific
permission overrides, especially for private channels.

Use a bot token, not a personal account token: automating a normal user account
violates [Discord's policy](https://support.discord.com/hc/en-us/articles/115002192352-Automated-User-Accounts-Self-Bots).
Keep the bot token private like a password.

Download the [DiscordChatExporter CLI](https://github.com/Tyrrrz/DiscordChatExporter/)
and create a file named .env in the project directory with exactly three lines:

~~~text
YOUR_DISCORD_BOT_TOKEN
YOUR_CHANNEL_ID
C:\path\to\DiscordChatExporter.Cli.exe
~~~

Run the commands from this project directory: the app reads `.env` and stores
`database.db` in the current directory. The same configured channel supplies
all registered games.

## Commands

Dates are always **YEAR [MONTH]**. Omitting the month selects the whole year.

~~~powershell
dgr fetch 2026 3
dgr fetch 2026

dgr eval 2026 3
dgr eval 2026
dgr eval 2026 3 --game wordle
~~~

Fetching exports messages once and tries every registered parser. There is no
game filter on fetching. Evaluation produces a leaderboard for one game; the
default is Wordle.

Fetch boundaries use UTC and include two extra calendar days on either side of
the requested period. For March 2026, the requested export runs from February 27
at 00:00 UTC until April 3 at 00:00 UTC.

Evaluation selects results by **puzzle date**, with no posting-time cutoff. A
late result imported later can update an older leaderboard. For repeated results
from the same player, game, and puzzle, the latest posting timestamp wins; Discord
message ID breaks ties. Reimporting an older export cannot overwrite a newer
submission. Each message supplies at most one result; ambiguous matches are
reported and skipped.

Exclude or restore a player's result for a particular puzzle:

~~~powershell
dgr ban_list add AUTHOR_ID PUZZLE_ID --game wordle
dgr ban_list remove AUTHOR_ID PUZZLE_ID --game wordle
dgr ban_list show --game wordle
~~~

Wordle is also the default for ban commands. Puzzle identifiers and bans are
separate for each game type.

## Adding a game

Implement [Game](games/base.py) and register its instance in
[GAMES](games/__init__.py). A game provides:

- A stable key, a display name, and whether higher scores rank first.
- A parse method accepting message text and returning a ParsedResult, or None
  for an unrelated or invalid message.
- A score method accepting that game's selected Result objects and returning
  PlayerScore objects keyed by player ID. Each game has one scoring method.
- Optionally, a report_details method for game-specific statistics.

Game provides inherited build_leaderboard, build_leaderboard_string, and
build_period_message methods. These supply dense ranking, display names, medals,
and English monthly/yearly report formatting. Override any of these methods to
customize a game's presentation; the inherited report calls the game's own
scoring, formatting, and detail methods.

ParsedResult contains a string puzzle identifier, its calendar date, and a
JSON-compatible payload. Wordle stores attempts, hard mode, and its grid in the
payload. Other games can use entirely different fields and aggregation rules.
The parser determines the puzzle date using its game's rules.

Shared code handles Discord metadata, persistence, period selection, and bans.
The evaluation coordinator loads the selected results and prints the report
returned by the game instance. Scoring aggregates by player ID rather than
display name. Wordle owns its puzzle-date conversions, solution lookups,
hard-mode helper, scoring, and monthly statistics. Its report includes first-try
wins and the easiest/hardest puzzles; solution lookups use the NYT endpoint.
Krillion is not implemented yet.

Python callers use parse_messages, save_results, and get_results for the shared
data flow, then call the game instance's scoring or report methods. Wordle date
conversions are available as Wordle.puzzle_id_to_date and
Wordle.date_to_puzzle_id. The old module-level Wordle helpers and month/year
convenience wrappers have been removed. CLI commands use the same syntax shown
above.

## Database compatibility

The first database access automatically migrates a legacy database in one
transaction. Before migration, it creates a uniquely named SQLite backup beside
the database, matching the pattern database.db.pre-v1-*.bak.

Existing results and bans become Wordle records. Results without original
message metadata remain eligible, and a new imported submission can replace
them. Common fields live in RESULTS with a JSON payload; bans live in GAME_BANS.
The old GAMES and BAN_LIST tables remain intact as historical data and are not
updated by new imports.

The existing migration converts legacy Wordle records through
Wordle.from_legacy_row. Class separation does not change the stored schema.

## Tests

~~~powershell
uv run python -m unittest discover -s tests -v
~~~

The tests use temporary databases, a test-only second game, and mocked Discord
exports and solution lookups. They do not access the configured Discord channel
or modify the project's database.
