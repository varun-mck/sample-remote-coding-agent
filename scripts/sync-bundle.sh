#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCK="$ROOT/agent/aipdlc.lock"
DEST="$ROOT/agent/aipdlc"

if [[ ! -f "$LOCK" ]]; then
  echo "missing $LOCK" >&2
  exit 1
fi

source_path=""
while IFS= read -r line; do
  case "$line" in
    source=*) source_path="${line#source=}" ;;
  esac
done <"$LOCK"

if [[ -z "$source_path" ]]; then
  echo "lock file has no source=" >&2
  exit 1
fi

if [[ "$source_path" = /* ]]; then
  SRC="$source_path"
else
  SRC="$ROOT/$source_path"
fi

rsync -a --delete "$SRC/" "$DEST/"
echo "synced bundle from $SRC to $DEST"