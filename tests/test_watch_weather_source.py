"""Checks how watch_weather_source calls weather-service.

Every assertion here failed against the code as it stood before 2026-09-07, and
each failure was silent in production: a 401 loop, a secret in the service's
access log, a thread parked forever, metrics filed under the wrong site. They are
cheap to re-break and expensive to notice, so they are pinned.
"""

import os
import json
import time
import importlib

import requests

from weather_exporter import exporter

FORECAST = {
    "data": {
        "2026-09-07 12:00:00+00:00": {
            "dt": time.time() - 60,  # within the last hour, so it reads as "now"
            "temperature": {"value": 21.5, "uom": "celsius"},
        }
    }
}


class _FakeResponse:
    status_code = 200
    text = json.dumps(FORECAST)


def _load():
    """Fresh module instance, so the metric cache starts empty every time."""
    module = importlib.reload(exporter)
    module.metric_set = lambda *args: None  # keep the prometheus registry out of it
    module.debug = lambda *args: None
    return module


def _one_cycle(module, site_name, source, parameters, requests_seen):
    """Run watch_weather_source for exactly one poll.

    The stub flips STOP_THREADS, which drops the sleep loop and then the outer
    while, so the call returns instead of polling forever.
    """

    def fake_get(url, **kwargs):
        requests_seen.append((url, kwargs.get("headers") or {}, kwargs.get("timeout")))
        module.STOP_THREADS = True
        return _FakeResponse()

    real_get = requests.get
    real_inc = module.metrics_utility.inc
    module.STOP_THREADS = False  # a previous cycle left it set
    requests.get = fake_get
    module.metrics_utility.inc = lambda *args: None
    try:
        module.watch_weather_source(
            source,
            "weather-service",
            9213,
            parameters,
            "40.71",
            "-74.01",
            site_name,
            1,
        )
    finally:
        requests.get = real_get
        module.metrics_utility.inc = real_inc


def test_gate_key_is_sent_and_upstream_key_stays_out_of_the_url():
    os.environ["WEATHER_API_KEY"] = "gate-secret"
    module = _load()
    seen = []
    _one_cycle(module, "home", "openweathermap", {"apikey": "owm-secret"}, seen)

    url, headers, timeout = seen[0]

    # weather-service fails closed; without this header every poll is a 401.
    assert headers.get("X-Api-Key") == "gate-secret", headers

    # The upstream key goes in a header so it never reaches the service's access
    # log, and never reaches the URL the exporter prints at DEBUG.
    assert headers.get("X-OpenWeatherMap-Key") == "owm-secret", headers
    assert "owm-secret" not in url, url
    assert "apikey" not in url, url

    # No timeout means one wedged upstream parks this thread for good.
    assert timeout is not None, "requests.get must carry a timeout"


def test_non_secret_parameters_still_travel_as_query_params():
    module = _load()
    seen = []
    _one_cycle(module, "home", "weathergov", {"agent": "me@example.com"}, seen)

    url, headers, _ = seen[0]
    assert "agent=me%40example.com" in url or "agent=me@example.com" in url, url
    assert "X-OpenWeatherMap-Key" not in headers, headers


def test_thread_registers_under_the_source_it_was_given():
    module = _load()
    # A stale module global by this name used to win over the argument, so every
    # thread registered under whichever source the startup loop assigned last.
    module.source_name = "weathergov"
    _one_cycle(module, "home", "openweathermap", {}, [])

    assert module.active_site_names == ["home.openweathermap"], module.active_site_names


def test_two_sites_sharing_a_source_get_separate_metric_caches():
    module = _load()
    for site_name in ("home", "cabin"):
        _one_cycle(module, site_name, "weathergov", {}, [])

    # Keyed by source alone, the second site overwrote the first's cache and the
    # next poll wiped metrics that were still live.
    assert sorted(module.metric_metadata_cache) == [
        "cabin.weathergov",
        "home.weathergov",
    ], sorted(module.metric_metadata_cache)


def test_error_counter_uses_one_label_set_everywhere():
    """Both weather_error_total call sites must agree.

    prometheus_client fixes a counter's labels at first registration. The
    non-200 branch used to pass {} and the exception handler base_labels;
    whichever ran second raised "Incorrect label count" from inside the except
    handler, where nothing catches it, ending the thread.
    """
    source = open(exporter.__file__, encoding="utf-8").read()
    assert (
        'inc("weather_error_total", {})' not in source
    ), "weather_error_total must always be incremented with base_labels"
    assert source.count('inc("weather_error_total", base_labels)') == 2, source.count(
        'inc("weather_error_total", base_labels)'
    )


if __name__ == "__main__":
    test_gate_key_is_sent_and_upstream_key_stays_out_of_the_url()
    test_non_secret_parameters_still_travel_as_query_params()
    test_thread_registers_under_the_source_it_was_given()
    test_two_sites_sharing_a_source_get_separate_metric_caches()
    test_error_counter_uses_one_label_set_everywhere()
    print("OK")
