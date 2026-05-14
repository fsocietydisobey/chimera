# khimaira sync — pip-install mode awareness (v1.2)

**Status**: 🟢 **shipped 2026-05-14** by `khimaira-6` — see implementation in `packages/khimaira/src/khimaira/bootstrap/install_mode.py`, the new site-packages branch in `runner.run_sync`, and the `--auto-upgrade` CLI flag. Spec landed 2026-05-14 by `chimera-extension` (2cac13b6).
**Phase**: NORTH_STAR Phase 3 follow-up (gates the install-and-stay-current story for pip users). 🟢 complete.
**Last reviewed**: 2026-05-14
**Estimated effort (actual)**: ~2h end-to-end (mode-detection + upgrade orchestration + CLI flag + 30 tests).

> **🎯 Goal: make `khimaira sync` do the right thing for users who installed via `pip install khimaira` or `uvx khimaira`, not just for editable-install dev contributors.**

---

## The gap

Today's `khimaira sync` (task #66) is designed for the **editable-install dev workflow** Joseph uses:
- Clone `khimaira` to `~/dev/khimaira`
- `uv sync --all-packages` to get an editable install
- `khimaira sync` does `git pull` + `uv sync` + reapply profile bits

For users who `pip install khimaira` or `uvx khimaira` (the OSS-launch audience), this is broken:
- They don't have `~/dev/khimaira` as a git repo
- `khimaira sync` either no-ops, fails on the git-pull step, or silently re-registers MCP without actually checking for upgrades
- The user's mental model — "I installed khimaira via pip, `khimaira sync` keeps it current" — doesn't match what happens

The OSS install-and-stay-current story doesn't work until this is fixed.

## Two user populations, two definitions of "sync"

| Population | Installed how | What "sync" means |
|---|---|---|
| **Contributors** (Joseph et al.) | `git clone` + `uv sync --all-packages` (editable) | git pull repos, uv sync deps |
| **OSS users** | `uvx khimaira` or `pip install khimaira` (site-packages) | check PyPI for new version, `pip install -U khimaira` (or `uvx --upgrade`), re-apply hooks/MCP if profile changed |

## Design: mode-aware single command (Path A)

`khimaira sync` detects installation mode at runtime and branches behavior:

```python
def detect_install_mode() -> Literal["editable", "site-packages"]:
    """Return 'editable' if khimaira's __file__ is under a workspace
    checkout, 'site-packages' if it's under a venv's site-packages."""
    import khimaira
    path = Path(khimaira.__file__).resolve()
    if "site-packages" in path.parts:
        return "site-packages"
    if (path.parent.parent.parent / "pyproject.toml").exists():
        return "editable"
    return "site-packages"  # default to safer assumption
```

Branch behavior:
- **editable mode** → current behavior (git pull repos + uv sync + reapply profile)
- **site-packages mode** → check PyPI for new version → if newer, `pip install -U khimaira` (or detect uvx and use `uv tool upgrade khimaira`) → reapply profile

Same command, two behaviors, no new CLI surface to document.

## The community profile (related to task #62)

Today's `~/dotfiles/khimaira-profile.yaml` is **Joseph's personal dev profile** — it clones git repos. That's wrong for pip users. Phase 3.3 (task #45) and the task #62 evening block were supposed to ship a **community profile** with a different shape:

```yaml
name: khimaira-community
description: Default profile for pip-installed users.

# NO repos: section — khimaira is already installed via pip/uvx

# Optional dotfiles section — only if user opted in
# dotfiles:
#   repo: <user's own dotfiles>
#   symlinks: [...]

mcp_servers:
  - name: khimaira
    command: uvx khimaira mcp     # ← key difference: pip-installed entry point

supervisor:
  auto_install: true

spa_build: true
install_claude_hooks: true
```

This task assumes the community profile exists (or lands in the same shipment). If task #62 hasn't shipped it yet, this task should include profile creation as a step.

## Implementation outline

### Phase 1 — Mode detection + pip upgrade path (half-day)

1. Add `khimaira.bootstrap.runner.detect_install_mode()`.
2. In `run_sync()`, branch on detected mode:
   - editable: existing path (no change)
   - site-packages: new `check_and_upgrade_pip_package()` helper that:
     - Fetches `https://pypi.org/pypi/khimaira/json`
     - Compares released version vs `khimaira.__version__`
     - If newer: shell out to `pip install --upgrade khimaira` (or `uv tool upgrade khimaira` if installed via `uvx`/`uv tool`)
     - Reports `package-upgrade khimaira  [updated]` row in the standard sync output
