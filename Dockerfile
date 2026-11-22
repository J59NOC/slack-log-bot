# Slack Log Bot
#
# 認証情報はイメージに入れず、実行時に profiles/ をマウントして渡す。
# 使い方は docs/running.md の「Docker で動かす」を参照。

FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    TZ=Asia/Tokyo \
    COLLECT_WEEKLY_LOG=

WORKDIR /app

# 依存だけ先に入れて、コード変更時にキャッシュを効かせる
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY *.py slack-app-manifest.yml ./
COPY src ./src

# compose 側の user: で上書きされる。単体で docker run したとき用の既定値。
USER 1000:1000

CMD ["python", "main.py"]
