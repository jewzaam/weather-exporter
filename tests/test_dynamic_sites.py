"""Checks the dynamic-site loop in main().

Sites can also come from a Prometheus query (telescope mount coordinates) rather
than the config file. The loop spawns a watcher per newly-seen host, skips hosts
it is already watching, and drops the ones that stopped reporting. It is the
least-exercised code in the exporter and the easiest to break silently: a stale
site keeps publishing weather for a location nobody is at.

Not used by the k3s deployment, which configures static sites only.
"""

import sys
import json
import time
import importlib

import requests

from weather_exporter import exporter

CONFIG = """\
metrics:
  port: 8011
service:
  host: weather-service
  port: 9213
prometheus:
  username: prom-user
  password: prom-pass
  host: prometheus
  port: 9090
  query: alpaca_telescope_sitelatitude
dynamic_sites:
  location_round: 2
  sources:
    - name: weathergov
      refresh_frequency_seconds: 300
sources:
  weathergov:
    parameters:
      agent: me@example.com
sites:
  - name: home
    latitude: "40.71"
    longitude: "-74.01"
    sources:
      - name: weathergov
        refresh_frequency_seconds: 300
"""

QUERY_RESULT = {
    "status": "success",
    "data": {
        "result": [
            {
                "metric": {
                    "host": "mount-1",
                    "latitude": "40.7128",
                    "longitude": "-74.0060",
                }
            },
            {
                "metric": {"host": "mount-2"}
            },  # no coordinates -> skipped, loop continues
        ]
    },
}


class _FakeThread:
    """Registers itself the way the real watcher thread does, then does nothing."""

    instances = []

    def __init__(self, target=None, args=()):
        self.target = target
        self.args = args
        _FakeThread.instances.append(self)

    def start(self):
        # watch_weather_source registers `<site>.<source>` on entry; the loop
        # relies on that to avoid spawning a second watcher for the same host.
        site_name, source = self.args[6], self.args[0]
        exporter.active_site_names.append(f"{site_name}.{source}")

    def is_alive(self):
        return False


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.text = json.dumps(payload) if payload is not None else ""


def _run_main(tmp_path, response, stale_sites=()):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG, encoding="utf-8")

    module = importlib.reload(exporter)
    _FakeThread.instances = []
    module.active_site_names.extend(stale_sites)

    real_argv, real_sleep, real_get = sys.argv, time.sleep, requests.get
    real_metrics = module.metrics_utility.metrics
    module.Thread = _FakeThread
    module.debug = lambda *args: None
    module.metrics_utility.metrics = lambda port: None
    time.sleep = lambda seconds: None
    requests.get = lambda url, **kwargs: response
    sys.argv = ["weather_exporter", "--config", str(config_path)]
    try:
        module.main()
    finally:
        sys.argv, time.sleep, requests.get = real_argv, real_sleep, real_get
        module.metrics_utility.metrics = real_metrics
    return module


def test_a_reporting_host_gets_one_watcher_and_only_one(tmp_path):
    module = _run_main(tmp_path, _FakeResponse(payload=QUERY_RESULT))

    dynamic = [
        t
        for t in _FakeThread.instances
        if t.args[6].startswith(module.DYNAMIC_SITE_PREFIX)
    ]
    # mount-1 spawns exactly one watcher across both poll passes; mount-2 has no
    # coordinates and is swallowed by the per-result guard without killing the loop.
    assert len(dynamic) == 1, [t.args[6] for t in dynamic]

    source, host, port, parameters, lat, long, site_name, refresh = dynamic[0].args
    assert site_name == "dynamic_site.mount-1", site_name
    assert (lat, long) == (40.71, -74.01), (lat, long)  # rounded to location_round
    assert source == "weathergov"
    assert parameters == {"agent": "me@example.com"}
    assert refresh == 300
    assert (host, port) == ("weather-service", 9213)


def test_a_host_that_stopped_reporting_is_dropped(tmp_path):
    module = _run_main(
        tmp_path,
        _FakeResponse(payload=QUERY_RESULT),
        stale_sites=["dynamic_site.retired-mount.weathergov"],
    )

    # The watcher thread clears its own metrics; the loop's job is to stop
    # listing it as active so the thread sees it should exit.
    assert (
        "dynamic_site.retired-mount.weathergov" not in module.active_site_names
    ), module.active_site_names
    assert (
        "dynamic_site.mount-1.weathergov" in module.active_site_names
    ), module.active_site_names


def test_a_failed_prometheus_query_spawns_nothing(tmp_path):
    module = _run_main(tmp_path, _FakeResponse(status_code=503))

    dynamic = [
        t
        for t in _FakeThread.instances
        if t.args[6].startswith(module.DYNAMIC_SITE_PREFIX)
    ]
    assert dynamic == [], dynamic


if __name__ == "__main__":
    import tempfile
    import pathlib

    for test in (
        test_a_reporting_host_gets_one_watcher_and_only_one,
        test_a_host_that_stopped_reporting_is_dropped,
        test_a_failed_prometheus_query_spawns_nothing,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            test(pathlib.Path(tmp))
    print("OK")
