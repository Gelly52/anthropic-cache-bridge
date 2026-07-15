#!/bin/sh
set -eu

NAME="anthropic-cache-bridge"
LABEL="io.github.anthropic-cache-bridge"
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/$NAME"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/$NAME"
INSTALL_DIR="$HOME/.local/lib/$NAME"
BIN_DIR="$HOME/.local/bin"
CONFIG_FILE="$CONFIG_DIR/config.json"
PORT=18787
UPSTREAM=""
AFFINITY_ID=""
CACHE_TTL="5m"
SIGNATURE_TTL=604800
FORCE_CONFIG=0
ENABLE_PROMPT_CACHE=true
ENABLE_THINKING_SIGNATURE=true

usage() {
    cat <<'EOF'
Usage: ./install.sh --upstream URL [options]

Required:
  --upstream URL          Anthropic-compatible upstream base URL

Options:
  --port PORT             Local listening port (default: 18787)
  --cache-ttl 5m|1h       Anthropic prompt-cache TTL (default: 5m)
  --signature-ttl SEC     Local signature retention (default: 604800)
  --affinity-id VALUE     Stable metadata.user_id for upstream routing
  --no-prompt-cache       Disable prompt-cache injection
  --no-thinking-signature Disable signature capture and restoration
  --force-config          Replace an existing config.json
  -h, --help              Show help

The installer never asks for or stores an API key. Keep credentials in the
client configuration; request headers are forwarded by the bridge.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --upstream) UPSTREAM="${2:?missing URL}"; shift 2 ;;
        --port) PORT="${2:?missing port}"; shift 2 ;;
        --cache-ttl) CACHE_TTL="${2:?missing TTL}"; shift 2 ;;
        --signature-ttl) SIGNATURE_TTL="${2:?missing seconds}"; shift 2 ;;
        --affinity-id) AFFINITY_ID="${2:?missing value}"; shift 2 ;;
        --no-prompt-cache) ENABLE_PROMPT_CACHE=false; shift ;;
        --no-thinking-signature) ENABLE_THINKING_SIGNATURE=false; shift ;;
        --force-config) FORCE_CONFIG=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ -z "$UPSTREAM" ] && [ ! -f "$CONFIG_FILE" ]; then
    echo "--upstream is required for the first install" >&2
    usage >&2
    exit 2
fi
if [ "$FORCE_CONFIG" -eq 1 ] && [ -z "$UPSTREAM" ]; then
    echo "--upstream is required with --force-config" >&2
    exit 2
fi
if [ -n "$UPSTREAM" ]; then
    case "$UPSTREAM" in http://*|https://*) ;; *) echo "Invalid upstream URL" >&2; exit 2 ;; esac
fi
case "$PORT" in *[!0-9]*|'') echo "Invalid port" >&2; exit 2 ;; esac
case "$SIGNATURE_TTL" in *[!0-9]*|'') echo "Invalid signature TTL" >&2; exit 2 ;; esac
case "$CACHE_TTL" in 5m|1h) ;; *) echo "cache TTL must be 5m or 1h" >&2; exit 2 ;; esac
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }

mkdir -p "$CONFIG_DIR" "$DATA_DIR" "$INSTALL_DIR" "$BIN_DIR"
chmod 700 "$CONFIG_DIR" "$DATA_DIR" "$INSTALL_DIR"

