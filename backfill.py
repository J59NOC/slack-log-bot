"""Backfill tool - Fetch existing message history and save to Google Sheets/Drive.

Messages and their thread replies are grouped together in the spreadsheet.
Public channels → tabs in the shared index spreadsheet,
Private channels → separate spreadsheet per channel.

本体は src/presentation/cli.py にある。

Usage:
    python backfill.py                      # All channels the bot is in
    python backfill.py --public              # All public channels the bot is in
    python backfill.py --channel general     # Specific channel by name
    python backfill.py --days 30            # Last 30 days (default: 90)
"""

from src.presentation.cli import backfill_main

if __name__ == "__main__":
    backfill_main()
