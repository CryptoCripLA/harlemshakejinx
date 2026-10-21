#!/usr/bin/env bash
# Deploy JINX (paper mode) from the engineering host to the Contabo execution server. Idempotent.
# Ships the committed HEAD plus the two runtime upstreams (Mako stream, GMGN bridge); D3AD and DataNomad are
# reference-only and never shipped. Keeps the newest 3 releases on the server (owner storage rule 1).
# Credentials: ~/.config/jinx/jinx.env (+ GMGN_API_KEY from ~/.config/gmgn/.env; the GMGN signing key never leaves
# the engineering host) are installed as /etc/jinx/jinx.env, mode 640 root:jinx. Nothing secret is printed.
set -euo pipefail
HOST=${JINX_HOST:-ubuntu@13.140.224.208}     # Ubuntu cloud image's default sudo user; key-only login
KEY=${JINX_SSH_KEY:-$HOME/.ssh/jinx_contabo}
ENV_FILE=${JINX_ENV_FILE:-$HOME/.config/jinx/jinx.env}
GMGN_ENV=${GMGN_ENV_FILE:-$HOME/.config/gmgn/.env}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
REV=$(git -C "$ROOT" rev-parse --short=12 HEAD)
SSH=(ssh -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new)
[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ] || { echo "uncommitted changes: commit first"; exit 1; }

STAGE=$(mktemp -d); trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/release/upstream" "$STAGE/release/build"
git -C "$ROOT" archive --format=tar HEAD | tar -x -C "$STAGE/release"
echo "$REV" > "$STAGE/release/REVISION"
cp -a "$ROOT/upstream/mako" "$ROOT/upstream/gmgn" "$STAGE/release/upstream/"
cp -a "$ROOT/build/gmgn" "$STAGE/release/build/"
cp /usr/bin/node "$STAGE/node"                             # Node 22 for the GMGN bridge (Ubuntu 24.04 ships 18)
umask 077
{ grep -v '^GMGN_API_KEY=' "$ENV_FILE"; grep '^GMGN_API_KEY=' "$GMGN_ENV" || true; } > "$STAGE/jinx.env"

rsync -a --delete -e "${SSH[*]}" "$STAGE/" "$HOST:/tmp/jinx-deploy/"
"${SSH[@]}" "$HOST" sudo -n bash -s "$REV" <<'REMOTE'
set -euo pipefail
REV=$1; SRC=/tmp/jinx-deploy
export DEBIAN_FRONTEND=noninteractive
command -v rsync >/dev/null && dpkg -s python3.12-venv >/dev/null 2>&1 || { apt-get update -qq; apt-get install -y -qq python3.12-venv rsync >/dev/null; }
install -m 755 "$SRC/node" /usr/local/bin/node
id jinx >/dev/null 2>&1 || useradd --system --home-dir /var/lib/jinx --no-create-home --shell /usr/sbin/nologin jinx
install -d -m 755 /opt/jinx/releases
rsync -a --delete "$SRC/release/" "/opt/jinx/releases/$REV/"
[ -x /opt/jinx/venv/bin/python ] || python3.12 -m venv /opt/jinx/venv
/opt/jinx/venv/bin/pip install -q --no-cache-dir --disable-pip-version-check -r "/opt/jinx/releases/$REV/requirements.txt"
install -d -m 750 -o root -g jinx /etc/jinx
install -m 640 -o root -g jinx "$SRC/jinx.env" /etc/jinx/jinx.env
install -d /etc/systemd/journald.conf.d
install -m 644 "/opt/jinx/releases/$REV/deploy/contabo/journald-jinx.conf" /etc/systemd/journald.conf.d/jinx.conf
systemctl restart systemd-journald
install -m 644 "/opt/jinx/releases/$REV/deploy/contabo/jinx.service" /etc/systemd/system/jinx.service
ln -sfn "/opt/jinx/releases/$REV" /opt/jinx/current
systemctl daemon-reload
systemctl enable -q jinx
systemctl restart jinx
# only SSH is reachable from outside; /health is read on the server or through an SSH tunnel
if command -v ufw >/dev/null; then ufw allow OpenSSH >/dev/null; ufw --force enable >/dev/null; fi
# storage hygiene: newest 3 releases, no staging or package caches left behind
ls -1dt /opt/jinx/releases/* | tail -n +4 | xargs -r rm -rf
rm -rf "$SRC"; apt-get clean
echo "deployed $REV"
REMOTE
for i in $(seq 1 30); do
  if "${SSH[@]}" "$HOST" "curl -sf -m 5 http://127.0.0.1:8080/health" > "$STAGE/health.json" 2>/dev/null; then break; fi
  sleep 2
done
python3 - "$STAGE/health.json" <<'PY'
import json, sys
h = json.load(open(sys.argv[1]))
print("health:", h["status"], "mode:", h["mode"], "services:",
      {k: ("ok" if not v.get("last_error") and v.get("configured", True) else v.get("last_error") or "off") for k, v in h["services"].items()})
PY
