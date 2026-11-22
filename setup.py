"""Interactive setup wizard.

Walks through everything the bot needs and verifies each piece as it goes:
Slack tokens (with a scope check), Google OAuth2 consent, and the spreadsheet
and Drive folder, which are created for you rather than made by hand and
pasted in as IDs.

Safe to re-run: anything already configured and working is left alone.

本体は src/presentation/setup/ にある。

Usage:
    python setup.py                           # set up whatever is missing
    python setup.py --reauth                  # force the Google consent flow to run again
    python setup.py --profile leaders         # profiles/leaders/ に別の bot を設定する
    python setup.py --check-drive <URL>       # 保存先に必要な権限があるかだけ確かめる
"""

from src.presentation.setup.wizard import main

if __name__ == "__main__":
    main()
