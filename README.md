# Home Assistant Realtime GTFS

This project contains a new sensor that provides real-time departure data for
local transit systems that provide gtfs feeds.

## Installation

- Copy file `sensor/gtfs_realtime.py` to your `ha_config_dir/custom-components/sensor` directory.
- Configure with config below.
- Restart Home-Assistant.

## Usage
To use this component in your installation, add the following to your `configuration.yaml` file:

```yaml
# Example configuration.yaml entry

sensor:
  - platform: gtfs_realtime
    trip_update_url: 'https://data.texas.gov/download/rmk2-acnw/application%2foctet-stream'
    vehicle_position_url: 'https://data.texas.gov/download/eiei-9rpf/application%2Foctet-stream'
    departures:
    - name: Downtown to airport
      route: 100
      stopid: 514
```

Configuration variables:

- **trip_update_url** (*Required*): Provides bus route etas. See the `Finding Feeds` section at the bottom of the page for more details on how to find these
- **vehicle_position_url** (*Optional*): Provides live bus position tracking on the home assistant map
- **api_key** (*Optional*): If provided, this key will be sent with API
requests in an "Authorization" header.
- **departures** (*Required*): A list of routes and departure locations to watch
- **route** (*Optional*): The name of the gtfs route
- **stopid** (*Optional*): The stopid for the location you want etas for

## Screenshot

![screenshot](https://i.imgur.com/VMcX9aG.png)

## Finding Feeds

[Transit Feeds](https://transitfeeds.com) is a fairly good source for realtime
gtfs feeds. Search for your city, and then look for a feed that is tagged with
'GTFS-RealTime'. There should be an 'official url' in the side bar that you can
use. Routes and stops can be found by clicking on the regular gtfs feed, and
finding the id for the stop you are interested in. Please feel free to message
me or open an issue if you find other good sources.
