#!/bin/sh
# Second Brain: one-command setup for Mac and Linux.
# Downloads one exact release, checks that it is the reviewed one, then runs its setup.
# Extra arguments go to setup_device.py, for example: sh setup.sh --yes
set -eu

TAG="${SECOND_BRAIN_REF:-v0.2.6}"
COMMIT="${SECOND_BRAIN_COMMIT:-__COMMIT__}"
ROOT="${SECOND_BRAIN_ROOT:-$HOME/.local/share/second-brain}"
SOURCE="https://github.com/code-by-muhammad/second-brain-system.git"

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1. $2" >&2; exit 1; }
}
need git "Mac: run 'xcode-select --install'. Linux: install git with your package manager."
need uv "Install it from https://docs.astral.sh/uv/getting-started/installation/ and open a new terminal."

DEST="$ROOT/releases/$TAG"
if [ ! -d "$DEST/.git" ]; then
  mkdir -p "$ROOT/releases"
  git clone -q -c core.autocrlf=false --branch "$TAG" --single-branch "$SOURCE" "$DEST"
fi
HEAD=$(git -C "$DEST" rev-parse HEAD)
if [ "$HEAD" != "$COMMIT" ]; then
  echo "Release check failed: expected commit $COMMIT, found $HEAD in $DEST." >&2
  echo "Nothing was installed. Keep that folder for review." >&2
  exit 1
fi
cd "$DEST"
exec uv run --no-project --python 3.12 python -I -B setup_device.py --root "$ROOT" "$@"
