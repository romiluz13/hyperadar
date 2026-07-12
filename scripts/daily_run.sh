#!/bin/bash
# HypeRadar daily agent runner — runs all 5 agents sequentially.
# Designed to be called by launchd daily or manually.
# Logs to integrations/runs/YYYY-MM-DD.log

set -euo pipefail

# launchd uses a minimal PATH — restore the full one
export PATH="/Users/rom.iluz/.local/share/mise/shims:/Users/rom.iluz/.local/share/mise/installs/node/24/bin:/Users/rom.iluz/.local/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

REPO_DIR="/Users/rom.iluz/Dev/hyperadar"
LOG_DIR="$REPO_DIR/integrations/runs"
TODAY=$(date +%Y-%m-%d)
LOG_FILE="$LOG_DIR/$TODAY.log"

mkdir -p "$LOG_DIR"

# Load env
set -a
source "$REPO_DIR/.env"
set +a

echo "========================================" | tee -a "$LOG_FILE"
echo "HypeRadar daily run — $(date)" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

AGENTS=("github_radar" "reddit_pulse" "youtube_trends" "hidden_gems" "weekly_digest")
SUCCESS=0
FAIL=0

for agent in "${AGENTS[@]}"; do
	echo "" | tee -a "$LOG_FILE"
	echo "=== @$agent ===" | tee -a "$LOG_FILE"
	echo "Started: $(date +%H:%M:%S)" | tee -a "$LOG_FILE"

	cd "$REPO_DIR/integrations/$agent"
	if uv run python main.py >>"$LOG_FILE" 2>&1; then
		echo "✓ $agent completed successfully" | tee -a "$LOG_FILE"
		SUCCESS=$((SUCCESS + 1))
	else
		echo "✗ $agent FAILED" | tee -a "$LOG_FILE"
		FAIL=$((FAIL + 1))
	fi
	echo "Finished: $(date +%H:%M:%S)" | tee -a "$LOG_FILE"
done

echo "" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
echo "Daily run complete: $SUCCESS succeeded, $FAIL failed" | tee -a "$LOG_FILE"
echo "Log: $LOG_FILE" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

# Exit non-zero if any agent failed (so launchd can alert)
exit $FAIL
