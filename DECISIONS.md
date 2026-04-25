# Battery Automation — Decisions & Knowledge

Last updated: 2026-04-25

## Goal

Mirror the Intelligent Octopus Go (IOG) cheap-rate signal onto the home battery: whenever IOG is making electricity cheap (because it has dispatched the EV charger off-window, or because we're inside the standard 23:00–05:30 window), force the Growatt SPH3000 to AC-charge from the grid. Stop charging when the cheap window ends.

Today the inverter only AC-charges in its scheduled night window, so when IOG dispatches the car at, say, 14:00, the EV pulls cheap grid power *and* the house battery, draining the battery at peak rate effectively for free to the car owner — exactly the wrong outcome.

## Hardware

| Component | Model | Notes |
|---|---|---|
| Inverter | Growatt **SPH3000** | Single-phase hybrid. Uses SPH/MIX register set & API surface. |
| Battery | Growatt **ARK 7.6L** (~7.68 kWh) | Charge target: 100%. |
| EV charger | **Hypervolt Home 3 Pro** | Cloud-only API, no local LAN endpoint exists. |
| Tariff | Octopus **Intelligent Octopus Go** | Standard cheap window: 23:00–05:30 (user-stated). |
| Host | Docker container on user's NAS | Long-running service, restart policy `unless-stopped`. |

## Cheap-rate detection logic (decided)

A boolean `cheap_now` is true when **any** of the following holds:
1. **Standard window**: local time is within 23:00–05:30 Europe/London. Ground truth, no API needed.
2. **Planned IOG dispatch**: Octopus GraphQL `plannedDispatches` returns a slot covering `now`.
3. **Active EV charge as fallback**: Hypervolt is drawing >1 kW (`ct_power > 1000`) AND it's outside peak hours that IOG never dispatches in. Used only as a corroborating signal for the case where Octopus's API misses an announcement (known to happen).

Combine as `cheap_now = standard_window OR planned_dispatch OR (hypervolt_charging AND not_obviously_peak)`.

Re-evaluate every 60s. The script writes to the inverter only on **edges** (false→true: enable AC-charge; true→false: disable) to stay under Growatt's rate limits.

## API choices (verified 2026-04-25)

### Octopus Energy — `BottlecapDave/HomeAssistant-OctopusEnergy` (reference) or direct GraphQL

- **Endpoint**: `https://api.octopus.energy/v1/graphql/`
- **Auth**: `ObtainKrakenToken` mutation with API key from <https://octopus.energy/dashboard> → JWT, ~1h TTL, refresh on 401.
- **Queries**: `plannedDispatches(accountNumber)` and `completedDispatches(accountNumber)`. Fields: `startDt`, `endDt`, `delta` (string), `meta { source, location }`. (Not `start`/`end`/`deltaKwh` as some older docs claim.)
- **Polling**: 2–5 min. No webhook/subscription exists.
- **Library decision**: copy BottlecapDave's queries and call them with `httpx` directly. Standalone PyPI clients (`open-octopus`, `python-octopus-energy`) are either too small or REST-only. ~50 lines.
- **March 2026 charge cap**: IOG now caps cheap-rate dispatches at 6h/day total. Doesn't change the API but worth knowing — once cap is hit, no more dispatches that day regardless of EV plug-in state.

### Hypervolt — vendor `hypervolt_api_client.py` from `gndean/home-assistant-hypervolt-charger`

- **Repo**: <https://github.com/gndean/home-assistant-hypervolt-charger> (active, v2.8.5 on 2026-04-20).
- **No standalone PyPI lib** and **no local LAN API**. The charger runs internally on a Pi but no local endpoint has been reverse-engineered.
- **Auth**: Keycloak `password` grant at `https://kc.prod.hypervolt.co.uk/realms/retail-customers/protocol/openid-connect/token` → bearer token. Email + password (the Hypervolt app login). No SSO. Requires app v5.3+ era credentials.
- **Live state**: WebSocket at `wss://api.hypervolt.co.uk/ws/charger/{charger_id}/sync`. Push, no polling needed for state changes.
- **Fields used**: `ct_power` (watts; primary signal — `> 1000` means actively charging) and `charging` (bool, from session-in-progress messages; fallback). We deliberately ignore `release_state`: verified against gndean's `hypervolt_device_state.py`, it's an enum tracking user-cancellation (`RELEASED`/`DEFAULT`), not power delivery.
- **Home 3 Pro**: fully supported, including V3-only fields (car-plugged-in flag, session charge mode).
- **Resilience**: WS can drop; reconnect with exponential backoff and a 60s REST fallback poll on the same auth.

### Growatt SPH3000 — primary: `growattServer` v2.1.0; fallback: local Modbus

- **Library**: `growattServer` 2.1.0 on PyPI (<https://github.com/indykoning/PyPi_GrowattServer>, last release 2026-04-16). Active.
- **Auth**: API token from ShinePhone app → Me → API Token (or `openapi.growatt.com` web UI → Account Management). One-time setup; user must request the token in-app.
- **Endpoint**: `openapi.growatt.com/v1/`. Avoid the legacy `server-api.growatt.com` and the username/password path — both rate-limit aggressively and the latter has locked accounts in the past.
- **SPH write call**: `OpenApiV1.sph_write_ac_charge_times(device_sn, charge_power, charge_stop_soc, mains_enabled, periods)`. `periods` is a list of three `{start_time, end_time, enabled}` dicts; we use period 1 for the live slot and disable periods 2 & 3.
- **Approach**: at `cheap_now` rising edge, write an AC-charge slot covering `[now, now+15min]` with enable=true. Refresh the slot every ~10 min while still cheap (extends end time). At falling edge, write enable=false. The 15-min sliding-window pattern is so a crashed script can't leave the inverter in force-charge mode for hours — the inverter will fall out of AC-charge naturally within 15 min if our process dies.
- **Standard 23:00–05:30 window**: same mechanism — the script owns this window too. The user's existing AC-charge schedule on the inverter will be cleared on first run (decided 2026-04-25); from then on every AC-charge slot is written by this service. This avoids two systems fighting over the slot.
- **Fallback if cloud writes flake**: local Modbus to the ShineWiFi-X / ShineLAN-X dongle (TCP) or RS485. SPH register map for AC-charge: **1090** (start, encoded as `(hour<<8)|minute`), **1091** (stop), **1092** (enable). Reference: `0xAHA/Growatt_ModbusTCP` and `bobbesnl/ModbusGrowatt_HomeAssistant`.

## Architecture (proposed)

Single Python service running in a long-lived loop:

```
┌─────────────────────────────────────────────────────────┐
│  battery_automation/                                    │
│    main.py                  ← orchestrator + edge logic │
│    decision.py              ← pure cheap_now logic      │
│    octopus.py               ← GraphQL plannedDispatches │
│    hypervolt.py             ← Keycloak auth + WS client │
│    growatt.py               ← growattServer wrapper     │
│    config.py                ← secrets from env / .env   │
└─────────────────────────────────────────────────────────┘
```

No persistence layer: the service is idempotent on restart. The startup
hook clears any pre-existing AC-charge schedule so the inverter state always
starts from a known-disabled baseline.

Loop cadence:
- Octopus polled every 120s.
- Hypervolt WS pushes state changes; reconnect on drop.
- Decision recomputed every 60s and on any WS event.
- Growatt write only on `cheap_now` edges, plus a 10-min keepalive while true.

Logging: plain-text lines to stdout (`%(asctime)s %(levelname)s %(name)s %(message)s`). The user can pipe into journald/Docker logs. JSON was considered but the deployment target reads logs through `docker logs`, where structured fields don't add enough value to justify the extra dependency.

Deploy: Docker container on the user's NAS, restart `unless-stopped`. Image runs the Python service directly; no orchestrator needed. Secrets injected via env vars or a bind-mounted `.env`.

## Decisions log

- 2026-04-25: Host = Docker container on NAS.
- 2026-04-25: Battery = ARK 7.6L; AC-charge target SoC = **100%**.
- 2026-04-25: Script owns the nightly 23:00–05:30 schedule (clears existing inverter schedule on first run).
- 2026-04-25: User has Hypervolt login & Growatt API token already; Octopus API key still to be obtained.
- 2026-04-25: AC-charge rate = inverter max (~3 kW for SPH3000).
- 2026-04-25: Full-battery during dispatch = no-op (just stop charging).
- 2026-04-25: Solar producing during a daytime dispatch = force grid AC-charge anyway.

## Notes

- Octopus rolled out a **6-hour daily cap on IOG cheap-rate dispatches in March 2026**. Doesn't change logic, just sets expectations on how often daytime dispatches happen.

## Credentials & inputs needed before first run

- [ ] **Octopus API key** — generate at <https://octopus.energy/dashboard/new/accounts/personal-details/api-access> (format `sk_live_...`).
- [ ] **Octopus account number** — `A-XXXXXXXX` shown on bills / dashboard.
- [x] **Growatt API token** — already obtained.
- [ ] **Growatt inverter serial** — visible in ShinePhone or on the unit sticker.
- [x] **Hypervolt app email + password** — already available.
- [ ] **Hypervolt charger ID** — visible in the Hypervolt app, or auto-discoverable via the API after login.

## Top risks (carried forward)

1. **Stale force-charge state.** If the script crashes mid-dispatch, the inverter could stay in AC-charge into peak rate. Mitigation: short sliding-window writes (15 min) that auto-expire, plus a watchdog that clears the schedule on startup.
2. **Octopus API misses a dispatch.** Hypervolt power-draw fallback covers this, but introduces a small risk of force-charging during a *manual* boost charge (user pays peak rate twice). Mitigation: only trust the Hypervolt-only signal during plausible dispatch hours, never 17:00–22:00 when the user might genuinely boost-charge at peak rate.
3. **Growatt cloud write flakiness.** Open HA regression on `write_ac_charge_times` (home-assistant/core#166817, Mar 2026). We're not using HA, so we sidestep it, but the underlying API still wobbles. Local Modbus is the parachute.

## Sources (verified during research)

### Octopus
- <https://developer.octopus.energy/graphql/reference/queries/>
- <https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy> (v18.2.1, 2026-04-16)
- <https://octopus.energy/blog/intelligent-octopus-go-charge-limit/> (March 2026 6h cap)
- <https://gist.github.com/livehybrid/1a54db5c52c508adeda18fa0eeb1b0b6> (verified field shape)

### Hypervolt
- <https://github.com/gndean/home-assistant-hypervolt-charger> (v2.8.5, 2026-04-20)
- <https://github.com/gndean/home-assistant-hypervolt-charger/blob/main/custom_components/hypervolt_charger/hypervolt_api_client.py>
- <https://blog.hypervolt.co.uk/en/engineering/hypervolt-app-5.3-release> (2025 auth change)

### Growatt
- <https://pypi.org/project/growattServer/> (v2.1.0, 2026-04-16)
- <https://github.com/indykoning/PyPi_GrowattServer>
- <https://github.com/0xAHA/Growatt_ModbusTCP> (local Modbus, multi-family writes)
- <https://github.com/bobbesnl/ModbusGrowatt_HomeAssistant> (SPH register map 1090/1091/1092)
- <https://github.com/home-assistant/core/issues/166817> (open HA regression, Mar 2026)
