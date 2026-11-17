#!/bin/sh
# Runs the app as PUID:PGID (default: Unraid's nobody:users = 99:100) so that files in /data are owned by that user.
set -e

PUID="${PUID:-99}"
PGID="${PGID:-100}"
DATA="${PUBCHEM_DATA_DIR:-/data}"
UMASK="${UMASK:-002}"
umask "$UMASK"

mkdir -p "$DATA"

if [ "$(id -u)" = "0" ] && [ "$PUID" != "0" ]; then
    # Only fix ownership of the top-level folders; never recurse over a 100+ GB database.
    for d in "$DATA" "$DATA/db" "$DATA/downloads" "$DATA/downloads/sdf" "$DATA/downloads/extras" \
             "$DATA/tmp" "$DATA/state" "$DATA/logs"; do
        mkdir -p "$d"
        chown "$PUID:$PGID" "$d" 2>/dev/null || true
    done
    for f in "$DATA"/state/* "$DATA"/logs/*; do
        [ -e "$f" ] && chown "$PUID:$PGID" "$f" 2>/dev/null || true
    done
    export HOME=/tmp
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups pubchem-local "$@"
fi

exec pubchem-local "$@"
