import argparse
from types import LambdaType
import time
import requests
import json
import yaml
import re
from threading import Thread
import traceback

import httpimport

with httpimport.github_repo('jewzaam', 'metrics-utility', 'utility', 'main'):
    import utility

# I like dark sky but the API is going away at end of 2022. 
# Until then I continue to use it!

# cache metadata for metrics.  it's an array of tuples, each tuple being [string,dictionary] representing metric name and labels (no value)
metric_metadata_cache = {}

DEBUG=True

def debug(message):
    if DEBUG:
        print("DEBUG: {}".format(message))

def watch_openweathermap(source, config):
    global metric_metadata_cache

    lat = config['location']['latitude']
    long = config['location']['longitude']
    api_key = config['openweathermap']['api_key']

    # create labels common to all metrics
    global_labels={
        "latitude": lat,
        "longitude": long,
        "source": source,
    }

    while True:
        try:
            debug("openweathermap request")
            response = requests.get("http://api.openweathermap.org/data/2.5/onecall?appid={}&lat={}&lon={}&exclude=minutely,daily&units=metric".format(api_key,lat,long))
            debug("openweathermap response " + str(response.status_code))

            if response.status_code != 200 or response.text is None or response.text == '':
                debug(response.text)
                utility.inc("weather_error_total")
            else:
                data = json.loads(response.text)

                metric_metadata = []

                if 'current' in data:
                    l={"when": "now"}
                    l.update(global_labels)
                    weather = normalize_weather(data['current'], config[source]['key_mappings'], config[source]['key_multipliers'])
                    metric_metadata += update_metrics(weather, l)
                if 'hourly' in data and  len(data['hourly']) > 0:
                    for i in range(0,12):
                        l={"when": "+{}h".format(i+1)}
                        l.update(global_labels)
                        weather = normalize_weather(data['hourly'][i], config[source]['key_mappings'], config[source]['key_multipliers'])
                        metric_metadata += update_metrics(weather, l)

                # for any cached labels that were not processed, remove the metric
                if source in metric_metadata_cache:
                    for mmc in metric_metadata_cache[source]:
                        # if the cache has a value we didn't just collect we must remove the metric
                        if mmc not in metric_metadata:
                            key=mmc[0]
                            labels=mmc[1]
                            debug("removing metric.  key={}, labels={}".format(key,labels))
                            # wipe the metric
                            utility.set("weather_{}".format(key),None,labels)

                # reset cache with what we just collected
                metric_metadata_cache[source] = metric_metadata

                utility.inc("weather_success_total", global_labels)
        except Exception as e:
            # well something went bad
            utility.inc("weather_error_total", global_labels)
            print(repr(e))
            traceback.print_exc()
            pass

        # sleep for the configured time
        time.sleep(config[source]['refresh_delay_seconds'])


def watch_darksky(source, config):
    global metric_metadata_cache

    lat = config['location']['latitude']
    long = config['location']['longitude']
    api_key = config[source]['api_key']

    # create labels common to all metrics
    global_labels={
        "latitude": lat,
        "longitude": long,
        "source": source,
    }

    while True:
        try:
            debug("darksky request")
            response = requests.get("https://api.darksky.net/forecast/{}/{},{}?language=en&units=si&exclude=minutely,daily,alerts,flags&extend=hourly".format(api_key,lat,long))
            debug("darksky response " + str(response.status_code))

            if response.status_code != 200 or response.text is None or response.text == '':
                debug(response.text)
                utility.inc("weather_error_total", global_labels)
            else:
                data = json.loads(response.text)

                metric_metadata = []

                now=time.time()
                currently_found=False
                if 'currently' in data:
                    currently_found=True
                    when="now"
                    l={"when": when}
                    l.update(global_labels)
                    weather = normalize_weather(data['currently'], config[source]['key_mappings'], config[source]['key_multipliers'])
                    metric_metadata += update_metrics(weather, l)

                if 'hourly' in data and 'data' in data['hourly'] and len(data['hourly']['data']) > 0:
                    start_index=0
                    i=0
                    max_hours=12
                    while i <= start_index + max_hours:
                        h=data['hourly']['data'][i]
                        when=""
                        if not currently_found and h['time'] < now and (now - h['time'])/3600 > 0:
                            # don't have a "now", this is in the past, isn't too old.  use it as "now"
                            start_index=i
                            when="now"
                        elif currently_found and h['time'] < now:
                            # already have a "now" and this is in the past.  move along!
                            start_index=i
                            i += 1
                            continue
                        else:
                            # everything else, future
                            when="+{}h".format(i-start_index)
                        l={"when": when}
                        l.update(global_labels)
                        # normalize the data
                        weather = normalize_weather(h, config[source]['key_mappings'], config[source]['key_multipliers'])
                        metric_metadata += update_metrics(weather, l)
                        # increment counter!
                        i += 1

                # for any cached labels that were not processed, remove the metric
                if source in metric_metadata_cache:
                    for mmc in metric_metadata_cache[source]:
                        # if the cache has a value we didn't just collect we must remove the metric
                        if mmc not in metric_metadata:
                            key=mmc[0]
                            labels=mmc[1]
                            debug("removing metric.  key={}, labels={}".format(key,labels))
                            # wipe the metric
                            utility.set("weather_{}".format(key),None,labels)

                # reset cache with what we just collected
                metric_metadata_cache[source] = metric_metadata

                utility.inc("weather_success_total", global_labels)
        except Exception as e:
            # well something went bad
            utility.inc("weather_error_total", global_labels)
            print(repr(e))
            traceback.print_exc()
            pass

        # sleep for the configured time
        time.sleep(config[source]['refresh_delay_seconds'])

