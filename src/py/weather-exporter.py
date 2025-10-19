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
import os
import sys

import metrics_utility

# cache metadata for metrics.  it's an array of tuples, each tuple being [string,dictionary] representing metric name and labels (no value)
metric_metadata_cache = {}

# active_site_names: array of site names for which threads should be active (checked by each instance of watch_weather_source)
active_site_names = []

DYNAMIC_SITE_PREFIX = "dynamic_site."

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
        metrics_utility.set(metric_name, metric_value, metric_labels)

def merge_labels(l1, l2):
    # dict.update is doing something weird so created this to ensure a deep copy
    output = copy.deepcopy(l1)
    output.update(l2)
    return output

def watch_weather_source(source, host, port, parameters, lat, long, site_name, refresh_frequency_seconds):
    global metric_metadata_cache

    params=f"source={source}"
    if parameters:
        for k, v in parameters.items():
            params+=f"&{k}={v}"

    # create labels common to all metrics
    base_labels={
        "latitude": lat,
        "longitude": long,
        "source": source,
        "site": site_name,
    }

    base_url = f"http://{host}:{port}"

    # register self as an active thread, someone else can remove it later if needed
    thread_name = f"{site_name}.{source_name}"
    active_site_names.append(thread_name)

    while not STOP_THREADS:
        try:
            # check if still in active list
            if thread_name not in active_site_names:
                # no longer active, exit thread
                debug(f"Thread for site '{thread_name}' no longer active.  Exiting thread.")
                break

            debug(f"watch_weather_source request({params})")
            url=f"{base_url}/forecast/{lat}/{long}?{params}"
            response = requests.get(url)
            debug(f"watch_weather_source response({params}) " + str(response.status_code))

            if response.status_code != 200 or response.text is None or response.text == '':
                debug(response.text)
                metrics_utility.inc("weather_error_total", {})
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

                        datum=forecast["data"][key]

                        dt=datum["dt"]#datetime.fromisoformat(key.replace("Z", "+00:00")).timestamp()
                        if not found_now and dt <= now and (now - dt) < 3600:
                            # this is in the past, isn't too old.  use it as "now"
                            print(f"key={key}, dt={dt}, now={now}, diff={(now - dt)/3600}, source={source}")
                            when="now"
                            found_now=True
                        elif found_now:
                            # we have found "now" and can increment.
                            when=f"+{i}h"
                            i+=1

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

                metrics_utility.inc("weather_success_total", base_labels)
        except Exception as e:
            # well something went bad
            metrics_utility.inc("weather_error_total", base_labels)
            print(repr(e))
            traceback.print_exc()
            pass

        # sleep for the configured time, allowing for interrupt
        for x in range(refresh_frequency_seconds):
            if thread_name not in active_site_names:
                # thread isn't active anymore
                # first wipe metrics for this source
                # then drop out of this loop and allow main loop to handle exit

                for mmc in metric_metadata_cache[source]:
                    key=mmc[0]
                    labels=mmc[1]
                    debug("removing metric.  key={}, labels={}".format(key,labels))
                    # wipe the metric
                    metric_set("weather_{}".format(key),None,labels)

                break
            if not STOP_THREADS:
                time.sleep(1)


