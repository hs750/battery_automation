# Battery Automation — Decisions & Knowledge

Last updated: 2026-11-22

## Goal

Keep the Growatt SPH3000's permanent AC-charge slot configured for the local cheap window (default 23:30–05:30, configurable). Use the dynamic slot for a confirmed IOG planned dispatch when Hypervolt confirms the EV is charging, or for an active Weekend Happy Hour booked on this account. No other Saving Session events are discovered, joined, or used to control the battery.

The permanent overnight slot is independent of the service's dynamic decision loop and remains configured if the service stops. The dynamic slot protects the home battery during confirmed IOG charging without enabling a charge based only on EV power draw. A booked Happy Hour can independently enable the same configured charge power and stop SoC, even when the EV is not charging.

## Hardware

| Component | Model | Notes |
|---|---|---|
| Inverter | Growatt **SPH3000** | Single-phase hybrid. Uses SPH/MIX register set & API surface. |
| Battery | Growatt **ARK 7.6L** (~7.68 kWh) | Charge target: 100%. |
| EV charger | **Hypervolt Home 3 Pro** | Cloud-only API, no local LAN endpoint exists. |
| Tariff | Octopus **Intelligent Octopus Go** | Configured cheap window defaults to 23:30–05:30 and can be changed with `CHEAP_WINDOW_START` / `CHEAP_WINDOW_END`. |
| Host | Docker container on user's NAS | Long-running service, restart policy `unless-stopped`. |

## Charging logic (current)

Charging has three paths:

1. **Permanent overnight window**: Growatt slot 1 is configured for the local window set by `CHEAP_WINDOW_START` and `CHEAP_WINDOW_END` (defaults: 23:30–05:30 Europe/London). It does not depend on an Octopus or Hypervolt signal or on the service remaining running.
2. **Dynamic IOG charging**: the decision is true only when an Octopus planned dispatch covers `now` **and** Hypervolt confirms the EV is charging. A false, unknown, or stale Hypervolt state does not enable IOG-driven dynamic charging. There is no Hypervolt-only fallback, and the overnight window is not part of this dynamic decision.
3. **Booked Weekend Happy Hour**: an active `WEEKEND_HAPPY_HOUR` in the account's `joinedEvents` enables the dynamic slot using the existing configured Growatt charge power and stop SoC. A public event without account participation does not qualify. The slot ends no later than the booking end or freshness deadline when Happy Hour is the only active reason; IOG charging is not shortened when both reasons overlap. The free-consumption allowance is not monitored, so some charging may be billed if it exceeds the allowance.

The dynamic decision is reevaluated every 60 seconds. On a false→true edge or a change in charging reason/end bound, the service writes a dynamic slot covering the current time through the next 15 minutes, capped at the booked-hour end when applicable; while the decision stays true, it refreshes that slot on a 10-minute keepalive. On a true→false edge, it clears the dynamic slot. Because the inverter receives time-of-day slot settings, do not assume that a dynamic slot automatically disappears if the process crashes before clearing it. Startup and normal shutdown clear the dynamic slot; the permanent overnight slot remains configured.

## Weekend Happy Hours

The Happy Hours poller is independent of EV plug state and shares the existing serialized Growatt decision/write path. It uses the Octopus backend GraphQL `savingSessions.account.joinedEvents` field with the existing Kraken token, requesting only event type and start/end timestamps. It does not query public events or send mutations; no Saving Session joins or free-hour bookings are automated.

The backend GraphQL API is not documented as a stable public customer API. Only a valid, active account-joined `WEEKEND_HAPPY_HOUR` causes charging; malformed booking data fails closed. Tests use fake clients and do not perform account-specific API calls or inverter control. [`SAVING_SESSIONS.md`](./SAVING_SESSIONS.md) is historical API research and does not describe the current runtime feature.

`HAPPY_HOURS_POLL_SECONDS` defaults to 120 and controls account-booking polling. `HAPPY_HOURS_STALE_SECONDS` defaults to 300; unknown or older account state disables Happy Hour charging. Neither value can be zero or negative. These replace the former `SAVING_SESSIONS_POLL_SECONDS` and `SAVING_SESSIONS_STALE_SECONDS` settings.

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

### Growatt SPH3000 — primary: `growattServer` v2.1.0 (Modbus fallback deferred, see below)

