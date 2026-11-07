#!/usr/bin/env bash
# One-command start for the Faceless Video Maker.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "==> creating virtualenv"
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
. .venv/bin/activate

echo "==> installing dependencies"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "!! ffmpeg is required but was not found on PATH." >&2
  echo "   Debian/Ubuntu: sudo apt-get install -y ffmpeg" >&2
  echo "   macOS:         brew install ffmpeg" >&2
  exit 1
fi

echo "==> starting server on http://localhost:${PORT:-8000}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
