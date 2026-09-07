import argparse
import time
import requests
import json
import yaml
import re
from threading import Thread
import traceback
import copy
import os

import metrics_utility

# cache metadata for metrics.  it's an array of tuples, each tuple being
# [string,dictionary] representing metric name and labels (no value)
metric_metadata_cache = {}

# active_site_names: array of site names for which threads should be active
# (checked by each instance of watch_weather_source)
active_site_names = []

DYNAMIC_SITE_PREFIX = "dynamic_site."

# Forecast key from weather-service -> (metric name suffix, labels beyond "unit").
# Two source keys can share one metric name as long as a label tells them apart,
# which is what "type" does for temperature and for the wind family below.
# Anything absent from here and not matching WIND_KEY_PATTERN is not exported.
METRIC_KEYS = {
    "temperature": ("temperature", {"type": "current"}),
    "apparentTemperature": ("temperature", {"type": "feels_like"}),
    "dewpoint": ("dew_point", {}),
    "relativeHumidity": ("humidity", {}),
    "skyCover": ("clouds", {}),
    "probabilityOfPrecipitation": ("precip_probability", {}),
    "quantitativePrecipitation": ("precip_intensity", {}),
    "pressure": ("pressure", {}),
    "visibility": ("visibility", {}),
}

# wind is split across windDirection / windSpeed / windGust; the suffix becomes
# the "type" label so they collapse into one weather_wind metric.
WIND_KEY_PATTERN = re.compile("^wind(.*)")

DEBUG = True

STOP_THREADS = False


def debug(message):
    if DEBUG:
        print("DEBUG: {}".format(message))


# wrapper to handle a few edge cases
def metric_set(metric_name, metric_value, metric_labels):
    # The metric util will try to delete a metric if the value is None.
    # But we do not want to do this as we are creating metrics across multiple
    # sources.  So, don't update in that case..
    if metric_value is not None:
        metrics_utility.set(metric_name, metric_value, metric_labels)


def merge_labels(l1, l2):
    # dict.update is doing something weird so created this to ensure a deep copy
    output = copy.deepcopy(l1)
    output.update(l2)
    return output


