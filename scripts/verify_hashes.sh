#!/bin/bash
set -euo pipefail

BACKUP_DIR="/mnt/new-pool1"
SNAPSHOT_DIR="${1:-/mnt/recovery_test_30848}"

echo "Starting verification of 100 random files..."
echo "Comparing: $BACKUP_DIR against $SNAPSHOT_DIR"

find "$BACKUP_DIR" -type f -size +50M 2>/dev/null | shuf -n 100 | while read src; do
  # Map the backup path to the snapshot path
  dst="${src/$BACKUP_DIR/$SNAPSHOT_DIR}"
  
  if [ ! -f "$dst" ]; then
    echo "❌ MISSING in snapshot: $dst"
    continue
  fi

  echo "Hashing: $(basename "$src")..."
  sha1_src=$(sha1sum "$src" | awk '{print $1}')
  sha1_dst=$(sha1sum "$dst" | awk '{print $1}')
  
  if [ "$sha1_src" == "$sha1_dst" ]; then
    echo "  ✅ MATCH ($sha1_src)"
  else
    echo "  ❌ MISMATCH"
    echo "     Backup: $sha1_src"
    echo "     Snapshot: $sha1_dst"
  fi
done
