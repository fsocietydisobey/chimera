"""Runtime manager — Pillar 2 of chimera.

`chimera dev` orchestrates the project's full dev stack with one command:
detect dev server, spawn it, launch Chrome with --remote-debugging-port for
Specter, probe Postgres, hook into chimera-monitor for LangGraph runtime.

Single Ctrl-C tears it all down via the tracked process registry from
chimera/monitor/processes.py — that registry is shared, so daemon-side and
chimera-dev-side processes coexist without lifecycle confusion.
"""
