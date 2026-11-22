# Working on battery automation

## Project purpose

This service automates AC charging of a Growatt SPH3000 home battery around the user's Intelligent Octopus Go (IOG) EV charging. Octopus supplies planned dispatch information, Hypervolt supplies EV charging state, and the service writes charge schedules to the Growatt inverter. These writes affect real hardware and electricity costs.

## Project map

- `src/battery_automation/main.py` orchestrates polling, decision ticks, Growatt writes, and shutdown.
- `src/battery_automation/decision.py` contains pure logic for the dynamic charging decision.
- `src/battery_automation/octopus.py` handles Octopus dispatch data; `hypervolt.py` handles charger state; `growatt.py` writes inverter schedules.
- `src/battery_automation/config.py` loads settings and credentials from environment variables and `.env`.
- `tests/` contains pytest coverage for configuration, decision logic, API clients, and service behavior.
- `DECISIONS.md` records user decisions, design context, and deferred work. Read it before changing behavior or adding integrations.
- `README.md`, `pyproject.toml`, `Dockerfile`, and `docker-compose.yml` document local development and deployment.

## Charging behavior and decision records

The current implementation configures Growatt slot 1 as a permanent cheap-window fallback and uses slot 2 for a short-lived dynamic charge window; slot 3 remains disabled. The permanent slot is asserted on startup and each Growatt write, while the dynamic slot is cleared on startup and shutdown. The dynamic decision is true only when an Octopus planned dispatch is active **and** Hypervolt reports that the EV is charging. It is evaluated periodically, with a keepalive while active.

There are known differences between `DECISIONS.md` and the implementation that must not be silently resolved:

- The decision record describes a 23:00 start, while the code default is 23:30 (`CHEAP_WINDOW_START`); both use 05:30 as the end by default.
- The decision record specifies an OR-based combination including the standard window and a Hypervolt fallback. In the current code, the overnight window is handled by the permanent inverter slot, and the dynamic slot requires both an active planned dispatch and active EV charging.
- The decision record describes clearing the existing inverter schedule; the current client instead maintains the permanent slot and clears only the dynamic slot.

Treat the code as evidence of current behavior and `DECISIONS.md` as the record of user intent and prior choices. Before changing charging criteria, schedules, charge targets, or inverter ownership, surface the discrepancy and get the user's direction. When the user makes a new design decision, update `DECISIONS.md` as well as implementing it; do not rewrite historical decisions as if they were always current.

## Development and validation

- Use Python 3.12 or newer. Install the project and development tools with `pip install -e '.[dev]'`.
- Run tests with `pytest`; run lint with `ruff check .` and check formatting with `ruff format --check .`. Follow the 100-character line length and Python 3.12 target in `pyproject.toml`.
- Keep decision logic independently testable and use the existing fake clients in service tests. Tests should not require real Octopus, Hypervolt, or Growatt credentials or make live API calls.
- For behavior changes, add or update focused tests for normal and failure/unknown-state cases. Run the relevant tests, then the full suite when appropriate.
- The README's local setup tells users to copy `.env.example`, but that file is not currently present in the repository. Do not assume it exists; use the required and optional variable names in `config.py`, and keep actual credentials out of tracked files, logs, and test fixtures.

## Configuration and operational safety

Required environment variables are `OCTOPUS_API_KEY`, `OCTOPUS_ACCOUNT_NUMBER`, `HYPERVOLT_EMAIL`, `HYPERVOLT_PASSWORD`, `GROWATT_API_TOKEN`, and `GROWATT_DEVICE_SN`. Optional settings and their defaults are defined in `config.py`, including the cheap-window start/end, charge power and stop SoC, and polling/decision intervals.

Starting the service (including `docker compose up`) can write inverter schedules at startup, during decisions, and at shutdown. Do not run the service, deploy/restart the container, or make live Growatt writes unless the user explicitly authorizes live operation. Prefer unit tests with fake clients for validation. Never include secret values in examples or commits.

Keep changes focused, preserve existing async and logging patterns, and update user-facing documentation when configuration or operational behavior changes.