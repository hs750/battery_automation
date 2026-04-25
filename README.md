# battery_automation

Mirrors Octopus Intelligent Go cheap-rate signals onto a Growatt SPH3000 home battery, so the battery AC-charges from the grid whenever IOG has dispatched the EV charger off-window (and during the standard 23:00–05:30 window). See [`DECISIONS.md`](./DECISIONS.md) for the full design, API choices, and risks.

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