def watch_weather_source(
    source, host, port, parameters, lat, long, site_name, refresh_frequency_seconds
):
    # weather-service gates every /forecast call on X-Api-Key and fails closed.
    headers = {"X-Api-Key": os.environ.get("WEATHER_API_KEY", "")}

    params = f"source={source}"
    if parameters:
        for k, v in parameters.items():
            if k == "apikey":
                # the upstream key travels in a header so it never lands in the
                # service's access log, and never in a URL we print at DEBUG.
                headers["X-OpenWeatherMap-Key"] = v
            else:
                params += f"&{k}={v}"

    # create labels common to all metrics
    base_labels = {
        "latitude": lat,
        "longitude": long,
        "source": source,
        "site": site_name,
    }

    base_url = f"http://{host}:{port}"

    # register self as an active thread, someone else can remove it later if needed
    thread_name = f"{site_name}.{source}"
    active_site_names.append(thread_name)

    while not STOP_THREADS:
        try:
            # check if still in active list
            if thread_name not in active_site_names:
                # no longer active, exit thread
                debug(
                    f"Thread for site '{thread_name}' no longer active. "
                    "Exiting thread."
                )
                break

            debug(f"watch_weather_source request({params})")
            url = f"{base_url}/forecast/{lat}/{long}?{params}"
            # timeout: without it a wedged upstream parks this thread forever and
            # the site's metrics silently freeze at their last value.
            response = requests.get(url, headers=headers, timeout=30)
            debug(
                f"watch_weather_source response({params}) " + str(response.status_code)
            )

            if (
                response.status_code != 200
                or response.text is None
                or response.text == ""
            ):
                debug(response.text)
                # base_labels, not {}: prometheus_client fixes a counter's label
                # set at first registration. Whichever of these two call sites
                # fired first used to define it, and the other then raised
                # "Incorrect label count" — inside the except handler below,
                # where nothing catches it, killing the thread for good.
                metrics_utility.inc("weather_error_total", base_labels)
            else:
                forecast = json.loads(response.text)

                metric_metadata = []

                now = time.time()
                found_now = False

                if "data" in forecast:
                    i = 1
                    max_hours = 12
                    when = "now"
                    for key in forecast["data"]:
                        if i > max_hours:
                            # got enough data, done
                            break

                        datum = forecast["data"][key]

                        dt = datum["dt"]
                        if not found_now and dt <= now and (now - dt) < 3600:
                            # this is in the past, isn't too old.  use it as "now"
                            print(
                                f"key={key}, dt={dt}, now={now}, "
                                f"diff={(now - dt)/3600}, source={source}"
                            )
                            when = "now"
                            found_now = True
                        elif found_now:
                            # we have found "now" and can increment.
                            when = f"+{i}h"
                            i += 1

                        # update metrics
                        metric_metadata += update_metrics(
                            datum, merge_labels(base_labels, {"when": when})
                        )

                        if when == "now":
                            # special case, also create +0h data.
                            metric_metadata += update_metrics(
                                datum, merge_labels(base_labels, {"when": "+0h"})
                            )

                # for any cached labels that were not processed, remove the metric.
                # keyed by thread_name, not source: two sites sharing one source
                # would otherwise overwrite each other's cache and wipe live metrics.
                if thread_name in metric_metadata_cache:
                    for mmc in metric_metadata_cache[thread_name]:
                        # if the cache has a value we didn't just collect we
                        # must remove the metric
                        if mmc not in metric_metadata:
                            key = mmc[0]
                            labels = mmc[1]
                            debug(
                                "removing metric.  key={}, labels={}".format(
                                    key, labels
                                )
                            )
                            # wipe the metric
                            metric_set("weather_{}".format(key), None, labels)

                # reset cache with what we just collected
                metric_metadata_cache[thread_name] = metric_metadata

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

                # .get(): a thread deactivated before its first successful fetch
                # has no cache entry, and a KeyError here would kill the exit path.
                for mmc in metric_metadata_cache.get(thread_name, []):
                    key = mmc[0]
                    labels = mmc[1]
                    debug("removing metric.  key={}, labels={}".format(key, labels))
                    # wipe the metric
                    metric_set("weather_{}".format(key), None, labels)

                break
            if not STOP_THREADS:
                time.sleep(1)


# update_metrics creates / updates metrics for forecast data and returns an
# array of [key,labels] processed
def update_metrics(forecast, base_labels):
    output = []

    # process all the keys
    for forecast_key in forecast:
        # for simplicity, extract the value for forecast_key
        if not isinstance(forecast[forecast_key], dict):
            # probably is "dt" which is not an object
            continue
        value = forecast[forecast_key]["value"]

        if forecast_key in METRIC_KEYS:
            metric_key, labels = METRIC_KEYS[forecast_key]
            labels = dict(labels)  # copy, the table entry is shared across calls
        else:
            m = WIND_KEY_PATTERN.match(forecast_key)
            if not m:
                # not a key we care about, continue so appending to label
                # cache and output are skipped
                continue
            metric_key = "wind"
            labels = {"type": m.groups()[0].lower()}

        labels["unit"] = forecast[forecast_key]["uom"]

        try:
            metric_set(
                "weather_{}".format(metric_key),
                value,
                merge_labels(base_labels, labels),
            )
        except Exception as e:
            # well something went bad, print and continue.
            print(repr(e))
            traceback.print_exc()

        # add the metric metadata to the output
        output.append([metric_key, labels])

    return output


