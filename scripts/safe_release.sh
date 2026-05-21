#!/usr/bin/env bash
# Safely push current branch, trigger Windows build, verify the artifact
# actually contains expected fixes, and only then promote to the release.
#
# Catches the failure mode where git push gets silently rejected (e.g. large
# files, pre-receive hook) and subsequent commands run on stale origin.
#
# Usage: scripts/safe_release.sh
set -euo pipefail
# Note: 'shopt -s inherit_errexit' would be nice but isn't available on
# macOS's bundled bash 3.2.

REPO="ahmadmuradmalik/meeting-transcriber"
RELEASE_TAG="v0.1.0-test"
WORKFLOW="build-windows.yml"
ARTIFACT_NAME="MeetingTranscriber-windows-x64"
EXE_NAME="MeetingTranscriber.exe"

log() { echo "── $*"; }

# 1. Push and verify origin matches local HEAD
log "Pushing main..."
git push origin main
LOCAL_SHA=$(git rev-parse HEAD)
ORIGIN_SHA=$(git ls-remote origin main | awk '{print $1}')
if [[ "$LOCAL_SHA" != "$ORIGIN_SHA" ]]; then
  echo "✗ origin/main ($ORIGIN_SHA) does not match local HEAD ($LOCAL_SHA)" >&2
  exit 1
fi
log "✓ origin/main at $LOCAL_SHA"

# 2. Trigger workflow and capture the correct run ID by SHA
log "Triggering workflow $WORKFLOW..."
gh workflow run "$WORKFLOW"
sleep 6
RUN_ID=""
for _ in {1..10}; do
  RUN_ID=$(gh run list --workflow="$WORKFLOW" --limit=5 \
           --json databaseId,headSha,status \
           --jq ".[] | select(.headSha == \"$LOCAL_SHA\") | .databaseId" | head -1)
  [[ -n "$RUN_ID" ]] && break
  sleep 3
done
if [[ -z "$RUN_ID" ]]; then
  echo "✗ Could not find a workflow run at SHA $LOCAL_SHA" >&2
  exit 1
fi
log "✓ Build run $RUN_ID at SHA $LOCAL_SHA"

# 3. Watch to completion
log "Watching build..."
gh run watch "$RUN_ID" --exit-status

# 4. Download artifact
log "Downloading artifact..."
rm -rf "$ARTIFACT_NAME"
gh run download "$RUN_ID"
EXE_PATH="$ARTIFACT_NAME/$EXE_NAME"
[[ -f "$EXE_PATH" ]] || { echo "✗ Artifact missing: $EXE_PATH" >&2; exit 1; }
SIZE_MB=$(awk "BEGIN {printf \"%.1f\", $(wc -c <"$EXE_PATH") / 1024 / 1024}")
log "✓ Artifact: $EXE_PATH (${SIZE_MB} MB)"

# 5. Verify the bundle actually contains the expected fixes
log "Verifying bundled code..."
python3 scripts/verify_build.py "$EXE_PATH" || {
  echo "" >&2
  echo "✗ Verification failed: the build does not contain the expected fixes." >&2
  echo "  Do NOT release this build to the user." >&2
  exit 1
}

# 6. Promote to release
log "Promoting to release $RELEASE_TAG..."
gh release upload "$RELEASE_TAG" "$EXE_PATH" --clobber

log "✓ Done. Download URL:"
echo "  https://github.com/$REPO/releases/download/$RELEASE_TAG/$EXE_NAME"
