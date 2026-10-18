# Changelog

## 1.0.0

A rewrite that brings the integration up to current Home Assistant standards and
makes it work with more feeds. Existing YAML configurations keep working with the
same entity IDs and attribute names.

### New

* **Set up from the UI.** Add a feed under *Settings → Devices & services*, then
  add a departure for each stop. Feeds and departures can be reconfigured later,
  for example to rotate an API key.
* **Route is optional.** Leave it out to show the next departure of any route
  from a stop (#3, #26).
* **More departures.** A new `departures` attribute lists up to 10 upcoming
  departures (#35). It is excluded from the recorder.
* **Unique IDs.** UI departures always have one. YAML departures accept an
  optional `unique_id` so they can be renamed and assigned to areas (#31).
* **Diagnostics** with credentials redacted, including which route IDs appear at
  each configured stop.
* **Clearer errors** when a URL returns a static GTFS zip, an HTML page or a
  text-format feed instead of a GTFS-realtime feed (#7, #24, #37).
* JSON GTFS-realtime feeds are accepted, and servers are asked for protobuf via
  the `Accept` header.

### Fixed

* "Due in" was off by hours when the operating system's timezone differed from
  Home Assistant's, for example in Docker (#23).
* Stops that only publish a departure time (often the first stop of a trip) and
  vehicles that are already at the stop now show up.
* Canceled trips and skipped stops no longer appear.
* `Occupancy` was reported as `EMPTY` when the feed didn't publish occupancy.
* Vehicles without a position were placed at 0°, 0° on the map.
* A failing vehicle positions feed no longer breaks departures.
* Feed requests have a timeout and no longer block Home Assistant (#10).
* The route ID is taken from vehicle positions when trip updates leave it out.
* Vehicles running frequency-based trips (several vehicles sharing one trip ID)
  are no longer mixed up; they're matched by start time.
* One out-of-range timestamp in a feed (for example in milliseconds) no longer
  breaks every sensor on it.

### Behavior changes

* With no upcoming departure the state is `unknown` instead of `-`. Home
  Assistant requires a numeric state for sensors with a unit. The `Due in`
  attribute still shows `-`.
* `Due at` and `Next bus` use Home Assistant's timezone (see the #23 fix).
* When a feed can't be fetched the sensor becomes `unavailable` instead of
  keeping old data, and Home Assistant no longer fails to set it up when the
  feed is down at startup.
* The sensor has the `duration` device class, so the frontend can format it.
  The value and unit (`min`) are unchanged.
* Requires Home Assistant 2025.6 or newer, and `gtfs-realtime-bindings` 2.2.0.

## 0.1.0

The original YAML-only integration. Installs made before this release was
published show version 1.0.1 in Home Assistant; that is the same code.
