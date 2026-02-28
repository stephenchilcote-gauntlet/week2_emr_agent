#!/usr/bin/env bash
# WorktreeRemove hook for Claude Code.
#
# Removes the symlinks we created, then removes the worktree and its branch.
#
# Receives JSON on stdin:
#   { "worktree_path": "/abs/path/to/worktree", ... }

set -euo pipefail

INPUT=$(cat)
WORKTREE_PATH=$(echo "$INPUT" | python3 -c "import sys, json; print(json.load(sys.stdin)['worktree_path'])")

# Remove symlinks (git worktree remove won't follow them, but cleaner to be explicit)
rm -f "$WORKTREE_PATH/.venv" "$WORKTREE_PATH/.env"

# Capture the branch name before removing the worktree
BRANCH=$(git -C "$WORKTREE_PATH" branch --show-current 2>/dev/null || true)

# Remove the worktree directory
git worktree remove "$WORKTREE_PATH" --force >&2

# Delete the branch if it's the auto-created worktree branch
if [[ "$BRANCH" == worktree-* ]]; then
    git branch -d "$BRANCH" 2>/dev/null || git branch -D "$BRANCH" 2>/dev/null || true
    echo "[worktree_remove] deleted branch $BRANCH" >&2
fi
