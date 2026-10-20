#!/usr/bin/env bash
# Compile GMGN's pinned TypeScript client (upstream/gmgn, unmodified) into build/gmgn/dist.
# The upstream tree is copied, never edited. Dependencies come from its own package-lock.json.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/upstream/gmgn"
OUT="$ROOT/build/gmgn"
[ -f "$SRC/package-lock.json" ] || { echo "upstream/gmgn missing: run scripts/upstream_sync.py gmgn" >&2; exit 1; }
rm -rf "$OUT"
mkdir -p "$OUT"
cp -r "$SRC/src" "$SRC/package.json" "$SRC/package-lock.json" "$SRC/tsconfig.json" "$SRC/LICENSE" "$OUT/"
cd "$OUT"
npm ci --ignore-scripts --no-audit --no-fund --loglevel=error
npx --no-install tsc -p tsconfig.json
echo "built $OUT/dist/client/OpenApiClient.js from gmgn@$(git -C "$SRC" rev-parse --short=12 HEAD)"
