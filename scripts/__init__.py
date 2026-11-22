"""保守用のスクリプト（bot の動作には関わらない、必要なときに手で流すもの）。

リポジトリのルートから、モジュールとして実行する:

    uv run python -m scripts.relink_attachments            # 対象を表示するだけ
    uv run python -m scripts.relink_attachments --apply    # 実際に書き換える

どれも既定では一覧を出すだけで、--apply を付けたときだけ書き換える。
Docker イメージには含めていないので、手元（uv の環境）から実行する。

    cleanup_duplicate_files  重複してアップロードされた添付ファイルをゴミ箱へ
    relink_attachments       添付セルの生の URL をファイル名リンクに変換
"""
