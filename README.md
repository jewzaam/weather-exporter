# weather-exporter

Export metrics based on configured weather sources.  Supported sources:
* darksky
* openweathermap

## Setup

An example configuration is included here with some fields redacted.  Please supply your values for:
* LATITUDE
* LONGITUDE
* DARKSKY_API_KEY
* OPENWEATHERMAP_API_KEY

```yaml
location:
  latitude: ${LATITUDE}
  longitude: ${LONGITUDE}
degrees_to_astronomical_sunrise: 20
degrees_to_astronomical_sunset: 20
sources:
  - darksky
  - openweathermap
darksky:
  api_key: ${DARKSKY_API_KEY}
  refresh_delay_seconds: 100 # 1000 calls / day cap
  key_mappings:
    time: time
    precipIntensity: precip_intensity
    precipProbability: precip_probability
    temperature: temperature
    apparentTemperature: feels_like
    dewPoint: dew_point
    humidity: humidity
    pressure: pressure
    windSpeed: wind_speed
    windGust: wind_gust
    windBearing: wind_direction
    cloudCover: clouds
    visibility: visibility
  key_multipliers:
    humidity: 100
    clouds: 100
    precip_probability: 100
    visibility: 1000
openweathermap:
  api_key: ${OPENWEATHERMAP_API_KEY}
  refresh_delay_seconds: 300 # had a limit on hourly calls, need to dig up the reference
  key_mappings:
    dt: time
    rain: precip_intensity
    snow: precip_intensity
    pop: precip_probability
    temp: temperature
    feels_like: feels_like
    dew_point: dew_point
    humidity: humidity
    pressure: pressure
    wind_speed: wind_speed
    wind_gust: wind_gust
    wind_deg: wind_direction
    clouds: clouds
    visibility: visibility
    sunrise: sunrise
    sunset: sunset
  key_multipliers:
    precip_probability: 100
```

### Configuration

* `location` - where you are in the world
  * `latitude` - your latitude
  * `longitude` - your longitude
* `degrees_to_astronomical_sunrise` - simplifies calculation for astronomical twilight by adjusting sunrise in degrees
* `degrees_to_astronomical_sunset` - same simplificaiton for sunset
* `sources` - array of sources, must have at least one value.  Valid values are **darksky** and **openweathermap**
* `darksky` - the config for darksky.  See "source configuration".
* `openweathermap` - the config for openweathermap.  See "source configuration".

Source Configuration is generic, but the example above is tailored for the supported sources.  You do not need to modify it!  But, if you want to add a new source it's helpful to know how this works.
* `api_key` - the API key for the source.  Hopefully any new sources follow this model...
* `refresh_delay_seconds` - each of these APIs have usage limits, so tune the time between requests to not run out of API requests.
* `key_mappings` - key value pairs where **key** is the value provided by the source and **value** is the key used in this exporter.
  * example: `dewPoint: dew_point`
  * receive **dewPoint** from source and rename to **dew_point** for this exporter.
* `key_multipliers` - used to convert a value so there is consistency across sources.  Uses the exporter key, not the source's key!
  * example: `precip_probability: 100`
  * converts from a 0.0 to 1.0 scale to 0.0 to 100.0 scale

### API Key: darksky

Unfortunetly darksky isn't long for this world, it will be discontinued at the end of 2022.  If you don't have an account already you're out of luck.

https://blog.darksky.net/

If you do have an account, login at https://darksky.net/dev/account and snag your "secret key".  This is your API key.

### API Key: openweathermap

1. Login at https://home.openweathermap.org/ 
1. Go to "API keys" (tab)
1. Create or use an existing key

This is your API key.

## Usage

Once you have your configuration file run the exporter.  This assumes the configuration file is `config.yaml` but you can name it anything.

```shell
python weather-exporter.py --port 8001 --config config.yaml
```

## Verify
