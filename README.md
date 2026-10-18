# Home Assistant GTFS-Realtime

Real-time departure sensors for any transit agency that publishes a
[GTFS-realtime](https://gtfs.org/realtime/) feed: minutes until the next
bus, train or ferry, the departures after it, and (when the agency publishes
it) where the vehicle is and how full it is.

* Set up from the UI, or keep using YAML.
* One sensor per stop, optionally limited to one route.
* Vehicle location on the map and occupancy, from an optional vehicle positions feed.
* Works with any header- or URL-based API key, and with protobuf or JSON feeds.

Requires Home Assistant 2025.6 or newer.

![screenshot](https://i.imgur.com/VMcX9aG.png)

## Installation

### HACS (recommended)

1. Install [HACS](https://hacs.xyz/docs/use/).
2. In HACS, open the menu → **Custom repositories**, add
   `https://github.com/zacs/ha-gtfs-rt` with type **Integration**.
3. Install **GTFS-Realtime** and restart Home Assistant.

### Manual

Copy `custom_components/gtfs_rt` into `<config>/custom_components/` (create the
`custom_components` folder if needed) and restart Home Assistant.

## Setup from the UI

1. **Settings → Devices & services → Add integration → GTFS-Realtime.**
2. Enter the agency's **trip updates URL**. Optionally add a **vehicle
   positions URL** and any **HTTP headers** the agency requires, for example:
   ```text
   x-api-key: YOUR_KEY
   ```
   The feed is downloaded and checked before it is saved.
3. On the integration's page, choose **Add departure** for each stop you want to
   track. Enter the **stop ID**, and optionally a **route ID** (leave it empty to
   see the next departure of any route) and a **name**.

Each departure becomes a device with one sensor, so you can assign it to an
area. To change a URL or rotate an API key, use **Reconfigure** on the feed;
your sensors are kept.

## Setup with YAML

YAML configuration from 0.1.0 keeps working unchanged. Each `platform` block is
one feed, with a list of departures:

```yaml
# Austin, TX
sensor:
  - platform: gtfs_rt
    trip_update_url: "https://data.texas.gov/download/rmk2-acnw/application%2foctet-stream"
    vehicle_position_url: "https://data.texas.gov/download/eiei-9rpf/application%2Foctet-stream"
    departures:
      - name: Downtown to airport
        route: 100
        stopid: 514
```

```yaml
# Seattle, WA
sensor:
  - platform: gtfs_rt
    trip_update_url: "http://api.pugetsound.onebusaway.org/api/gtfs_realtime/trip-updates-for-agency/1.pb?key=TEST"
    vehicle_position_url: "http://api.pugetsound.onebusaway.org/api/gtfs_realtime/vehicle-positions-for-agency/1.pb?key=TEST"
    departures:
      - name: "48 to Uni"
        route: 100228
        stopid: 36800
```

```yaml
# Montréal (API key in an "apikey" header)
sensor:
  - platform: gtfs_rt
    trip_update_url: "https://api.stm.info/pub/od/gtfs-rt/ic/v2/tripUpdates"
    vehicle_position_url: "https://api.stm.info/pub/od/gtfs-rt/ic/v2/vehiclePositions"
    apikey: !secret stm_api_key
    departures:
      - name: "Bus 178"
        route: 168
        stopid: 56698
```

```yaml
# New York City subway (API key in an "x-api-key" header)
sensor:
  - platform: gtfs_rt
    trip_update_url: "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-bdfm"
    x_api_key: !secret mta_api_key
    departures:
      - name: "Brooklyn F"
        route: "F"
        stopid: "F16S"
```

```yaml
# Boston
sensor:
  - platform: gtfs_rt
    trip_update_url: "https://cdn.mbta.com/realtime/TripUpdates.pb"
    vehicle_position_url: "https://cdn.mbta.com/realtime/VehiclePositions.pb"
    departures:
      - name: "MBTA Red Line Kendall/MIT to Ashmont/Braintree"
        route: "Red"
        stopid: "70071"
      - name: "Anything leaving Kendall/MIT"
        stopid: "70071"
```

```yaml
# Any other header, for example Auckland Transport
sensor:
  - platform: gtfs_rt
    trip_update_url: "https://api.at.govt.nz/realtime/legacy/tripupdates"
    headers:
      Ocp-Apim-Subscription-Key: !secret at_api_key
    departures:
      - name: Hobsonville to Downtown
        route: "HOBS-209"
        stopid: "21779-d98fae38"
        unique_id: hobsonville_ferry
```

| Option | Required | Description |
| --- | --- | --- |
| `trip_update_url` | yes | The GTFS-realtime TripUpdates feed. |
| `vehicle_position_url` | no | The GTFS-realtime VehiclePositions feed, used for location and occupancy. |
| `headers` | no | HTTP headers sent to both feeds, e.g. an API key header. |
| `api_key` / `apikey` / `x_api_key` | no | Shortcuts for an `Authorization`, `apikey` or `x-api-key` header. Use at most one of these or `headers`. |
| `departures` | yes | The stops to track. |
| `departures[].stopid` | yes | The stop's `stop_id`. |
| `departures[].route` | no | The route's `route_id`. Leave it out to show any route. |
| `departures[].name` | no | Sensor name. Defaults to `Next Bus`. |
| `departures[].unique_id` | no | Lets you rename the sensor and assign it to an area from the UI. |
| `scan_interval` | no | Seconds between updates. Values below the default of 60 are ignored. |

API keys passed as query parameters can stay in the URL. Keep keys in
[`secrets.yaml`](https://www.home-assistant.io/docs/configuration/secrets/).

## The sensor

The state is the number of minutes until the next departure, or `unknown` when
nothing is scheduled. Attributes:

| Attribute | Description |
| --- | --- |
| `Due in` | Same as the state (`-` when nothing is scheduled). |
| `Due at` | Time of the next departure (`HH:MM`). |
| `Stop ID`, `Route` | The configured stop and route. |
| `Occupancy` | Occupancy of the next vehicle, e.g. `MANY_SEATS_AVAILABLE`, if published. |
| `latitude`, `longitude` | Position of the next vehicle, if published. Shows the sensor on the map. |
| `Next bus`, `Next bus due in`, `Next bus occupancy` | The same for the departure after that. |
| `departures` | Up to 10 upcoming departures, each with `route_id`, `trip_id`, `vehicle_id`, `time`, `due_in` and `occupancy`. Not recorded in history. |

Arrival times are used when the feed publishes them, otherwise departure times.
Canceled trips and skipped stops are left out.

## Finding feeds, stop IDs and route IDs

* The [Mobility Database](https://mobilitydatabase.org) and
  [Transitland](https://www.transit.land) list GTFS-realtime feeds for most
  agencies. Look for the **trip updates** and **vehicle positions** URLs, and
  register with the agency if it requires an API key.
* `stop_id` and `route_id` come from the agency's static GTFS schedule
  (`stops.txt` and `routes.txt`). They often differ from what's printed on signs:
  Seattle's route 48 is `100228`, for example.
* Many agencies use a different stop ID for each direction (NYC subway stop
  `F16S` is the southbound platform of `F16`).
* For feeds set up in the UI, **Download diagnostics** on the integration shows
  which route IDs currently appear at each of your stops. Credentials are
  redacted.

## Troubleshooting

* **"The URL did not return a GTFS-realtime feed"** explains what came back
  instead. A ZIP file is the static schedule, not the realtime feed. An HTML
  page usually means a wrong URL or a missing API key. Text means the URL asks
  for a debug format (remove options like `?debug`).
* **The sensor is `unknown`**: nothing is currently predicted for that stop and
  route. Check the IDs (see above), and remember many feeds only include trips
  that are already running.
* **Feeds that only publish delays** (no times) aren't supported yet; they need
  the static schedule to work out times.
* To get debug logs:
  ```yaml
  logger:
    logs:
      custom_components.gtfs_rt: debug
  ```
  When opening an issue, include the log, your Home Assistant version and, for
  UI setups, the diagnostics file.

## Upgrading from 0.1.0

(If you installed before releases existed, Home Assistant shows this version
as 1.0.1.)

Existing YAML keeps working, with the same entity IDs and attribute names. See
the [changelog](CHANGELOG.md) for the few behavior changes (mostly bug fixes).
To move a feed to the UI, add it there and remove its YAML block. The UI
creates new entities, named after each departure.

## Development

```bash
pip install -r requirements_test.txt ruff mypy
pytest --cov
ruff check . && ruff format --check .
mypy
```

CI runs these against the oldest supported and the latest Home Assistant, plus
`hassfest` and HACS validation. See [the design notes](docs/modernization-plan.md)
for how the integration is put together.
