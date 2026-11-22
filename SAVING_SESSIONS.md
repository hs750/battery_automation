# Octopus Saving Sessions and free-electricity slots

> Historical API research only. The current service does not discover or join Saving Sessions. It reads account-booked Weekend Happy Hours for charging via `src/battery_automation/happy_hours.py`.

Researched 22 November 2026. **In brief:** Octopus exposes Saving Session events and account participation through GraphQL. Per-session opt-in is possible through a backend GraphQL mutation. The free hour earned through Saving Sessions is called a **Weekend Happy Hour**; its booking mutation is present in Octopus's backend schema and has been tested by a community integration, but I found no Octopus-published developer documentation promising that mutation as a supported public API.

## Findings

### Saving Session availability and opt-in

- The published GraphQL reference documents `customerFlexibilityCampaignEvents(accountNumber, supplyPointIdentifier, campaignSlug, ...)`. Its event records include `code`, `startAt`, `endAt`, and `isEventParticipant`, so an account-scoped client can discover event times and participation. The schema accepts a string campaign slug; it does not document the valid slug list.
- Octopus's backend GraphQL schema also exposes `savingSessionsEvents` (event codes, start/end, `status`, and `eventType`) and account `joinedEvents`. The schema includes `joinSavingSessionsEvent`, whose input requires `accountNumber` and `eventCode`. This is evidence that joining through the backend API is possible, not a guarantee of a stable, officially supported customer API. A community Node-RED flow demonstrates this mutation; the official FAQ confirms that customers must opt in before a session starts.
- **Export:** Yes, it can contribute to a **Power Down** result if the export MPAN is associated with the import MPAN signed up to Saving Sessions. Octopus calculates the reward using the net change across import and export against the baseline. This does not mean each extra exported kWh is independently rewarded, nor does it apply as a Power Up reward (which rewards increased use).

### Free-electricity hour: Weekend Happy Hour

- Octopus's current scheme page describes Weekend Happy Hours as free weekend electricity earned with tokens from successful Power Down participation. Slots are selected for a chosen hour; consumption is refunded up to 16 kWh. Token requirements can vary, so one Saving Session does not necessarily earn a complete free hour.
- For event-wide detection, `savingSessionsEvents` returns Happy Hour records with `eventType: WEEKEND_HAPPY_HOUR`, `status`, `startAt`, and `endAt`. For a user's actual booking, inspect the account's `joinedEvents` and match its type and time window; a nationally `ONGOING` event alone does **not** prove that this account booked it.
- For booking, the backend schema exposes `bookSavingSessionsWeekendHappyHourEvent`, with required `accountNumber` and `eventCode` inputs. A community report describes a successful live booking and says the result appeared in the account's joined events. Use this mutation for a Happy Hour, not `joinSavingSessionsEvent` (the community report found that the latter rejects Happy Hour codes).
- **Terminology caveat:** the developer API's generic campaign query is separate from the Weekend Happy Hour booking flow. A community report uses `campaignSlug: "free_electricity"`, but the public schema only specifies `campaignSlug` as a string and I found no documented slot-booking mutation for that separate campaign. Do not assume the Happy Hour booking mutation books a `free_electricity` campaign event.

### Live API check

On 22 November 2026, an unauthenticated read of the backend event list returned Happy Hour event `EVENT_149_221126` as `ONGOING`, from 12:00 to 13:00 UTC. This demonstrates that the API can identify a currently running event; it does not establish any particular customer's eligibility or booking.

## Sources

- [Octopus Saving Sessions FAQs](https://octopus.energy/blog/saving-sessions-faqs) — opt-in timing, baseline, and combined import/export calculation.
- [Octopus Saving Sessions terms](https://octopus.energy/Octoplus-terms-and-conditions) — export MPAN association and net-change condition.
- [Octopus Happy Hours](https://octopus.energy/saving-sessions/happy-hours) — tokens, choosing a weekend slot, and the 16 kWh cap.
- [Octopus GraphQL API reference: `customerFlexibilityCampaignEvents`](https://developer.octopus.energy/graphql/reference/queries/customerflexibilitycampaignevents) — documented query arguments and campaign event fields.
- [Octopus backend GraphQL endpoint](https://api.backend.octopus.energy/v1/graphql/) — live schema used to verify `savingSessionsEvents`, account joined-event fields, and mutation names; this is schema evidence, not published API support documentation.
- [Community Happy Hour API investigation](https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy/issues/1845) — reports the booking mutation and a successful account booking.
- [Community Saving Sessions API example](https://flows.nodered.org/flow/83a2ff80d99599599def737197829b7e) — demonstrates querying events and `joinSavingSessionsEvent`.
- [Community campaign-query discussion](https://github.com/springfall2008/batpred/issues/2741) — reports using the `free_electricity` campaign slug; not an Octopus specification.