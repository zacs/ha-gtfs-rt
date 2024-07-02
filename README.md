# Home Assistant Realtime GTFS

This project contains a new sensor that provides real-time departure data for
local transit systems that provide gtfs feeds.

## Installation (HACS) - Recommended
0. Have [HACS](https://custom-components.github.io/hacs/installation/manual/) installed, this will allow you to easily update
1. Add `https://github.com/zacs/ha-gtfs-rt` as a [custom repository](https://custom-components.github.io/hacs/usage/settings/#add-custom-repositories) as Type: Integration
2. Click install under "GTFS-Realtime", restart your instance.

## Installation (Manual)
1. Download this repository as a ZIP (green button, top right) and unzip the archive
2. Copy `/custom_components/gtfs_rt` to your `<config_dir>/custom_components/` directory
   * You will need to create the `custom_components` folder if it does not exist
   * On Hassio the final location will be `/config/custom_components/gtfs_rt`
   * On Hassbian the final location will be `/home/homeassistant/.homeassistant/custom_components/gtfs_rt`

## Configuration

Add the following to your `configuration.yaml` file:

```yaml
# Example entry for Austin TX

sensor:
  - platform: gtfs_rt
    trip_update_url: 'https://data.texas.gov/download/rmk2-acnw/application%2foctet-stream'
    vehicle_position_url: 'https://data.texas.gov/download/eiei-9rpf/application%2Foctet-stream'
    departures:
    - name: Downtown to airport
      route: 100
      stopid: 514
```

```yaml
# Example entry for Seattle WA

- platform: gtfs_rt
  trip_update_url: 'http://api.pugetsound.onebusaway.org/api/gtfs_realtime/trip-updates-for-agency/1.pb?key=TEST'
  departures:
  - name: "48 to Uni"
    route: 100228
    stopid: 36800
```

```yaml
# Example entry for Montreal

- platform: gtfs_rt
  trip_update_url: 'https://api.stm.info/pub/od/gtfs-rt/ic/v2/tripUpdates'
  vehicle_position_url: 'https://api.stm.info/pub/od/gtfs-rt/ic/v2/vehiclePositions'
  apikey: <api key>
  departures:
  - name: "Bus 178"
    route: 168
    stopid: 56698 
```

```yaml
# Example entry for NYC

- platform: gtfs_rt
    trip_update_url: 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-bdfm'
    x_api_key: <api key>
    departures:
      - name: "Brooklyn F"
        route: 'F'
        stopid: 'F16S'
```

Configuration variables:

- **trip_update_url** (*Required*): Provides bus route etas. See the **Finding Feeds** section at the bottom of the page for more details on how to find these
- **vehicle_position_url** (*Optional*): Provides live bus position tracking on the home assistant map
- **headers**(*Optional*): Expects a dictionary. If provided, the dictionary will be sent as headers. (e.g. {"Authorization": "mykey"})
- **departures** (*Required*): A list of routes and departure locations to watch
- **route** (*Optional*): The name of the gtfs route
- **stopid** (*Optional*): The stopid for the location you want etas for
- **max_departures** (*Optional*): The maximum number of departures to retrieve. Defaults to 2. Specify 0 for all departures.

## Screenshot

![screenshot](https://i.imgur.com/VMcX9aG.png)

## Finding Feeds

[Transit Feeds](https://transitfeeds.com) is a fairly good source for realtime
gtfs feeds. Search for your city, and then look for a feed that is tagged with
'GTFS-RealTime'. There should be an 'official url' in the side bar that you can
use. Routes and stops can be found by clicking on the regular gtfs feed, and
finding the id for the stop you are interested in. Please feel free to message
me or open an issue if you find other good sources.

## Reporting an Issue

1. Setup your logger to print debug messages for this component using:
```yaml
logger:
  default: info
  logs:
    custom_components.gtfs_rt: debug
```
2. Restart HA
3. Verify you're still having the issue
4. File an issue in this Github Repository containing your HA log (Developer section > Info > Load Full Home Assistant Log)
   * You can paste your log file at pastebin https://pastebin.com/ and submit a link.
   * Please include details about your setup (Pi, NUC, etc, docker?, HASSOS?)
   * The log file can also be found at `/<config_dir>/home-assistant.log`