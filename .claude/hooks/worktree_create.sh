#!/usr/bin/env bash
# WorktreeCreate hook for Claude Code.
#
# Replaces the default worktree creation to also symlink .venv and .env into
# the new worktree, so agents can run `uv run` and access credentials without
# any extra setup.
#
# Receives JSON on stdin:
#   { "name": "bold-oak-a3f2", "session_id": "...", "cwd": "...", ... }
# Must print the absolute worktree path on stdout — nothing else.

set -euo pipefail

INPUT=$(cat)
NAME=$(echo "$INPUT" | python3 -c "import sys, json; print(json.load(sys.stdin)['name'])")

REPO_ROOT=$(git rev-parse --show-toplevel)
WORKTREE_PATH="$REPO_ROOT/.claude/worktrees/$NAME"
BRANCH="worktree-$NAME"

# Create the worktree on a new branch from current HEAD
git worktree add "$WORKTREE_PATH" -b "$BRANCH" >&2

# Symlink .venv so `uv run` works immediately without reinstalling
if [ -d "$REPO_ROOT/.venv" ]; then
    ln -sf "$REPO_ROOT/.venv" "$WORKTREE_PATH/.venv"
    echo "[worktree_create] symlinked .venv" >&2
fi

# Symlink .env so API keys and config are available
if [ -f "$REPO_ROOT/.env" ]; then
    ln -sf "$REPO_ROOT/.env" "$WORKTREE_PATH/.env"
    echo "[worktree_create] symlinked .env" >&2
fi

# Required: print the worktree path on stdout
echo "$WORKTREE_PATH"
