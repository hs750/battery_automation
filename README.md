# battery_automation

Mirrors Octopus Intelligent Go charging onto a Growatt SPH3000 home battery. The permanent overnight AC-charge window defaults to 23:30–05:30 and is configurable. Dynamic charging is enabled during a confirmed IOG dispatch while Hypervolt confirms the EV is charging, or during an active Weekend Happy Hour booked on this Octopus account. No other Saving Session events are monitored or acted on. See [`DECISIONS.md`](./DECISIONS.md) for design, API choices, and risks.

## Free Weekend Happy Hours

The service reads the account's joined-event records independently of EV plug state and charges only when one is an active `WEEKEND_HAPPY_HOUR`. It does not discover public events, join Power Down or Power Up events, or book Happy Hours. The backend GraphQL API is not documented as a stable customer API; [`SAVING_SESSIONS.md`](./SAVING_SESSIONS.md) is retained as historical API research, not a description of current event automation.

During a booked Happy Hour, the service uses the existing configured Growatt charge power and stop SoC. The dynamic slot is bounded by the booking end and data freshness deadline unless IOG charging is also active. Missing, malformed, or stale booking data does not enable Happy Hour charging. The free-consumption allowance is not monitored, so charging may incur cost after it is exceeded. Growatt schedules can recur after a crash, and failed cloud writes may delay clearing a slot.

`HAPPY_HOURS_POLL_SECONDS` (default `120`) and `HAPPY_HOURS_STALE_SECONDS` (default `300`) configure polling and the maximum age of booking data; both must be positive integers. These replace the former `SAVING_SESSIONS_POLL_SECONDS` and `SAVING_SESSIONS_STALE_SECONDS` names.

## Run locally

```sh
cp .env.example .env  # then fill in
pip install -e '.[dev]'
python -m battery_automation.main
```

## Run in Docker (intended deployment)

```sh
cp .env.example .env  # then fill in
docker compose up -d --build
docker compose logs -f
```

## Tests

```sh
pip install -e '.[dev]'
pytest
```