# update_metrics creates / updates metrics for forecast data and returns an array of [key,labels] processed
def update_metrics(forecast, base_labels):
    output = []

    # process all the keys
    for key in forecast:
        # for simplicity, extract the value for key
        if not isinstance(forecast[key], dict):
            # probably is "dt" which is not an object
            continue
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
        config = yaml.safe_load(f)

    # Start up the server to expose the metrics.
    metrics_utility.metrics(config["metrics"]["port"])
    
    # start threads to watch each site + source
    threads = []
    for site in config['sites']:
        if 'sources' not in site:
            continue
        for source in site['sources']:
            watch_source = locals()["watch_weather_source"]
            # def watch_weather_source(source, host, port, parameters, lat, long, site_name, refresh_frequency_seconds):
            source_name = source['name']
            host = config['service']['host']
            port = config['service']['port']
            parameters = config['sources'][source_name]['parameters']
            lat = site['latitude']
            long = site['longitude']
            site_name = site['name']
            refresh_frequency_seconds = source['refresh_frequency_seconds']
            t = Thread(target=watch_source, args=(source_name, host, port, parameters, lat, long, site_name, refresh_frequency_seconds))
            t.start()
            threads.append(t)

    # wait for all threads then exit
    # watch for any dynamic sites as detected from telescope metrics
    try:
        while len(threads) > 0:
            # still have work to do, yay

            # check for (and remove) any dead threads
            for t in threads:
                if not t.is_alive():
                    threads.remove(t)

            # see if there's any dynamic lat/long to export
            if 'prometheus' in config:
                url = f"https://{config['prometheus']['host']}:{config['prometheus']['port']}/api/v1/query?query={config['prometheus']['query']}"
                username = config['prometheus']['username']
                password = config['prometheus']['password']
                response = requests.get(url, auth=(username, password))
                if response.status_code != 200 or response.text is None or response.text == '':
                    debug(response.text)
                    # going to just ignore failures for now...
                else:
                    data = json.loads(response.text)
                    if 'status' in data and data['status'] == "success":
                        # assume all dynamic sites need to be removed unless we see them active
                        inactive_site_names = []
                        for site in active_site_names:
                            if site.startswith(DYNAMIC_SITE_PREFIX):
                                inactive_site_names.append(site)

                        debug(f"initial value: inactive_site_names = {inactive_site_names}")

                        for result in data['data']['result']:
                            try:
                                # round dynamic sites in case some might be close together (i.e. multiple mounts in one location)
                                location_round = config['dynamic_sites']['location_round']
                                lat = round(float(result['metric']['latitude']),location_round)
                                long = round(float(result['metric']['longitude']),location_round)
                                site_name = f"{DYNAMIC_SITE_PREFIX}{result['metric']['host']}"
                                watch_source = locals()["watch_weather_source"]
                                # def watch_weather_source(source, host, port, parameters, lat, long, site_name, refresh_frequency_seconds):
                                host = config['service']['host']
                                port = config['service']['port']
                                for source in config['dynamic_sites']['sources']:
                                    source_name = source['name']
                                    refresh_frequency_seconds = source['refresh_frequency_seconds']
                                    parameters = config['sources'][source_name]['parameters']

                                    dynamic_sites_cache_name = f"{site_name}.{source_name}"

                                    # make sure we don't delete this thread, it's active
                                    if dynamic_sites_cache_name in inactive_site_names:
                                        inactive_site_names.remove(dynamic_sites_cache_name)

                                    # only create this if it's not already tracked
                                    if dynamic_sites_cache_name not in active_site_names:
                                        debug(f"creating dynamic site '{dynamic_sites_cache_name}'")
                                        # NOTE thread will register self as active, no need to manually do that here
                                        t = Thread(target=watch_source, args=(source_name, host, port, parameters, lat, long, site_name, refresh_frequency_seconds))
                                        t.start()
                                        threads.append(t)
                            except Exception as e:
                                # something went wrong. just continue.
                                print("EXCEPTION")
                                print(e)
                                pass
            
                        # remove any dynamic sites that are no longer active
                        debug(f"active_site_names = {active_site_names}")
                        debug(f"inactive_site_names = {inactive_site_names}")
                        for i in inactive_site_names:
                            active_site_names.remove(i)
                        # NOTE each individual thread handles cleanup
            
            # sleep a while so it's not a busy wait
            time.sleep(15)
    except KeyboardInterrupt:
        # time to abort, set var to have all threads exit
        debug("KeyboardInterrupt! Stopping threads...")
        STOP_THREADS=True

# cd GitHub\weather-exporter
# python src\py\weather-exporter.py --config config.yaml