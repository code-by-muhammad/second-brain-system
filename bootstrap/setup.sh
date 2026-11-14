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
case "$COMMIT" in
  *[!0-9a-f]*|""|?|??|???|????|?????|??????)
    echo "This copy of setup.sh is the source file and names no release commit. Use the setup.sh attached to the release." >&2
    exit 1;;
esac
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
# A release folder kept from an earlier run must still be exactly that commit before any of it runs:
# no extra or changed file by Git's own view, and every tracked file's bytes equal to its committed blob
# (hashed here, so a flag in the folder's index cannot hide a change). A failing Git command stops here.
EXTRA=$(git -C "$DEST" -c core.fsmonitor=false status --porcelain)
WANT=$(git -C "$DEST" ls-tree -r HEAD | awk '{print $3}')
HAVE=$(git -C "$DEST" ls-tree -r --name-only HEAD | git -C "$DEST" hash-object --no-filters --stdin-paths)
if [ -n "$EXTRA" ] || [ -z "$WANT" ] || [ "$WANT" != "$HAVE" ]; then
  echo "Release check failed: files in $DEST differ from commit $COMMIT." >&2
  echo "Nothing was run. Keep that folder for review, or move it away and run this again." >&2
  exit 1
fi
cd "$DEST"
exec uv run --no-project --python 3.12 python -I -B setup_device.py --root "$ROOT" "$@"
