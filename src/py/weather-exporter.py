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
from datetime import date, timedelta, datetime

import httpimport

with httpimport.github_repo('jewzaam', 'metrics-utility', 'utility', 'main'):
    import utility

# cache metadata for metrics.  it's an array of tuples, each tuple being [string,dictionary] representing metric name and labels (no value)
metric_metadata_cache = {}

DEBUG=True

STOP_THREADS=False

def debug(message):
    if DEBUG:
        print("DEBUG: {}".format(message))

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

def watch_weather_source(source, config):
    global metric_metadata_cache

    lat = config['location']['latitude']
    long = config['location']['longitude']
    params=f"source={source}"
    if "parameters" in config[source]:
        for k, v in config[source]["parameters"].items():
            params+=f"&{k}={v}"

    # create labels common to all metrics
    base_labels={
        "latitude": lat,
        "longitude": long,
        "source": source,
    }

    host=config["service"]["host"]
    port=config["service"]["port"]
    base_url = f"http://{host}:{port}"

    while not STOP_THREADS:
        try:
            debug(f"watch_weather_source request({params})")
            url=f"{base_url}/forecast/{lat}/{long}?{params}"
            response = requests.get(url)
            debug(f"watch_weather_source response({params}) " + str(response.status_code))

            if response.status_code != 200 or response.text is None or response.text == '':
                debug(response.text)
                utility.inc("weather_error_total")
            else:
                forecast = json.loads(response.text)

                metric_metadata = []

                now=time.time()
                found_now = False

                if 'data' in forecast:
                    i=1
                    max_hours=12
                    when="now"
                    for key in forecast["data"]:
                        if i > max_hours:
                            # got enough data, done
                            break
                        dt=datetime.fromisoformat(key.replace("Z", "+00:00")).timestamp()
                        if not found_now and dt <= now and (now - dt)/3600 > 0:
                            # this is in the past, isn't too old.  use it as "now"
                            when="now"
                            found_now=True
                        elif found_now:
                            # we have found "now" and can increment.
                            when=f"+{i}h"
                            i+=1

                        datum=forecast["data"][key]

                        # update metrics
                        metric_metadata += update_metrics(datum, merge_labels(base_labels,{"when": when}))

                        if when=="now":
                            # special case, also create +0h data.
                            metric_metadata += update_metrics(datum, merge_labels(base_labels,{"when": "+0h"}))

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

                utility.inc("weather_success_total", base_labels)
        except Exception as e:
            # well something went bad
            utility.inc("weather_error_total", base_labels)
            print(repr(e))
            traceback.print_exc()
            pass

        # sleep for the configured time, allowing for interrupt
        for x in range(config[source]['refresh_delay_seconds']):
            if not STOP_THREADS:
                time.sleep(1)


# update_metrics creates / updates metrics for forecast data and returns an array of [key,labels] processed
def update_metrics(forecast, base_labels):
    output = []

    # process all the keys
    for key in forecast:
        # for simplicity, extract the value for key
        value=forecast[key]["value"]

        # wind is split across multiple keys, use simple regex to extract it
        m = re.match('^wind(.*)', key)

        # pre allocate variable l so it exists outside the loop.  it will be added to label cache
        l = {}

        if key=='temperature':
            l={"type": "current", "unit": forecast[key]["uom"]}
            try:
                metric_set("weather_{}".format(key),value,merge_labels(base_labels,l))
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='apparentTemperature':
            l={"type": "feels_like", "unit": forecast[key]["uom"]}
            key="temperature"
            try:
                metric_set("weather_{}".format(key),value,merge_labels(base_labels,l))
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='pressure':
            l={"unit": forecast[key]["uom"]}
            try:
                metric_set("weather_{}".format(key),value,merge_labels(base_labels,l))
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='relativeHumidity':
            l={"unit": forecast[key]["uom"]}
            key="humidity"
            try:
                metric_set("weather_{}".format(key),value,merge_labels(base_labels,l))
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='dewpoint':
            l={"unit": forecast[key]["uom"]}
            key="dew_point"
            try:
                metric_set("weather_{}".format(key),value,merge_labels(base_labels,l))
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='visibility':
            l={"unit": forecast[key]["uom"]}
            try:
                metric_set("weather_{}".format(key),value,merge_labels(base_labels,l))
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='skyCover':
            l={"unit": forecast[key]["uom"]}
            key="clouds"
            try:
                metric_set("weather_{}".format(key),value,merge_labels(base_labels,l))
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='probabilityOfPrecipitation':
            l={"unit": forecast[key]["uom"]}
            key='precip_probability'
            try:
                metric_set("weather_{}".format(key),value,merge_labels(base_labels,l))
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif key=='quantitativePrecipitation':
            l={"unit": forecast[key]["uom"]}
            key='precip_intensity'
            try:
                metric_set("weather_{}".format(key),value,merge_labels(base_labels,l))
            except Exception as e:
                # well something went bad, print and continue.
                print(repr(e))
                traceback.print_exc()
                pass
        elif m:
            unit=forecast[key]["uom"]
            key="wind"
            t=m.groups()[0].lower()
            if t=='Direction':
                unit="degree" # why did I pick singular?
            l={"type": t.lower(), "unit": unit}
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
    parser.add_argument("--config", type=str, help="configuraiton file", default="config.yaml")
    
    args = parser.parse_args()

    config = {}
    with open(args.config, 'r') as f:
        config = yaml.load(f)

    # Start up the server to expose the metrics.
    utility.metrics(config["metrics"]["port"])

    
    # start threads to watch each source
    threads = []
    for source in config['sources']:
        watch_source = locals()["watch_weather_source"]
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