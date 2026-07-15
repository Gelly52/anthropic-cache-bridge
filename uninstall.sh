#!/bin/sh
set -eu

NAME="anthropic-cache-bridge"
LABEL="io.github.anthropic-cache-bridge"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/$NAME"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/$NAME"
INSTALL_DIR="$HOME/.local/lib/$NAME"
BIN="$HOME/.local/bin/acbctl"

case "$(uname -s)" in
    Darwin)
        service="gui/$(id -u)/$LABEL"
        plist="$HOME/Library/LaunchAgents/$LABEL.plist"
        launchctl print "$service" >/dev/null 2>&1 && launchctl bootout "$service" || true
        rm -f "$plist"
        ;;
    Linux)
        systemctl --user disable --now "$LABEL.service" 2>/dev/null || true
        rm -f "$HOME/.config/systemd/user/$LABEL.service"
        systemctl --user daemon-reload
        ;;
esac

rm -f "$BIN"
rm -rf "$INSTALL_DIR"
echo "Removed service and binaries."
echo "Preserved configuration: $CONFIG_DIR"
echo "Preserved signatures/logs/backups: $DATA_DIR"
echo "Remove those directories manually only if you no longer need the data."