- **Library**: `growattServer` 2.1.0 on PyPI (<https://github.com/indykoning/PyPi_GrowattServer>, last release 2026-04-16). Active.
- **Auth**: API token from ShinePhone app → Me → API Token (or `openapi.growatt.com` web UI → Account Management). One-time setup; user must request the token in-app.
- **Endpoint**: `openapi.growatt.com/v1/`. Avoid the legacy `server-api.growatt.com` and the username/password path — both rate-limit aggressively and the latter has locked accounts in the past.
- **SPH write call**: `OpenApiV1.sph_write_ac_charge_times(device_sn, charge_power, charge_stop_soc, mains_enabled, periods)`. Each write specifies all three periods: slot 1 is the permanent cheap window, slot 2 is the dynamic slot, and slot 3 is disabled.
- **Slot ownership and lifecycle**: the service controls all three slots and overwrites their prior settings when it writes. At startup it asserts the permanent slot and clears the dynamic slot; at shutdown it clears the dynamic slot and retains the permanent slot. A service stop therefore does not remove the overnight schedule.
- **Dynamic-slot approach**: at the dynamic decision's rising edge or a change in charging reason/end bound, write an enabled slot covering `[now, now+15min]`, capped at the booked Happy Hour end when that is the only active reason; refresh it every ~10 min while the decision remains true. At the falling edge, disable slot 2. If the process stops ungracefully, slot 2 may remain configured until a later startup/write clears or replaces it, or it is cleared manually; do not treat the 15-minute period length as proof of automatic schedule removal.
- **Fallback if cloud writes flake**: *deferred — not implemented (2026-04-26)*. See "Modbus fallback (deferred)" below for what this would buy us and what it'd take to add later.

## Architecture (proposed)

Single Python service running in a long-lived loop:

```
┌─────────────────────────────────────────────────────────┐
│  battery_automation/                                    │
│    main.py                  ← orchestrator + edge logic │
│    decision.py              ← pure charge decision      │
│    octopus.py               ← shared GraphQL auth       │
│    happy_hours.py           ← read account bookings     │
│    hypervolt.py             ← Keycloak auth + WS client │
│    growatt.py               ← growattServer wrapper     │
│    config.py                ← secrets from env / .env   │
└─────────────────────────────────────────────────────────┘
```

No persistence layer: the service is idempotent on restart. At startup, it writes
its three-slot layout: it configures the permanent cheap-window slot, clears the
dynamic slot, and disables slot 3. This overwrites prior settings for those slots;
the permanent slot remains configured at shutdown.

Loop cadence:
- IOG dispatches polled every 120s (while the EV is plugged in or plug state is unknown).
- Weekend Happy Hour account bookings polled independently every 120s by default.
- Hypervolt WS pushes state changes; reconnect on drop.
- Decision recomputed every 60s and on any WS event.
- Dynamic Growatt slot writes on decision edges, plus a 10-min keepalive while active; every write also reasserts the permanent slot and disables slot 3.

Logging: plain-text lines to stdout (`%(asctime)s %(levelname)s %(name)s %(message)s`). The user can pipe into journald/Docker logs. JSON was considered but the deployment target reads logs through `docker logs`, where structured fields don't add enough value to justify the extra dependency.

Deploy: Docker container on the user's NAS, restart `unless-stopped`. Image runs the Python service directly; no orchestrator needed. Secrets injected via env vars or a bind-mounted `.env`.

## Decisions log

- 2026-04-25: Host = Docker container on NAS.
- 2026-04-25: Battery = ARK 7.6L; AC-charge target SoC = **100%**.
- 2026-04-25: Script owns the nightly 23:00–05:30 schedule (clears existing inverter schedule on first run). Historical design; superseded by the 2026-11-22 slot layout and 23:30 default below.
- 2026-04-25: User has Hypervolt login & Growatt API token already; Octopus API key still to be obtained.
- 2026-04-25: AC-charge rate = inverter max (~3 kW for SPH3000).
- 2026-04-25: Full-battery during dispatch = no-op (just stop charging).
- 2026-04-25: Solar producing during a daytime dispatch = force grid AC-charge anyway.
- 2026-11-22: Confirmed current behavior: permanent configurable overnight slot (default 23:30–05:30) plus a dynamic slot only for an active planned dispatch with confirmed EV charging; unknown/stale EV state fails closed, and no Hypervolt-only fallback is used. The service writes all three Growatt slots, with the permanent window retained if the service stops.
- 2026-11-22: First Saving Sessions iteration charges during account-booked Weekend Happy Hours using the existing dynamic slot. Automatic event joining is blocked because individual invitations cannot currently be confirmed. It does not adjust battery behavior during Power Down/Power Up, book Happy Hours, or monitor the free-consumption allowance.
- 2026-11-22: Simplified event automation to read-only Weekend Happy Hour account bookings. Removed public event discovery and all Saving Session join/booking behavior; charging during confirmed booked free hours and existing IOG/overnight behavior remain.

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

1. **Dynamic slot left active after a crash.** If the service stops ungracefully while a dynamic charge is active, slot 2 may remain configured; automatic removal after 15 minutes is not established by the code. Mitigation: normal shutdown and the next startup clear the dynamic slot; the permanent overnight slot remains configured independently.
2. **Missed dispatch or unavailable EV state.** If Octopus misses a dispatch or Hypervolt state is unknown/stale, dynamic charging is not enabled even if the EV is drawing power. This fail-closed behavior avoids charging on an unconfirmed Hypervolt-only signal but can leave the home battery supporting the EV during a cheap dispatch.
3. **Growatt cloud write flakiness.** Open HA regression on `write_ac_charge_times` (home-assistant/core#166817, Mar 2026). We're not using HA, so we sidestep it, but the underlying API still wobbles. No parachute today (Modbus fallback deferred — see below); a sustained Growatt cloud outage means no dynamic AC-charge writes. The permanent slot 1 keeps honoring the nightly window regardless.
4. **Octopus Happy Hours backend API can change.** Account booking data is read from a backend schema that is not documented as a stable customer API. Unknown response shapes fail closed, but API outages can cause booking data to become stale.
5. **Happy Hour consumption cap is not monitored.** The service charges during a confirmed booking using the existing configured power and SoC settings; it cannot tell when the free-consumption allowance is exhausted, so some consumption could be billed.

## Deferred work

### Modbus fallback for Growatt (deferred 2026-04-26)

**What it is.** Modbus is a 1970s industrial protocol that nearly every solar inverter still speaks natively. The SPH3000 exposes the AC-charge controls as numeric "holding registers" (specifically **1090** = slot start time, **1091** = slot end time, **1092** = enable flag — encoded as `(hour<<8)|minute` for the time fields). Two ways to reach those registers:

- **Modbus TCP** over the LAN, via the ShineWiFi-X / ShineLAN-X dongle on port 502. May be open or closed depending on dongle firmware (worth running `nmap -p 502 <dongle-ip>` before committing to the implementation).
- **Modbus RTU** over RS-485, with a USB-to-RS485 adapter (~£10) wired to the inverter's COM port.

Either path talks to the inverter directly with no dependency on Growatt's cloud, ShineServer, or our internet uplink.

**Why we'd want it.** Today the only path is `script → Growatt cloud → ShineServer → dongle → inverter`. Adding Modbus would buy us:

1. **Cloud-independent fallback.** Sustained Growatt cloud outages currently mean no dynamic AC-charge writes (the permanent slot 1 still fires nightly). A local path keeps the dynamic slot working through cloud outages.
2. **Read-back verification.** We could poll registers to confirm a write actually took effect, instead of trusting that a 200-OK from the cloud means the inverter obeyed. The current write-only design can't catch silent write failures.
3. **Latency and quotas.** Local writes are milliseconds and have no rate limit; the cloud round-trip is multi-second and quota-bounded.
4. **Resilience to Growatt token expiry / API breaking changes.**

**Why we deferred it.** The cloud path has been holding up; this is meaningful work for an unrealised problem. Specifically, deferring avoids:

- Adding `pymodbus` and a parallel control path that needs its own tests.
- Relying on a community-reverse-engineered register map (Growatt doesn't officially publish registers) — wrong-register risk against a battery is non-zero.
- Designing the fallback orchestration (try cloud first then local? always-local? health-check-driven failover?).
- Possibly buying and wiring an RS-485 adapter if the dongle's port 502 turns out to be closed.

**Trigger to revisit.** Any of: a Growatt cloud outage that prevents a planned dispatch from charging the battery; evidence (from logs / inverter state read-back when added) that cloud writes are silently dropping; or a Growatt API breaking change. References: [`0xAHA/Growatt_ModbusTCP`](https://github.com/0xAHA/Growatt_ModbusTCP), [`bobbesnl/ModbusGrowatt_HomeAssistant`](https://github.com/bobbesnl/ModbusGrowatt_HomeAssistant) (SPH register map 1090/1091/1092).

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