3. After upgrade (or no-op), re-run the existing profile-apply steps (symlinks, MCP register, hooks). These work the same in both modes.

### Phase 2 — Profile shape for community users (half-day, may overlap task #62)

1. If `khimaira-profile.community.yaml` doesn't already exist (from task #62), create it.
2. Document the two profile shapes in `docs/PROTOCOL.md`:
   - dev profile: has `repos:` section
   - community profile: no `repos:` section, uses `uvx khimaira mcp` for the MCP command
3. The profile loader should validate: if `repos:` references exist but mode is `site-packages`, surface a warning ("This profile is set up for editable-install; you're pip-installed. Consider switching to the community profile.")

### Phase 3 — Tests + docs

1. Unit tests for `detect_install_mode()` — mock `khimaira.__file__` in both shapes.
2. Unit test for `check_and_upgrade_pip_package` against a mock PyPI response.
3. README install section gets the OSS-user flow:
   ```bash
   uvx khimaira install --profile <community-url>
   # Then later, anytime:
   khimaira sync
   # → checks PyPI, upgrades if needed, reapplies profile
   ```

## Open questions — resolved 2026-05-14 (khimaira-6 picking up implementation)

1. **Detection precision** — RESOLVED. Use `Path(khimaira.__file__).resolve()` and check whether `"site-packages"` appears in `parts`. Verified against the editable case (`.../packages/khimaira/src/khimaira/__init__.py` — no site-packages part) and the wheel case (`.../site-packages/khimaira/__init__.py`). uvx case also matches (`~/.local/share/uv/tools/khimaira/lib/python3.X/site-packages/khimaira/...`). Unit-tested both shapes.

2. **uvx vs pip distinction** — RESOLVED. Detect via `sys.executable` — if `uv/tools/khimaira/` appears in the path, the install came from `uvx`/`uv tool install` → use `uv tool upgrade khimaira`. Otherwise → `pip install -U khimaira`. No PATH probing needed (cheap, deterministic).

3. **Auto-upgrade vs prompt** — RESOLVED. Prompt by default; `--auto-upgrade` flag for cron mode. Mirrors `--auto-restart` deferral pattern. Default no-interaction (e.g. inside a shell pipeline or when stdin isn't a tty) falls back to no-op + suggestion (don't block).

4. **Sibling-package upgrades** — RESOLVED. Probe `importlib.metadata.distributions()` for installed `khimaira-*` distributions; pass the discovered list to the upgrade command explicitly. Avoids the `pip install -U 'khimaira[all]'` foot-gun (which would install missing siblings even when the user didn't ask for them). Concretely: if user has `khimaira` + `khimaira-types` + `khimaira-transport` + `khimaira-seance`, run `pip install -U khimaira khimaira-types khimaira-transport khimaira-seance`. Upgrade only what's already there.

5. **Hook integration (SessionStart upgrade-available warning)** — DEFERRED to v1.3. Cheap win but out of scope for the install-and-stay-current MVP. Open follow-up task to add after this lands.

## Validation hooks (how to know it works)

After Phase 1 + 2:
1. On a fresh laptop: `uvx khimaira install --profile <community>` works without git clones.
2. `khimaira sync` checks PyPI, reports current version status.
3. When a new khimaira release lands on PyPI, the laptop's next `khimaira sync` proposes (or auto-runs) the upgrade.
4. After upgrade, MCP + hooks are re-applied automatically — no manual `claude mcp add` needed.

## Connection to other tasks

- **Task #45** (Phase 3.3 — Community profile): this task assumes the community profile exists. If task #45 hasn't landed it, this task may absorb the work or block on it.
- **Task #66** (khimaira sync v1): builds directly on top. v1's mode-detection logic is the new piece.
- **Task #62** (Reframe NORTH_STAR + ship install story): the README install section khimaira-6 is writing should reflect the OSS-user flow that this task enables.

## Anti-scope

- **Not building a package registry of our own.** Read PyPI directly.
- **Not auto-rolling-back on failed upgrade.** If `pip install -U` fails, surface the error and bail. User can manually pin if they need to.
- **Not auto-uninstalling old khimaira-chimera transitional aliases** (if any). Out of scope.