def main():
    # assigned below on KeyboardInterrupt; without this it would bind a local
    # and the watcher threads would never see the stop signal.
    global STOP_THREADS

    parser = argparse.ArgumentParser(description="Export logs as prometheus metrics.")
    parser.add_argument(
        "--config", type=str, help="configuraiton file", default="config.yaml"
    )

    args = parser.parse_args()

    config = {}
    with open(args.config, "r") as f:
        # expandvars so ${OPENWEATHERMAP_API_KEY} style placeholders resolve from
        # the environment; keeps secrets out of the config file (and out of a
        # Kubernetes ConfigMap). Unset vars stay literal and fail at the upstream.
        config = yaml.safe_load(os.path.expandvars(f.read()))

    # Start up the server to expose the metrics.
    metrics_utility.metrics(config["metrics"]["port"])

    # start threads to watch each site + source
    threads = []
    for site in config["sites"]:
        if "sources" not in site:
            continue
        for source in site["sources"]:
            source_name = source["name"]
            host = config["service"]["host"]
            port = config["service"]["port"]
            parameters = config["sources"][source_name]["parameters"]
            lat = site["latitude"]
            long = site["longitude"]
            site_name = site["name"]
            refresh_frequency_seconds = source["refresh_frequency_seconds"]
            t = Thread(
                target=watch_weather_source,
                args=(
                    source_name,
                    host,
                    port,
                    parameters,
                    lat,
                    long,
                    site_name,
                    refresh_frequency_seconds,
                ),
            )
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
            if "prometheus" in config:
                prometheus = config["prometheus"]
                url = (
                    f"https://{prometheus['host']}:{prometheus['port']}"
                    f"/api/v1/query?query={prometheus['query']}"
                )
                username = prometheus["username"]
                password = prometheus["password"]
                response = requests.get(url, auth=(username, password))
                if (
                    response.status_code != 200
                    or response.text is None
                    or response.text == ""
                ):
                    debug(response.text)
                    # going to just ignore failures for now...
                else:
                    data = json.loads(response.text)
                    if "status" in data and data["status"] == "success":
                        # assume all dynamic sites need to be removed unless
                        # we see them active
                        inactive_site_names = []
                        for site in active_site_names:
                            if site.startswith(DYNAMIC_SITE_PREFIX):
                                inactive_site_names.append(site)

                        debug(
                            "initial value: inactive_site_names = "
                            f"{inactive_site_names}"
                        )

                        for result in data["data"]["result"]:
                            try:
                                # round dynamic sites in case some might be
                                # close together (i.e. multiple mounts in one
                                # location)
                                location_round = config["dynamic_sites"][
                                    "location_round"
                                ]
                                lat = round(
                                    float(result["metric"]["latitude"]), location_round
                                )
                                long = round(
                                    float(result["metric"]["longitude"]), location_round
                                )
                                site_name = (
                                    f"{DYNAMIC_SITE_PREFIX}{result['metric']['host']}"
                                )
                                host = config["service"]["host"]
                                port = config["service"]["port"]
                                for source in config["dynamic_sites"]["sources"]:
                                    source_name = source["name"]
                                    refresh_frequency_seconds = source[
                                        "refresh_frequency_seconds"
                                    ]
                                    parameters = config["sources"][source_name][
                                        "parameters"
                                    ]

                                    dynamic_sites_cache_name = (
                                        f"{site_name}.{source_name}"
                                    )

                                    # make sure we don't delete this thread, it's active
                                    if dynamic_sites_cache_name in inactive_site_names:
                                        inactive_site_names.remove(
                                            dynamic_sites_cache_name
                                        )

                                    # only create this if it's not already tracked
                                    if (
                                        dynamic_sites_cache_name
                                        not in active_site_names
                                    ):
                                        debug(
                                            "creating dynamic site "
                                            f"'{dynamic_sites_cache_name}'"
                                        )
                                        # NOTE thread will register self as
                                        # active, no need to do that here
                                        t = Thread(
                                            target=watch_weather_source,
                                            args=(
                                                source_name,
                                                host,
                                                port,
                                                parameters,
                                                lat,
                                                long,
                                                site_name,
                                                refresh_frequency_seconds,
                                            ),
                                        )
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
        STOP_THREADS = True


# python -m weather_exporter --config config.yaml
