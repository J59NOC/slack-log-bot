"""Weekly collection script - Fetch the past week's messages and save to Google Sheets/Drive.

Designed to run as a weekly cron job. Deduplication ensures no duplicate messages
even if collection periods overlap or the script runs multiple times.
Messages and their thread replies are grouped together in the spreadsheet.

Public channels  → tabs in the shared index spreadsheet
Private channels → separate spreadsheet per channel (members only)

本体は src/presentation/cli.py にある。

Usage:
    python collect_weekly.py                    # Past 7 days, all channels
    python collect_weekly.py --public            # Past 7 days, all public channels
    python collect_weekly.py --channel general  # Specific channel
    python collect_weekly.py --days 14          # Custom range (default: 8 for overlap margin)
"""

from src.presentation.cli import weekly_main

if __name__ == "__main__":
    weekly_main()
