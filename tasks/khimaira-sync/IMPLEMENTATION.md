# khimaira sync — task #66

**Status**: spec landed 2026-05-14 in session `khimaira-6`. v1 implementation in flight (~1d effort budget).

**Origin**: chimera-extension's task #66. Joseph + chimera-extension hand-synced the laptop in ~30 sec (40 khimaira commits + 8 dotfiles + claude mcp cleanup + uv sync + monitor restart), then asked "how do we keep this in sync going forward." Manual git pull works but doesn't catch dep/MCP/monitor drift; bootstrap re-runs everything every time. `khimaira sync` is the targeted in-between.

---

## Current state (pre-task #66)

`khimaira sync` already exists at `packages/khimaira/src/khimaira/cli/bootstrap.py:74-93` and calls `run_sync()` from `khimaira.bootstrap.runner:139-180`. The existing behavior:

1. `git pull` the dotfiles repo via `ops.sync_dotfiles`
2. Re-apply symlinks (picks up new entries since last bootstrap)
3. Re-register MCP servers (idempotent — skips already-registered)
4. Re-apply Claude Code hooks (`install_claude_hooks`)

`runner.py:144` explicitly notes that sibling-repo git-pull is **TODO when needed** — that "when needed" is now.

## What task #66 adds

Per the spec from chimera-extension, the gaps vs. existing behavior:

1. **Sibling-repo git pull** — `git fetch + git merge --ff-only` on every repo declared in the profile's `repos` block, not just dotfiles.
2. **Dependency-change detection** — if any pulled repo's `pyproject.toml` or `uv.lock` changed, run `uv sync --all-packages` in the khimaira workspace once at the end.
3. **MCP drift reconcile** — detect drift via `claude mcp list` vs profile's `mcp_servers`. Currently re-register is idempotent-add; this adds idempotent-remove for stale entries that exist locally but not in profile. (Optional in v1 — current re-register flow is already mostly drift-correct.)
4. **Monitor-restart detection** — if `khimaira-monitor` daemon's in-memory boot time predates the latest khimaira commit, surface a "consider `systemctl --user restart khimaira-monitor`" line in the report. Auto-run via `--auto-restart` flag in v2.
5. **Unpushed-commits surface** — for every synced repo, if local `main` has unique commits not on `origin/main`, report it (e.g. "khimaira: 2 unpushed commits — push from this machine?"). Sync does not auto-push.
6. **Final report shape** — "X commits pulled, Y deps changed, Z MCP servers reconciled, monitor restart suggested: yes/no" (single line, parseable for cron post-processing).

## Decisions on the four open questions

From chimera-extension's spec:

| Q | Decision | Why |
|---|---|---|
| **1. Frequency expectation** — cron/systemd timer vs interactive | **Both**. Default verbose; `--quiet` for cron use (only output on changes or errors). | A timer-friendly mode unlocks the OSS "install + stay-current" story. |
| **2. Local commits ahead of origin** — report-and-bail or auto-push | **Report-and-bail**. Show "khimaira: 2 unpushed commits ahead of origin/main on machine `<host>`". | Auto-push from a sync command is too aggressive — user might be mid-edit on the other machine; surfacing the situation is enough for them to decide. |
| **3. Hook integration (SessionStart)** | **No**. | Adds boot latency. Sync is explicit/timer-driven only. |
| **4. Flag set v1** | **Minimal**: `khimaira sync` + `khimaira sync --check`. **No `--reset`** — document `khimaira bootstrap` as the reset path. | --reset would duplicate bootstrap. Keep the CLI surface narrow. |

## v1 scope (this evening)

Ship the load-bearing pieces:

1. **Sibling-repo git fetch + ff-only merge** (runner.py + new `ops.git_pull_repo`)
2. **uv sync auto-trigger** when any pulled repo touches `pyproject.toml` or `uv.lock`
3. **Unpushed-commits surface** (report-only, never push)
4. **`--check` flag** (dry-run preview, mirrors bootstrap's --check shape)
5. **`--quiet` flag** (silent on no-op; cron-friendly)
6. **Final summary report** ("X commits pulled, Y deps changed, ...")
7. **Tests** — fast unit tests against tmp git repos; no real-network operations

**Deferred to v2**:
- MCP drift reconcile (current re-register is already idempotent-add; idempotent-remove is a polish)
- Monitor-restart auto-run (suggestion-only in v1; `--auto-restart` flag in v2)
- Sibling-repo install-command re-run (only triggers if repo's install command changed; rare)

## File map

```
packages/khimaira/src/khimaira/bootstrap/operations.py
  + git_pull_repo(spec) → OpResult with metadata { commits_pulled, deps_changed, unpushed_count }
  + maybe_run_uv_sync(any_deps_changed) → OpResult (no-op if no flag)
  + check_unpushed(spec) → OpResult (informational; never fails)

packages/khimaira/src/khimaira/bootstrap/checks.py
  + check_git_pull_repo(spec) → OpResult (would-create / current; never side-effects)

packages/khimaira/src/khimaira/bootstrap/runner.py
  Extend run_sync():
    after dotfiles pull, iterate profile.repos
      → git_pull_repo each
      → collect any_deps_changed
    if any_deps_changed: maybe_run_uv_sync
    after pulls, for each repo: check_unpushed (report-only)
  New check_sync() mirroring check_bootstrap() shape, calls check_git_pull_repo + the existing dotfiles/symlinks/mcp/hooks checks.

packages/khimaira/src/khimaira/cli/bootstrap.py
  Extend _run_sync() to handle args.check + args.quiet
  Add --check + --quiet flags to the sync subparser

packages/khimaira/tests/test_bootstrap_sync.py (new)
  Test the new operations in isolation:
    - git_pull_repo on a tmp git repo (origin set up via fixture)
    - dep-change detection (touch pyproject in fixture, assert flag set)
    - unpushed detection (commit locally without push, assert count)
    - run_sync end-to-end with two fake repos
```

## Anti-patterns

- **Don't auto-push.** Even when there are unpushed commits and the user invoked sync — they might be mid-edit. Surface; don't act.
- **Don't fail-loud on minor drift.** A profile with one stale MCP entry shouldn't cause exit 1 — surface it as a warning in the report, let the user decide.
- **Don't shell out where Python git would do.** `subprocess.run(['git', 'fetch'])` is fine — keep it stdlib + subprocess, no GitPython dep.
- **Don't add `--reset`.** Document `khimaira bootstrap` as the reset path in the help text.
- **Don't run install commands on every sync.** Sibling-repo install (`uv sync`) is only needed when `uv.lock` changed; gate explicitly.

## Done when

- `khimaira sync` on the desktop after laptop has been pushing pulls cleanly: dotfiles + every sibling repo, surfaces dep-changes triggering uv sync, reports unpushed commits if any.
- `khimaira sync --check` returns drift-only output with no side effects.
- `khimaira sync --quiet` is silent on no-op (suitable for `systemd --user` timer).
- Tests cover happy path + drift detection + unpushed-commits report.
- `khimaira doctor` reflects the new check_sync behavior (drift-detect for sibling repos).

## Open follow-ups for v2

These would be follow-up commits / a future session:

- MCP drift reconcile — idempotent-remove for stale `claude mcp` entries not in profile
- `--auto-restart` flag — actually run `systemctl --user restart khimaira-monitor` instead of just suggesting
- Sibling-repo install re-run — only when the profile's `install:` command for a repo changed
- Cross-machine "you pulled X commits on machine A, push needed on machine B" reconciliation hint
- README install path documents `khimaira sync` as the post-bootstrap "stay current" command

## References

- Existing `run_sync` at `packages/khimaira/src/khimaira/bootstrap/runner.py:139-180`
- Existing `register_mcp` op at `packages/khimaira/src/khimaira/bootstrap/operations.py`
- Drift-check pattern at `packages/khimaira/src/khimaira/bootstrap/checks.py`
- chimera-extension's task #66 archive note id: `27ee0318f6e9` (use `session_search_archive(query="task #66")` to recover full body)
