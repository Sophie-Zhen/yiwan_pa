#!/usr/bin/env bash
# Back up the bot's runtime data to Google Drive.
#
# WHY: data/ (todos, contracts, document fact-sheets + original PDFs) lives only
# on the Pi's SD card — it's gitignored and, unlike the Google Sheets, has no
# cloud copy. An SD-card failure would lose it. This mirrors data/ to Drive,
# keeping prior versions of anything changed/deleted.
#
# WHAT'S EXCLUDED: credentials/ (a secret, and re-downloadable from Google Cloud
# Console — don't sync it to Drive), the chat-history logs, and *.log files.
#
# ONE-TIME SETUP ON THE PI:
#   1. Install rclone:        curl https://rclone.org/install.sh | sudo bash
#   2. Configure a Drive remote named 'gdrive':
#        rclone config         # n → name "gdrive" → type "drive" → defaults
#      On a headless Pi, when it asks to auto-config, say no, then on your Mac:
#        rclone authorize "drive"
#      and paste the token back into the Pi's rclone config.
#   3. Run it once by hand to confirm it works:
#        ~/yiwan_pa/scripts/backup_data.sh
#   4. Schedule it (daily 03:00) — `crontab -e`, then add:
#        0 3 * * * /home/wenqiangli/yiwan_pa/scripts/backup_data.sh >> /home/wenqiangli/yiwan_pa/data/backup.log 2>&1
#
# Override the remote/path with RCLONE_REMOTE if you like (default below).
set -euo pipefail

DATA_DIR="$(cd "$(dirname "$0")/../data" && pwd)"
REMOTE="${RCLONE_REMOTE:-gdrive:yiwan_pa-backup}"
STAMP="$(date +%Y%m%d-%H%M%S)"

rclone sync "$DATA_DIR" "$REMOTE/current" \
  --exclude 'credentials/**' \
  --exclude 'history/**' \
  --exclude '*.log' \
  --backup-dir "$REMOTE/archive/$STAMP"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] backup done → $REMOTE/current (prior versions → archive/$STAMP)"
