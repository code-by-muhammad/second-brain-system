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

# Always download fresh, and trust only that download.
DEST="$ROOT/releases/$TAG"
mkdir -p "$(dirname "$DEST")"
FRESH=$(mktemp -d "$(dirname "$DEST")/.download-XXXXXX")
trap 'rm -rf "$FRESH"' EXIT
git clone -q -c core.autocrlf=false --branch "$TAG" --single-branch "$SOURCE" "$FRESH/release"
HEAD=$(git -C "$FRESH/release" rev-parse HEAD)
if [ "$HEAD" != "$COMMIT" ]; then
  echo "Release check failed: expected commit $COMMIT, the download is $HEAD. Nothing was installed." >&2
  exit 1
fi
if [ ! -e "$DEST" ]; then
  mv "$FRESH/release" "$DEST"
# A release folder kept from an earlier run must equal the fresh download, file for file, before any
# of it runs. Only its own Python environments and Git folder are left out of the comparison.
elif ! diff -rq -x .git -x '.venv*' "$FRESH/release" "$DEST" >/dev/null; then
  echo "Release check failed: $DEST differs from a fresh download of commit $COMMIT." >&2
  echo "Nothing was run. Keep that folder for review, or move it away and run this again." >&2
  exit 1
fi
rm -rf "$FRESH"; trap - EXIT
cd "$DEST"
exec uv run --no-project --python 3.12 python -I -B setup_device.py --root "$ROOT" "$@"