def normalize_weather(raw, key_mappings, key_multipliers):
    # start with the raw input
    weather = {}

    for raw_key in raw:
        raw_value = raw[raw_key]

        # only process a key we have a mapping for
        if raw_key in key_mappings:
            normalized_key = key_mappings[raw_key]
            normalized_value = raw_value

            if isinstance(raw_value, list):
                # just pick the first element
                normalized_value = raw_value[0]
            if isinstance(raw_value, dict):
                # just pick the first key
                normalized_value = raw_value[next(iter(raw_value))]

            if normalized_key in key_multipliers:
                normalized_value *= key_multipliers[normalized_key]
            
            weather[normalized_key] = normalized_value

    return weather

# update_metrics creates / updates metrics for normalized weather data and returns an array of [key,labels] processed
def update_metrics(weather, global_labels):
    output = []

    # process all the keys
    for key in weather:
        # for simplicity, extract the value for key
        value=weather[key]

        # wind is split across multiple keys, use simple regex to extract it
        m = re.match('^wind_(.*)', key)

        # pre allocate variable l so it exists outside the loop.  it will be added to label cache
        l = {}

        if key=='temperature':
            l={"type": "current", "unit": "celsius"}
            l.update(global_labels)
            try:
                utility.set("weather_{}".format(key),value,l)
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='feels_like':
            l={"type": "feels_like", "unit": "celsius"}
            key="temperature"
            l.update(global_labels)
            try:
                utility.set("weather_{}".format(key),value,l)
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='pressure':
            l={"unit": "millibars"}
            l.update(global_labels)
            try:
                utility.set("weather_{}".format(key),value,l)
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='humidity':
            l={"unit": "percent"}
            l.update(global_labels)
            try:
                utility.set("weather_{}".format(key),value,l)
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='dew_point':
            l={"unit": "celsius"}
            l.update(global_labels)
            try:
                utility.set("weather_{}".format(key),value,l)
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='visibility':
            l={"unit": "meters"}
            l.update(global_labels)
            try:
                utility.set("weather_{}".format(key),value,l)
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='clouds':
            l={"unit": "percent"}
            l.update(global_labels)
            try:
                utility.set("weather_{}".format(key),value,l)
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='time':
            l={"type": "current", "unit": "second"}
            l.update(global_labels)
            try:
                utility.set("weather_{}".format(key),int(value),l)
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='precip_probability':
            l={"unit": "percent"}
            l.update(global_labels)
            try:
                utility.set("weather_{}".format(key),value,l)
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='precip_intensity':
            l={"unit": "mm"}
            l.update(global_labels)
            try:
                utility.set("weather_{}".format(key),value,l)
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif m:
            key="wind"
            t=m.groups()[0].lower()
            unit="kph"
            if t=='direction':
                unit="degree"
            l={"type": t, "unit": unit}
            l.update(global_labels)
            try:
                utility.set("weather_{}".format(key),value,l)
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        else:
            # not a key we care about, continue so appending to label cache and output are skipped
            continue

        # add the metric metadata to the output
        output.append([key,l])

    return output

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Export logs as prometheus metrics.")
    parser.add_argument("--port", type=int, help="port to expose metrics on")
    parser.add_argument("--config", type=str, help="configuraiton file")
    
    args = parser.parse_args()
    
    # Start up the server to expose the metrics.
    utility.metrics(args.port)

    config = {}
    with open(args.config, 'r') as f:
        config = yaml.load(f)
    
    # start threads to watch each source
    threads = []
    for source in config['sources']:
        watch_source = locals()["watch_{}".format(source)]
        t = Thread(target=watch_source, args=(source, config), daemon=True)
        t.start()
        threads.append(t)

    # wait for all threads then exit
    while len(threads) > 0:
        for t in threads:
            if not t.is_alive():
                threads.remove(t)
        time.sleep(5)

# python exporter-weather.py --port 8001 --config exporter-weather.yaml