import argparse
from types import LambdaType
import time
import requests
import json
import yaml
import re
from threading import Thread
import traceback
import copy
from datetime import datetime

import httpimport

with httpimport.github_repo('jewzaam', 'metrics-utility', 'utility', 'main'):
    import utility

# I like dark sky but the API is going away at end of 2022. 
# Until then I continue to use it!

# cache metadata for metrics.  it's an array of tuples, each tuple being [string,dictionary] representing metric name and labels (no value)
metric_metadata_cache = {}

DEBUG=True

STOP_THREADS=False

def debug(message):
    if DEBUG:
        print("DEBUG: {}".format(message))

# https://www.programiz.com/python-programming/examples/check-string-number
def isfloat(num):
    try:
        float(num)
        return True
    except ValueError:
        return False

# wrapper to handle a few edge cases
def metric_set(metric_name, metric_value, metric_labels):
    # The metric util will try to delete a metric if the value is None.
    # But we do not want to do this as we are creating metrics across multiple
    # sources.  So, don't update in that case..
    #debug("metric_name={}, metric_value={}, metric_labels={}".format(metric_name, metric_value, metric_labels))
    if metric_value is not None:
        utility.set(metric_name, metric_value, metric_labels)

def merge_labels(l1, l2):
    # dict.update is doing something weird so created this to ensure a deep copy
    output = copy.deepcopy(l1)
    output.update(l2)
    return output

def watch_openweathermap(source, config):
    global metric_metadata_cache

    lat = config['location']['latitude']
    long = config['location']['longitude']
    api_key = config['openweathermap']['api_key']

    # create labels common to all metrics
    owm_labels={
        "latitude": lat,
        "longitude": long,
        "source": source,
    }

    while not STOP_THREADS:
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
                    weather = normalize_weather(data['current'], config[source]['key_mappings'], config[source]['key_multipliers'])
                    metric_metadata += update_metrics(weather, merge_labels(owm_labels,l))
                if 'hourly' in data and  len(data['hourly']) > 0:
                    for i in range(0,12):
                        l={"when": "+{}h".format(i+1)}
                        weather = normalize_weather(data['hourly'][i], config[source]['key_mappings'], config[source]['key_multipliers'])
                        metric_metadata += update_metrics(weather, merge_labels(owm_labels,l))

                # for any cached labels that were not processed, remove the metric
                if source in metric_metadata_cache:
                    for mmc in metric_metadata_cache[source]:
                        # if the cache has a value we didn't just collect we must remove the metric
                        if mmc not in metric_metadata:
                            key=mmc[0]
                            labels=mmc[1]
                            debug("removing metric.  key={}, labels={}".format(key,labels))
                            # wipe the metric
                            metric_set("weather_{}".format(key),None,labels)

                # reset cache with what we just collected
                metric_metadata_cache[source] = metric_metadata

                utility.inc("weather_success_total", owm_labels)
        except Exception as e:
            # well something went bad
            utility.inc("weather_error_total", owm_labels)
            print(repr(e))
            traceback.print_exc()
            pass

        # sleep for the configured time, allowing for interrupt
        for x in range(config[source]['refresh_delay_seconds']):
            if not STOP_THREADS:
                time.sleep(1)


