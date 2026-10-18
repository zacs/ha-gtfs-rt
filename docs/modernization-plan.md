# GTFS-Realtime integration — modernization plan (v2.0.0)

This document records the plan, the design decisions behind it, and the
backwards-compatibility contract for the 2.0.0 rewrite. It is meant to be read
by reviewers and future maintainers.

## 1. Goals

1. **Modern Home Assistant integration.** UI setup (config flow), a
   `DataUpdateCoordinator`, fully async I/O on HA's shared HTTP session, typed
   `runtime_data`, unique IDs, devices, translations, diagnostics, and
   alignment with the Bronze/Silver tiers of the
   [integration quality scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/).
2. **Work with as many transit systems as possible** by following the
   GTFS-Realtime spec more closely. There is **no per-agency code**. Every
   improvement is either spec behavior or a generic HTTP/format detail.
3. **Don't break existing installs.** Existing YAML keeps working with the same
   entity IDs and the same attributes. The few intentional behavior changes are
   bug fixes or HA requirements, and section 5 lists each one.
4. **Tests and CI gating** so a change can't silently break users. That matters
   more here than usual: the repository has no releases, so HACS installs
   straight from `master`.

## 2. What was wrong (audit of 1.0.1)

| Problem | User impact | Issue(s) |
| --- | --- | --- |
| Arrival times are built with `datetime.fromtimestamp()` (OS timezone) and compared with `dt_util.now()` (HA timezone). | "Due in" is off by hours when the container timezone ≠ HA timezone (for example +240 min on a UTC container in New York). | #23, PR #41 |
| Blocking `requests.get` with **no timeout** runs in HA's executor pool. | "Update taking over 10 seconds" warnings. A hung server ties up a worker thread forever. | #10 |
| HTTP errors are logged, then parsing continues on the error body. | Cryptic `DecodeError` tracebacks instead of an actionable message. | #7, #24, #32, #37 |
| Only `arrival.time` is read. The `departure.time` fallback in the comments was never written. | No data at stops (typically the first stop of a trip) where the feed only publishes departures. | — |
| Canceled trips and skipped stops are not filtered out. | Sensors count down to buses that will never come. | — |
| `route` is required, even though the README says it's optional. | Can't build a "next departure from this stop, any route" sensor. | #3, #26 |
| `occupancy_status` is read without `HasField`, so the proto2 default wins. | Vehicles that don't report occupancy show **`EMPTY`**. | — |
| `position` is used without `HasField`. | A vehicle without a fix is shown at 0°, 0° on the map. | — |
| `vehicle_position_url` failures abort the whole update. | An optional enrichment feed can take down every sensor. | — |
| The sensor overrides `state` and returns `'-'` while declaring a unit. | Bypasses HA's numeric-sensor validation. Breaks statistics/graphs. | — |
| No unique IDs. | Can't rename, assign areas, or customize from the UI. | #31, #20 |
| YAML only, with a module-level `Throttle`. | Not configurable from the UI. One throttle is shared across every platform instance. | — |
| Hand-maintained `OccupancyStatus` enum. | Drifts from the spec (no `NOT_BOARDABLE` until recently). | — |
| `gtfs-realtime-bindings==1.0.0` pinned. No tests, no CI. | Every merge to `master` ships untested to HACS users. | #27 |

## 3. Architecture

```
custom_components/gtfs_rt/
├── __init__.py       config-entry setup/unload, reload on change
├── const.py          keys, defaults
├── feed.py           pure GTFS-RT logic: fetch, parse, build departures (no HA imports)
├── coordinator.py    one DataUpdateCoordinator per feed
├── sensor.py         one entity class, two setup paths: YAML platform and config entry
├── config_flow.py    feed entry (user, reconfigure) + "departure" subentry (add, reconfigure)
├── diagnostics.py    redacted config + feed summary for bug reports
├── icons.json
├── manifest.json
└── translations/en.json
```

