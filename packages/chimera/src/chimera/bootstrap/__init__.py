"""Profile-driven bootstrap — chimera as the dev's portable agent setup.

Two entry points:

  `chimera bootstrap [--profile <path|url>]` — first-run on a fresh
  machine. Clones dotfiles + sibling repos, applies symlinks, installs
  packages, registers MCP servers with Claude Code, optionally
  installs the supervisor + builds the SPA.

  `chimera sync` — ongoing across machines. `git pull` the dotfiles
  repo, then re-apply the manifest. Idempotent — skips operations
  already in the target state.

A profile is a YAML manifest the dev maintains in their own git repo
(typically alongside dotfiles). Same profile applied on N machines
yields N matching setups.

See `chimera/bootstrap/default_profile.yaml` for the baseline shipped
with chimera (chimera-only, no personal config) and `schema.py` for
the full manifest grammar.
"""

from chimera.bootstrap.schema import (
    DotfilesSpec,
    McpServerSpec,
    Profile,
    ProfileError,
    RepoSpec,
    SupervisorSpec,
    SymlinkEntry,
    dump_profile_json,
    load_profile,
)

__all__ = [
    "DotfilesSpec",
    "McpServerSpec",
    "Profile",
    "ProfileError",
    "RepoSpec",
    "SupervisorSpec",
    "SymlinkEntry",
    "dump_profile_json",
    "load_profile",
]