def watch_darksky(source, config):
    global metric_metadata_cache

    lat = config['location']['latitude']
    long = config['location']['longitude']
    api_key = config[source]['api_key']

    # create labels common to all metrics
    ds_labels={
        "latitude": lat,
        "longitude": long,
        "source": source,
    }

    while not STOP_THREADS:
        try:
            debug("{} request".format(source))
            response = requests.get("https://api.darksky.net/forecast/{}/{},{}?language=en&units=si&exclude=minutely,daily,alerts,flags&extend=hourly".format(api_key,lat,long))
            debug("{} response {}".format(source,str(response.status_code)))

            if response.status_code != 200 or response.text is None or response.text == '':
                debug(response.text)
                utility.inc("weather_error_total", ds_labels)
            else:
                data = json.loads(response.text)

                metric_metadata = []

                now=time.time()
                currently_found=False
                if 'currently' in data:
                    currently_found=True
                    when="now"
                    l={"when": when}
                    weather = normalize_weather(data['currently'], config[source]['key_mappings'], config[source]['key_multipliers'])
                    metric_metadata += update_metrics(weather, merge_labels(ds_labels,l))

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
                        # normalize the data
                        weather = normalize_weather(h, config[source]['key_mappings'], config[source]['key_multipliers'])
                        metric_metadata += update_metrics(weather, merge_labels(ds_labels,l))
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
                            metric_set("weather_{}".format(key),None,labels)

                # reset cache with what we just collected
                metric_metadata_cache[source] = metric_metadata

                utility.inc("weather_success_total", ds_labels)
        except Exception as e:
            # well something went bad
            utility.inc("weather_error_total", ds_labels)
            print(repr(e))
            traceback.print_exc()
            pass

        # sleep for the configured time, allowing for interrupt
        for x in range(config[source]['refresh_delay_seconds']):
            if not STOP_THREADS:
                time.sleep(1)


def watch_visualcrossing(source, config):
    global metric_metadata_cache

    lat = config['location']['latitude']
    long = config['location']['longitude']
    api_key = config[source]['api_key']

    # create labels common to all metrics
    ds_labels={
        "latitude": lat,
        "longitude": long,
        "source": source,
    }

    while not STOP_THREADS:
        try:
            debug("{} request".format(source))
            response = requests.get("https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/weatherdata/forecast?contentType=json&aggregateHours=1&forecastDays=1&includeAstronomy=true&unitGroup=metric&key={}&locations={},{}".format(api_key,lat,long))
            debug("{} response {}".format(source,str(response.status_code)))

            if response.status_code != 200 or response.text is None or response.text == '':
                debug(response.text)
                utility.inc("weather_error_total", ds_labels)
            else:
                data = json.loads(response.text)["locations"]["{},{}".format(lat,long)]

                metric_metadata = []

                now=time.time()
                currently_found=False
                if 'currentConditions' in data:
                    currently_found=True
                    when="now"
                    l={"when": when}

                    # datetime in currentConditions is inconsistent with aggregate data, sigh.
                    if 'datetime' in data['currentConditions']:
                        data['currentConditions']['datetimeStr'] = data['currentConditions']['datetime']
                        data['currentConditions']['datetime'] = now
                        # also adjust the 'raw' data for any multiplier
                        if 'time' in config[source]['key_multipliers']:
                            data['currentConditions']['datetime'] /= config[source]['key_multipliers']['time']

                    weather = normalize_weather(data['currentConditions'], config[source]['key_mappings'], config[source]['key_multipliers'])
                    metric_metadata += update_metrics(weather, merge_labels(ds_labels,l))

                if 'values' in data and len(data['values']) > 0:
                    start_index=0
                    i=0
                    max_hours=12
                    while i <= start_index + max_hours:
                        h=data['values'][i]
                        when=""
                        if not currently_found and h['datetime'] < now and (now - h['datetime'])/3600 > 0:
                            # don't have a "now", this is in the past, isn't too old.  use it as "now"
                            start_index=i
                            when="now"
                        elif currently_found and h['datetime'] < now:
                            # already have a "now" and this is in the past.  move along!
                            start_index=i
                            i += 1
                            continue
                        else:
                            # everything else, future
                            when="+{}h".format(i-start_index)
                        l={"when": when}
                        # normalize the data
                        weather = normalize_weather(h, config[source]['key_mappings'], config[source]['key_multipliers'])
                        metric_metadata += update_metrics(weather, merge_labels(ds_labels,l))
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
                            metric_set("weather_{}".format(key),None,labels)

                # reset cache with what we just collected
                metric_metadata_cache[source] = metric_metadata

                utility.inc("weather_success_total", ds_labels)
        except Exception as e:
            # well something went bad
            utility.inc("weather_error_total", ds_labels)
            print(repr(e))
            traceback.print_exc()
            pass
        except KeyboardInterrupt:
            return

        # sleep for the configured time, allowing for interrupt
        for x in range(config[source]['refresh_delay_seconds']):
            if not STOP_THREADS:
                time.sleep(1)

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
                #debug("raw_key={}, raw_value={}, normalized_key={}, normalized_value={}, multiplier={}".format(raw_key,raw_value,normalized_key,normalized_value,key_multipliers[normalized_key]))
                multiplier = key_multipliers[normalized_key]
                if not isfloat(multiplier):
                    # blindly assume it's a date conversion, shrug.  get in seconds
                    normalized_value = datetime.strptime(normalized_value, multiplier).timestamp()
                    #debug("datetime conversion: raw={}, normalized={}".format(raw_value,normalized_value))
                else:
                    normalized_value *= float(key_multipliers[normalized_key])
            
            weather[normalized_key] = normalized_value

    return weather