for source in "$ROOT_DIR"/bin/*.py; do
    PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/acb-pycache" \
        python3 -m py_compile "$source"
done

if [ -f "$CONFIG_FILE" ] || [ -f "$INSTALL_DIR/bridge.py" ]; then
    backup_dir="$DATA_DIR/backups/$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$backup_dir"
    chmod 700 "$DATA_DIR/backups" "$backup_dir"
    [ -f "$CONFIG_FILE" ] && cp -p "$CONFIG_FILE" "$backup_dir/"
    for installed in "$INSTALL_DIR"/*.py; do
        [ -f "$installed" ] && cp -p "$installed" "$backup_dir/"
    done
    echo "Existing installation backed up to $backup_dir"
fi

if [ -f "$CONFIG_FILE" ] && [ "$FORCE_CONFIG" -ne 1 ]; then
    echo "Existing config preserved: $CONFIG_FILE"
else
    UPSTREAM="$UPSTREAM" PORT="$PORT" AFFINITY_ID="$AFFINITY_ID" \
    CACHE_TTL="$CACHE_TTL" SIGNATURE_TTL="$SIGNATURE_TTL" \
    ENABLE_PROMPT_CACHE="$ENABLE_PROMPT_CACHE" \
    ENABLE_THINKING_SIGNATURE="$ENABLE_THINKING_SIGNATURE" \
    CONFIG_FILE="$CONFIG_FILE" DB_FILE="$DATA_DIR/signatures.sqlite3" python3 - <<'PY'
import json
import os

config = {
    "upstream_url": os.environ["UPSTREAM"].rstrip("/"),
    "proxy_port": int(os.environ["PORT"]),
    "enable_prompt_cache": os.environ["ENABLE_PROMPT_CACHE"] == "true",
    "enable_thinking_signature": os.environ["ENABLE_THINKING_SIGNATURE"] == "true",
    "cache_ttl": os.environ["CACHE_TTL"],
    "cache_affinity_user_id": os.environ["AFFINITY_ID"],
    "signature_ttl": int(os.environ["SIGNATURE_TTL"]),
    "signature_limit": 2048,
    "signature_db_path": os.environ["DB_FILE"],
    "dump_requests": False,
}
path = os.environ["CONFIG_FILE"]
temp = path + ".tmp"
with open(temp, "w", encoding="utf-8") as stream:
    json.dump(config, stream, indent=2, ensure_ascii=True)
    stream.write("\n")
os.chmod(temp, 0o600)
os.replace(temp, path)
PY
fi

cp "$ROOT_DIR/bin/anthropic-cache-bridge.py" "$INSTALL_DIR/bridge.py"
cp "$ROOT_DIR/bin/prompt_cache.py" "$INSTALL_DIR/prompt_cache.py"
cp "$ROOT_DIR/bin/thinking_signature.py" "$INSTALL_DIR/thinking_signature.py"
cp "$ROOT_DIR/bin/raw_json.py" "$INSTALL_DIR/raw_json.py"
cp "$ROOT_DIR/bin/acbctl" "$BIN_DIR/acbctl"
chmod 700 "$INSTALL_DIR"/*.py "$BIN_DIR/acbctl"

case "$(uname -s)" in
    Darwin)
        plist="$HOME/Library/LaunchAgents/$LABEL.plist"
        mkdir -p "$HOME/Library/LaunchAgents"
        python_path="$(command -v python3)"
        PYTHON_PATH="$python_path" BRIDGE_PATH="$INSTALL_DIR/bridge.py" \
        CONFIG_PATH="$CONFIG_FILE" \
        LOG_PATH="$DATA_DIR/bridge.log" LABEL="$LABEL" PLIST="$plist" python3 - <<'PY'
import os
import plistlib

data = {
    "Label": os.environ["LABEL"],
    "ProgramArguments": [os.environ["PYTHON_PATH"], os.environ["BRIDGE_PATH"]],
    "EnvironmentVariables": {
        "ANTHROPIC_CACHE_BRIDGE_CONFIG": os.environ["CONFIG_PATH"]
    },
    "RunAtLoad": True,
    "KeepAlive": {"SuccessfulExit": False},
    "StandardOutPath": os.environ["LOG_PATH"],
    "StandardErrorPath": os.environ["LOG_PATH"],
}
path = os.environ["PLIST"]
temp = path + ".tmp"
with open(temp, "wb") as stream:
    plistlib.dump(data, stream)
os.replace(temp, path)
PY
        if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
            launchctl bootout "gui/$(id -u)/$LABEL"
        fi
        launchctl bootstrap "gui/$(id -u)" "$plist"
        ;;
    Linux)
        unit_dir="$HOME/.config/systemd/user"
        unit="$unit_dir/$LABEL.service"
        mkdir -p "$unit_dir"
        cat >"$unit" <<EOF
[Unit]
Description=Anthropic Cache Bridge
After=network.target

[Service]
ExecStart=$(command -v python3) $INSTALL_DIR/bridge.py
Environment=ANTHROPIC_CACHE_BRIDGE_CONFIG=$CONFIG_FILE
Restart=on-failure
StandardOutput=append:$DATA_DIR/bridge.log
StandardError=append:$DATA_DIR/bridge.log

[Install]
WantedBy=default.target
EOF
        systemctl --user daemon-reload
        systemctl --user enable --now "$LABEL.service"
        ;;
    *) echo "Unsupported OS" >&2; exit 1 ;;
esac

echo
echo "Installed $NAME"
echo "Local endpoint: http://127.0.0.1:$PORT"
echo "Set your Anthropic-compatible client base URL to that endpoint."
echo "Run: $BIN_DIR/acbctl doctor"
