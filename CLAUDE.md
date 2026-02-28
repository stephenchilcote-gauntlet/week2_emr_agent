# Claude Code Instructions

## Setup (run once per clone)

    git config core.hooksPath .githooks

This activates the pre-push safety hook that blocks pushes from detached HEADs
and pushes that are behind origin (the most common multi-agent collision).

## Git discipline

Before committing or pushing, always run `git status` and `git branch` first.

If the working directory is on a detached HEAD or an unexpected branch, stop
and ask the human before proceeding — do not try to fix it unilaterally.

Before pushing, always check for divergence:
  git fetch origin && git log --oneline origin/master..HEAD

If origin/master is ahead, say so and ask the human how to proceed. Never
cherry-pick a subset of commits off a branch with more history behind it.

Never push directly to master from agent/parallel work — push to the worktree
branch and let the human merge.

## Parallel work

Multiple agents cannot be on different branches in the same working directory.
For parallel commit work, agents must use `isolation: "worktree"` which gives
each agent its own directory and branch (.claude/worktrees/<name>/).

When an agent finishes work in a worktree, report the branch name so the human
can merge it when ready. Do not merge into master unilaterally.

For read-only tasks (research, code exploration) parallelism in the same
directory is fine — no git state is changed.
