# Agent Plugins

Drop-in agent files live in this folder.

Rules:
- File name must match `agent_*.py`.
- File must define `AGENT_ID`, `LABEL`, and `run_agent_turn(request, context)`.
- Optional fields: `DESCRIPTION`, `PARAMETERS`.

`dashboard.py` discovers these files at runtime and shows them in the **Agent profile** selector.
Removing a file removes that agent from the UI on next rerun.
