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

For parallel work in worktrees, push to the worktree branch and let the human
merge — never merge into master unilaterally from a worktree.

## ⛔ NO LOCAL DOCKER

There is NO local Docker deployment. Do NOT run `docker compose up`, `docker build`,
`systemctl start docker`, or any Docker commands locally. There is no `docker-compose.yml`.

All testing and deployment targets the **prod VPS** at `emragent.404.mn` (77.42.17.207).
See `docs/DEPLOY.md` for deployment instructions. See the README for test instructions.

The `docker/` directory contains Dockerfiles and scripts used exclusively by the
**prod deployment pipeline** (`scripts/deploy.sh`). Do not use them locally.

## Parallel work

Multiple agents cannot be on different branches in the same working directory.
For parallel commit work, agents must use `isolation: "worktree"` which gives
each agent its own directory and branch (.claude/worktrees/<name>/).

When an agent finishes work in a worktree, report the branch name so the human
can merge it when ready. Do not merge into master unilaterally.

For read-only tasks (research, code exploration) parallelism in the same
directory is fine — no git state is changed.