Key decisions:

* **`feed.py` has no Home Assistant imports.** All the GTFS-RT handling (the
  part that decides which transit systems work) is plain Python, unit-tested
  with feeds built in code. This keeps HA plumbing and transit logic apart.
* **Data model: one config entry per feed, one subentry per departure.** A
  feed entry holds the trip-updates URL, the optional vehicle-positions URL, and
  HTTP headers. Each *departure* subentry (stop, optional route, name) becomes
  one sensor attached to its own service device. This mirrors the YAML shape
  (`platform` block → `departures` list) and core's own `mta` integration. One
  coordinator per feed, so N sensors on a feed still make one request per
  interval.
* **YAML stays a supported path, not an import.** The YAML platform builds the
  same coordinator and entity class. YAML is not auto-imported or deprecated
  (see section 7), because an import would change entity IDs (devices +
  `has_entity_name`) and attributes for users who never asked to migrate.
* **Reload on change via one update listener.** Adding or removing a departure
  subentry reloads the entry. The reconfigure flows only *update* the entry or
  subentry (the subentry flow's `async_update_and_abort`; for the feed entry,
  `async_update_entry` + abort, because `ConfigFlow.async_update_and_abort`
  doesn't exist in 2025.6) and let the listener do the single reload. This is
  the combination current HA supports. Recent HA raises if
  `async_update_reload_and_abort` is mixed with an update listener on
  subentries.
* **Polling.** 60 s, the same floor as the old `Throttle`. Between polls, each
  sensor recalculates "Due in" every 30 s from the last data, as 1.x did
  when HA polled the entity on the sensor default of 30 s. UI entries use a
  fixed interval (quality scale `appropriate-polling`). Users who need
  something else can disable polling and call `homeassistant.update_entity`.
  YAML honors `scan_interval` when it's **longer** than 60 s, which preserves
  the old effective behavior where the throttle ignored shorter values.

## 4. Supporting more transit systems (spec compliance, no special cases)

| Change | Why it's generic |
| --- | --- |
| All times are UTC-aware (`fromtimestamp(ts, UTC)` vs `dt_util.utcnow()`). "Due at" is rendered in HA's timezone. | GTFS-RT times are POSIX timestamps. Fixes #23 for every agency. |
| Use `arrival.time`, fall back to `departure.time`. | Both are spec-optional. Arrival stays first so existing sensors don't shift. |
| Skip trips whose `schedule_relationship` is `CANCELED`/`DELETED`, stops that are `SKIPPED`/`NO_DATA`, and entities with `is_deleted`. | Spec semantics. Looked up by enum *name* so it works on any bindings version. |
| If a TripUpdate has no `route_id`, use the route from the VehiclePosition for the *same trip*. A position matched only by vehicle ID could be for the vehicle's previous trip. | `route_id` is optional when `trip_id` is present. Some feeds only put it in vehicle positions. |
| `route` is optional per departure. Omitting it gives "next departure from this stop, any route". | Requested in #3. Works for any feed. |
| Occupancy and position are only reported when the feed sets them (`HasField`). Names come from the generated enum. | Removes fake `EMPTY` / 0°,0° values. Picks up new spec values for free. |
| Join TripUpdate → VehiclePosition by vehicle ID, then by trip (`trip_id` + `start_date` + `start_time`). A `trip_id` run by several vehicles at once is never guessed. | The spec identifies frequency-based trip instances by start time. 1.0.1 gave every such trip the last vehicle's position. |
| Timestamps that can't be represented (for example milliseconds) are skipped, not fatal. | One bad value no longer fails the update for every sensor on the feed. |
| `Accept: application/x-protobuf, application/octet-stream;q=0.9, */*;q=0.8` unless the user sets `Accept`. | Standard content negotiation. Some APIs return JSON or HTML unless asked for protobuf. |
| If the body is JSON (`{`), parse it with protobuf's own `json_format` (camelCase or snake_case). | A one-byte format check, no per-provider mapping. Covers feeds that only serve JSON (#29). |
| A body that won't parse, or parses to nothing, raises a clear error. The error recognizes static GTFS zips, HTML pages, and text-format protobuf (`?debug`). | Turns the most common setup mistakes (#7, #24, #37) into actionable messages. |
| 30 s request timeout. Protobuf parsing runs in the executor. | Large national feeds (for example HSL) no longer block the event loop (#10). |
| Vehicle-positions failures are logged once and the update continues without them. | Optional enrichment should never take down the primary data. |
| Any header-based auth (generic `headers` + the 3 legacy shortcuts). Query-string keys keep working inside the URL. | Already generic. Kept as is. |

## 5. Backwards-compatibility contract

**Guaranteed unchanged for YAML users** (each item is covered by tests):

* Every 1.0.1 YAML key is accepted with the same meaning: `trip_update_url`,
  `vehicle_position_url`, `headers`, `api_key`, `apikey`, `x_api_key` (still
  mutually exclusive), `departures[].name` (default "Next Bus"), `stopid`,
  `route`. Plus the platform keys `scan_interval` and `entity_namespace`. The
  README examples are parsed by the test suite.
* **Entity IDs** are unchanged (derived from `name`, no device, no
  `has_entity_name`).
* **State** is integer minutes until the next departure, truncated the same way.
  Unit is `min`.
* **Attributes**: same keys, same value formats (`Due in`, `Due at` as `HH:MM`,
  `Stop ID`, `Route`, `Occupancy`, `Next bus`, `Next bus due in`,
  `Next bus occupancy`, `latitude`, `longitude`).
* Icon `mdi:bus`.

**Intentional changes** (in the CHANGELOG):

| Change | Reason |
| --- | --- |
| With no upcoming departure, the state is `unknown` instead of `-`. The `Due in` attribute keeps `-`. | HA requires a numeric state for sensors with a unit. `-` broke history graphs and statistics. |
| `Due at` / `Next bus` use HA's configured timezone (previously the OS timezone). | Bug fix (#23). Identical when the two match. |
| `Occupancy` is `null` when the feed doesn't report it (previously `EMPTY`). | Bug fix. |
| `latitude`/`longitude` are omitted when the vehicle has no position (previously 0, 0). | Bug fix. |
| On feed errors the entity becomes `unavailable` instead of keeping stale data or failing setup. | Standard HA behavior. Setup no longer fails if the feed is down at boot. |
| Device class `duration` is set. | Lets the frontend format and convert the value. The state value and unit are unchanged. |
| New optional YAML keys: `route` may be omitted, `unique_id` per departure. New attribute `departures` (excluded from the recorder). | Additive. |
| Minimum Home Assistant **2025.6.0**. `gtfs-realtime-bindings==2.2.0`. | Config subentries need 2025.3+, and 2025.6 is a conservative floor. Bindings 2.2.0 has no protobuf pin, so it installs on every HA from 2025.6 to today. 3.0.0 requires protobuf ≥ 7.34 and would fail on HA < 2026.9. |

## 6. Quality-scale self-assessment (UI path)

Bronze: config-flow ✅, test-before-configure ✅ (feed is fetched and parsed),
unique-config-entry ✅ (abort on same trip-updates URL), entity-unique-id ✅,
has-entity-name ✅, runtime-data ✅, appropriate-polling ✅,
test-before-setup ✅ (`async_config_entry_first_refresh`),
config-flow-test-coverage ✅, dependency-transparency ✅, docs ✅,
brands ✅ (`custom_integrations/gtfs_rt` already exists in `home-assistant/brands`).

Silver: config-entry-unloading ✅, entity-unavailable ✅,
log-when-unavailable ✅ (coordinator + one-shot vehicle-feed warning),
parallel-updates ✅, test-coverage ✅ (CI floor 95%),
reauthentication-flow ➖ (reconfigure covers key rotation; see section 7).

Gold (partial): devices ✅, diagnostics ✅ (headers and URL query strings
redacted), reconfiguration-flow ✅ (entry and subentries),
entity-device-class ✅, icon-translations ✅, entity-translations ✅,
stale-devices ✅ (HA removes a subentry's device and entities when it's deleted).

## 7. Deliberately out of scope (and why)

* **Static GTFS (`stops.txt`, `stop_times.txt`).** It would unlock delay-only
  feeds (#36), human-readable stop/route names, and `stop_sequence`-only feeds.
  But it means downloading and indexing tens of MB of zip per agency, which is
  heavy on a Raspberry Pi and needs its own refresh schedule. If added later, it
  should be an opt-in `static_gtfs_url` on the feed entry, feeding the same
  `feed.py` pipeline.
* **Service alerts** (#21). Different message type with different UX
  (event/binary sensor). A good follow-up.
* **YAML auto-import / deprecation.** Keeping YAML costs ~40 lines because it
  shares the coordinator and entity. Revisit only if maintaining it becomes a
  burden. Then use an import flow + repair issue.
* **Reauth flow.** Reconfigure already lets users rotate keys without losing
  entities. Reauth can be added on top of the same form.
* **Per-agency workarounds** (Auckland's wrapped legacy JSON, STM metro
  licensing). Out of principle. The `Accept` header and the clear error message
  cover what can be covered generically.

## 8. Testing and gating

* `tests/test_feed.py`: spec behaviors in `feed.py` with feeds built in code
  (UTC times, fallbacks, cancel/skip, joins, occupancy, JSON, invalid inputs,
  HTTP errors without the URL in messages).
* `tests/test_sensor_yaml.py`: the compatibility contract in section 5. README
  and 1.x examples validate. Legacy attribute names and formats. Each auth
  shortcut produces the right header. `entity_namespace`, default name and
  `scan_interval` floor. Feed errors → unavailable → recovery.
* `tests/test_timezone.py`: issue #23 on the real clock, with the OS in Tokyo
  and HA in New York. (freezegun can't model this: it ignores the OS timezone
  for naive local times.) Putting the 1.0.1 time arithmetic back makes it fail
  with "Due in" 785 instead of 5.
* `tests/test_config_flow.py`, `tests/test_init.py`,
  `tests/test_diagnostics.py`: UI path, every error branch, reconfigure,
  subentry add/reconfigure/remove, unload, redaction.
* `tests/test_manifest.py`: the manifest requirement matches the version tests
  run against, and the manifest and `pyproject.toml` versions agree.
* **CI** (`.github/workflows/ci.yml`), all required:
  * `ruff check` + `ruff format --check`
  * `mypy --strict` on the integration
  * `pytest` with a 95% line+branch coverage floor (currently 100%) on **both**
    the minimum supported HA (2025.6.0, Python 3.13) and the latest HA
    (Python 3.14). The minimum job installs with that release's own
    `package_constraints.txt`. It has already caught a real bug: the entry
    reconfigure flow used an API that 2025.6 doesn't have.
  * `hassfest` (HA's manifest/translation/config-flow validator)
  * HACS validation (the `topics` check is skipped until the repository has
    GitHub topics. 1.0.1's `hacs.json` also used the removed `domains` key, which
    current HACS rejects.)
* Dependabot keeps the test harness and actions current, so a new HA release
  that breaks something shows up as a red PR instead of a user bug report.

## 9. Rollout

1. **Before merging, tag the current `master` as `v1.0.1` and publish a GitHub
   release.** HACS currently installs from the default branch because there
   are no releases. A release lets users pin or roll back from the HACS UI.
2. Merge, then publish **`v2.0.0`** with the CHANGELOG's migration notes.
3. Watch issues for a week. Every behavior change has a test, so regressions
   should land as targeted fixes.