# update_metrics creates / updates metrics for normalized weather data and returns an array of [key,labels] processed
def update_metrics(weather, base_labels):
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
            try:
                metric_set("weather_{}".format(key),value,merge_labels(base_labels,l))
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='feels_like':
            l={"type": "feels_like", "unit": "celsius"}
            key="temperature"
            try:
                metric_set("weather_{}".format(key),value,merge_labels(base_labels,l))
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='pressure':
            l={"unit": "millibars"}
            try:
                metric_set("weather_{}".format(key),value,merge_labels(base_labels,l))
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='humidity':
            l={"unit": "percent"}
            try:
                metric_set("weather_{}".format(key),value,merge_labels(base_labels,l))
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='dew_point':
            l={"unit": "celsius"}
            try:
                metric_set("weather_{}".format(key),value,merge_labels(base_labels,l))
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='visibility':
            l={"unit": "meters"}
            try:
                metric_set("weather_{}".format(key),value,merge_labels(base_labels,l))
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='clouds':
            l={"unit": "percent"}
            try:
                metric_set("weather_{}".format(key),value,merge_labels(base_labels,l))
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='time':
            l={"type": "current", "unit": "second"}
            try:
                # don't use wrapper function 'metric_set'
                utility.set("weather_{}".format(key),int(value),merge_labels(base_labels,l))
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='sunrise' and "when" in base_labels and base_labels["when"] == "now":
            key="time"
            l={"type": "sunrise", "unit": "second"}
            try:
                # don't use wrapper function 'metric_set'
                utility.set("weather_{}".format(key),int(value),merge_labels(base_labels,l))
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='sunset' and "when" in base_labels and base_labels["when"] == "now":
            key="time"
            l={"type": "sunset", "unit": "second"}
            try:
                # don't use wrapper function 'metric_set'
                utility.set("weather_{}".format(key),int(value),merge_labels(base_labels,l))
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='precip_probability':
            l={"unit": "percent"}
            try:
                metric_set("weather_{}".format(key),value,merge_labels(base_labels,l))
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='precip_intensity':
            l={"unit": "mm"}
            try:
                metric_set("weather_{}".format(key),value,merge_labels(base_labels,l))
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
            try:
                metric_set("weather_{}".format(key),value,merge_labels(base_labels,l))
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
        t = Thread(target=watch_source, args=(source, config))
        t.start()
        threads.append(t)

    # wait for all threads then exit
    try:
        while len(threads) > 0:
            for t in threads:
                if not t.is_alive():
                    threads.remove(t)
            time.sleep(5)
    except KeyboardInterrupt:
        debug("KeyboardInterrupt! Stopping threads...")
        STOP_THREADS=True

# cd GitHub\weather-exporter
# python src\py\weather-exporter.py --port 8912 --config config.yaml