"""Slack Log Bot - Collects messages and files from Slack and saves to Google Sheets/Drive.

リアルタイム収集（Socket Mode）の入口。本体は src/ にある（構成は src/__init__.py）。

Usage:
    python main.py
"""

from src.presentation.slack_app import main

if __name__ == "__main__":
    main()
