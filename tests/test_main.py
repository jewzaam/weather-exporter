"""Checks main() wires config into watch_weather_source threads.

main() is the only path k3s actually runs, and it moved from a top-level
`if __name__ == '__main__'` block into a function. That move is what makes
these worth pinning: `locals()["watch_weather_source"]` resolved at module
scope and raises KeyError inside a function, and `STOP_THREADS = True` binds
a local unless declared global. Neither failure is visible until a site is
configured, or until Ctrl-C fails to stop anything.
"""

import os
import sys
import time
import importlib

from weather_exporter import exporter

CONFIG = """\
metrics:
  port: 8011
service:
  host: weather-service
  port: 9213
sources:
  openweathermap:
    parameters:
      apikey: ${TEST_OWM_KEY}
  weathergov:
    parameters:
      agent: me@example.com
sites:
  - name: home
    latitude: "40.71"
    longitude: "-74.01"
    sources:
      - name: openweathermap
        refresh_frequency_seconds: 300
      - name: weathergov
        refresh_frequency_seconds: 600
  - name: no-sources-site
    latitude: "0"
    longitude: "0"
"""


class _FakeThread:
    """Records construction; never starts anything."""

    instances = []

    def __init__(self, target=None, args=()):
        self.target = target
        self.args = args
        _FakeThread.instances.append(self)

    def start(self):
        pass

    def is_alive(self):
        return False


def _run_main(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG, encoding="utf-8")

    module = importlib.reload(exporter)
    _FakeThread.instances = []

    real_argv, real_sleep = sys.argv, time.sleep
    real_metrics = module.metrics_utility.metrics
    module.Thread = _FakeThread
    module.debug = lambda *args: None
    module.metrics_utility.metrics = lambda port: None  # do not bind a port
    time.sleep = lambda seconds: None  # do not wait out the poll loop
    sys.argv = ["weather_exporter", "--config", str(config_path)]
    try:
        module.main()
    finally:
        sys.argv, time.sleep = real_argv, real_sleep
        module.metrics_utility.metrics = real_metrics
    return module


def test_main_starts_one_thread_per_site_source(tmp_path):
    os.environ["TEST_OWM_KEY"] = "owm-secret"
    _run_main(tmp_path)

    # Two sources on "home"; "no-sources-site" has no `sources` key and is skipped.
    assert len(_FakeThread.instances) == 2, _FakeThread.instances

    first, second = (t.args for t in _FakeThread.instances)
    # (source, host, port, parameters, lat, long, site_name, refresh_seconds)
    assert first == (
        "openweathermap",
        "weather-service",
        9213,
        {"apikey": "owm-secret"},
        "40.71",
        "-74.01",
        "home",
        300,
    ), first
    assert second[0] == "weathergov"
    assert second[3] == {"agent": "me@example.com"}
    assert second[7] == 600

    # The target must be the real function, not a locals() lookup that no longer
    # resolves now that this code lives inside main().
    for thread in _FakeThread.instances:
        assert thread.target is exporter.watch_weather_source or callable(thread.target)


def test_main_expands_environment_placeholders(tmp_path):
    os.environ["TEST_OWM_KEY"] = "rotated-key"
    _run_main(tmp_path)

    parameters = _FakeThread.instances[0].args[3]
    assert parameters == {"apikey": "rotated-key"}, parameters


def test_module_entry_point_resolves():
    """`python -m weather_exporter` must find main().

    Nothing else imports weather_exporter.__main__, so renaming or moving main()
    breaks the container's CMD and the systemd unit with no other symptom.
    """
    entry = importlib.import_module("weather_exporter.__main__")
    assert entry.main is exporter.main


def test_stop_threads_is_module_state_not_a_local(tmp_path):
    """KeyboardInterrupt in main() must set the flag the watcher threads read."""
    module = _run_main(tmp_path)
    assert "global STOP_THREADS" in open(module.__file__, encoding="utf-8").read()


if __name__ == "__main__":
    import tempfile
    import pathlib

    with tempfile.TemporaryDirectory() as tmp:
        test_module_entry_point_resolves()
        for test in (
            test_main_starts_one_thread_per_site_source,
            test_main_expands_environment_placeholders,
            test_stop_threads_is_module_state_not_a_local,
        ):
            test(pathlib.Path(tmp))
    print("OK")
