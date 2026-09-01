#!/bin/bash
# Pulls finished recording segments from the VPS down to the local
# external drive, then deletes them from the VPS to keep its disk
# small and cheap. Meant to run ONCE PER NIGHT (cron) on whichever
# Mac has the drive attached — no cloud storage cost involved.
#
# NOTE: since this only runs nightly, the VPS needs enough local disk
# to buffer a full day's segments (roughly 16-43GB/day depending on
# actual stream bitrate — check after your first day of recording and
# size the VPS's disk with that in mind, plus a missed-night buffer).

set -euo pipefail

VPS_HOST="youruser@your-vps-ip"
REMOTE_DIR="/opt/warehouse-recorder/segments/"
LOCAL_DRIVE="/Volumes/nia"
LOCAL_DIR="$LOCAL_DRIVE/warehouse-footage"
LOG_FILE="$HOME/warehouse-pull.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG_FILE"; }

if [ ! -d "$LOCAL_DRIVE" ]; then
    log "ERROR: drive not mounted at $LOCAL_DRIVE, skipping this run"
    exit 1
fi

mkdir -p "$LOCAL_DIR"

log "Starting pull from $VPS_HOST"
rsync -avz --remove-source-files \
    "$VPS_HOST:$REMOTE_DIR" "$LOCAL_DIR/" >> "$LOG_FILE" 2>&1

# clean up now-empty directories left behind on the VPS
ssh "$VPS_HOST" "find $REMOTE_DIR -type d -empty -delete" >> "$LOG_FILE" 2>&1

log "Pull complete"
