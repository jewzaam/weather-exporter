# weather-exporter

Export metrics based on configured weather sources.  Supported sources:
* openweathermap
* weather.gov

# Setup

## Dependencies

Install:
* python
* pip
* git

## Clone Source

```shell
git clone https://github.com/jewzaam/weather-exporter
```

## Python requirements

```shell
pip install -r requirements.txt --user
```

## config.yaml

You can name the file whatever you want, but this document assumes **config.yaml**.

An example configuration is included here with some fields redacted.  Please supply your values for:
* OPENWEATHERMAP_API_KEY
* WEATHERGOV_AGENT
* LATITUDE
* LONGITUDE

**OPTIONAL** For dynamic forecasts create:
* `prometheus` - how to query for dynamic locations (based on telescope lat/long)
* `dynamic_sites` - for what weather sources to use and other configurations

```yaml
metrics:
  port: 8011
service:
  host: "127.0.0.1"
  port: 9213
prometheus:
  username: ${PROMETHEUS_USERNAME}
  password: ${PROMETHEUS_PASSWORD}
  host: ${PROMETHEUS_HOST}
  port: ${PROMETHEUS_PORT:9090}
  query: alpaca_telescope_sitelatitude
dynamic_sites:
  location_round: 2 # round location precision to reduce number of dynamic sites
  sources:
  - name: weathergov
    refresh_frequency_seconds: 300
  - name: openweathermap
    refresh_frequency_seconds: 300
degrees_to_astronomical_sunrise: 20
degrees_to_astronomical_sunset: 20
sources:
  openweathermap:
    parameters: # passed to the weather api as query params
      apikey: ${OPENWEATHERMAP_API_KEY}
  weathergov:
    parameters: # passed to the weather api as query params
      agent: ${WEATHERGOV_AGENT}
sites:
  - name: Main Site
    latitude: ${LATITUDE}
    longitude: ${LONGITUDE}
    sources:
      - name: openweathermap
        refresh_frequency_seconds: 300 # openweathermap free api has a limit of 1000 calls a day, this will consume 288
      - name: weathergov
        refresh_frequency_seconds: 300 # just match openweathermap
```

### Config Details

DOCUMENTATION OUT OF DATE!!  Sorry..


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
  * example: `precip_probability: 100` converts from a 0.0 to 1.0 scale to 0.0 to 100.0 scale

### API Key: openweathermap

1. Login at https://home.openweathermap.org/ 
1. Go to "API keys" (tab)
1. Create or use an existing key

This is your API key.

### Agent: weathergov

They want an email address or website I think so they can contact you if needed.

# Installation

## Linux Service

Install the service by setting up some env vars then copying the systemd template with those vars, start the service, and enable the service.

```shell
export REPO_BASE_DIR=~/weather-exporter
export PORT=8011
export CONFIG=$REPO_BASE_DIR/config.yaml
export PYTHON=$(which python)

cat $REPO_BASE_DIR/src/systemd/weather-exporter.service | envsubst > /tmp/weather-exporter.service
sudo mv /tmp/weather-exporter.service /etc/systemd/system/weather-exporter.service

unset REPO_BASE_DIR
unset PORT
unset CONFIG
unset PYTHON

sudo systemctl daemon-reload
sudo systemctl start weather-exporter.service
sudo systemctl enable weather-exporter.service
```

# Verify

## Metrics 
Check the metrics are exported on the port you specified.
