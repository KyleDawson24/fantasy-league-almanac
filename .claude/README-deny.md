# Why `.claude/settings.json` carries a deny list (MLB-300, Tier 2)

Git protects only what is committed and pushed. This project keeps its most
valuable files OUT of git on purpose: `CLAUDE.md`, `Study Material.MD`,
`BRAINTHOUGHTS.md`, the real `dbt_league/league_config/` (skip-worktree),
`archives/anonymization/`, the byte-diff goldens under `tests/fixtures/`,
everything under `scratchpad/` (handoffs, kickoffs, `RELEASING.md`), and
`data/` (raw parquet, the DuckDB warehouse, the frozen fixture). A single
`git clean -fd`, `git checkout -- .`, `git stash` gone wrong, or an
`rm -rf` typed by an agent that misread a path would erase months of work
that no remote holds.

MLB-300 Tier 2 makes those commands physically unavailable to agents
running in this checkout. Claude Code evaluates `permissions.deny` before
any allow rule and regardless of the permission mode the session runs in,
so an agent cannot talk its way past the list.

## What is denied, in words

- Working-tree wipes: `git clean`, `git reset --hard`, `git reset --merge`,
  `git checkout -- <anything>`, `git checkout .`, `git restore`, `git stash`.
- Pushes of any kind: agents never push. Kyle runs the one sanctioned push,
  `git push dev main:main-public`, himself. `origin` (the public portfolio
  repo) receives pushes only at release cuts. `dev/main` is a frozen
  pre-rewrite backup that is not an ancestor of today's `main`; nothing is
  ever pushed to it.
- Ref deletion and history rewriting: `git branch -D/-d/--delete`,
  `git tag -d`, `git filter-repo`, `git filter-branch`, `git gc --prune`,
  `git prune`, `git reflog expire`, `git worktree remove`.
- Recursive deletes in every shell an agent can reach: `rm -r*` / `-fr*`,
  `Remove-Item -Recurse` (and its aliases `rm`, `ri`, `rd`, `rmdir`, `del`,
  `erase`), `rmdir /s`, `del /s`, and `cmd`-wrapped versions of those.
- Mirror copies that delete on the destination: `robocopy /MIR` and
  `robocopy /PURGE`.

Both the `Bash(...)` and `PowerShell(...)` tool families are covered, one
entry per line, because the desktop app on Windows exposes both.

## What to do instead

- A deletion is a MOVE into `scratchpad/_to_delete/<yyyy-mm-dd>/`. Kyle
  empties that folder by hand. Agents never write deletion routines.
- Discarding a change is `git diff` + a reviewed edit, never a wipe.
- Anything that needs a push stops and says what is ready.

## Tracking

`.claude/` is gitignored as a whole (editor state, worktrees,
`settings.local.json`). Two files are re-included by name so that they
ship with every clone: this README and `settings.json`. A stranger cloning
the public repo inherits the deny list; that is intended and harmless.
Per-machine additions belong in `.claude/settings.local.json`, which stays
ignored.
