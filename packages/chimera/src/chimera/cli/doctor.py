"""`chimera doctor` — diagnostic of the dev's environment.

Reports what chimera can see:
  - Which CLI runners are installed (Claude Code, Codex, Gemini, Ollama, llm)
  - Whether at least one is usable
  - Privacy mode (CHIMERA_LOCAL_ONLY)
  - Routing-table source (defaults / user / project)

Exits 0 when at least one runner works. Non-zero when chimera can't dispatch
anything — which is the failure mode `doctor` exists to detect.
"""

from __future__ import annotations

import argparse
import os

from chimera.config import is_local_only_mode
from chimera.dispatch.runners import RUNNERS, available_runners


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "doctor",
        help="Diagnose your chimera environment — which runners, which mode.",
    )
    p.set_defaults(func=run)


def run(_args: argparse.Namespace) -> int:
    print("chimera doctor")
    print("=" * 60)

    # Runners
    print("\nCLI runners:")
    avail = available_runners()
    for name, runner in RUNNERS.items():
        marker = "✅" if avail[name] else "❌"
        cmd = getattr(runner, "cmd", "?")
        default_model = getattr(runner, "default_model", "?")
        if avail[name]:
            print(f"  {marker} {name:8s} → {cmd!r:20s}  default model: {default_model}")
        else:
            print(f"  {marker} {name:8s} → {cmd!r:20s}  NOT FOUND")

    # Modes
    print("\nModes:")
    print(f"  privacy (CHIMERA_LOCAL_ONLY): {'on' if is_local_only_mode() else 'off'}")

    # Env vars worth surfacing
    relevant_env = [
        "CHIMERA_CLAUDE_CMD", "CHIMERA_CLAUDE_MODEL",
        "CHIMERA_CODEX_CMD", "CHIMERA_CODEX_MODEL",
        "CHIMERA_GEMINI_CMD", "CHIMERA_GEMINI_MODEL",
        "CHIMERA_OLLAMA_CMD", "CHIMERA_OLLAMA_MODEL",
        "CHIMERA_LLM_CMD", "CHIMERA_LLM_MODEL",
        "CHIMERA_LOCAL_ONLY",
    ]
    set_vars = [(k, os.environ[k]) for k in relevant_env if k in os.environ]
    if set_vars:
        print("\nOverrides set:")
        for k, v in set_vars:
            print(f"  {k}={v}")

    # Verdict
    any_available = any(avail.values())
    print()
    if any_available:
        print(f"✅ chimera is operational ({sum(avail.values())}/{len(avail)} runners installed).")
        if not avail.get("ollama"):
            print(
                "   Tip: install Ollama for free local fallback — "
                "https://ollama.com/download"
            )
        return 0
    print("❌ NO runners installed. chimera cannot dispatch any tasks.")
    print("   Install at least one of:")
    print("     • Claude Code:  https://claude.com/claude-code")
    print("     • Codex CLI:    npm install -g @openai/codex")
    print("     • Gemini CLI:   npm install -g @google/gemini-cli")
    print("     • Ollama:       https://ollama.com/download")
    print("     • llm:          pip install llm")
    return 1
