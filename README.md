# battery_automation

Mirrors Octopus Intelligent Go charging onto a Growatt SPH3000 home battery. The permanent overnight AC-charge window defaults to 23:30–05:30 and is configurable; dynamic charging is enabled only while an Octopus planned dispatch is active and Hypervolt confirms the EV is charging. Unknown or stale EV state does not trigger dynamic charging. See [`DECISIONS.md`](./DECISIONS.md) for the full design, API choices, and risks.

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
