"""Checks update_metrics still maps every forecast key to the right metric.

METRIC_KEYS replaced a chain of if/elif branches. The mapping is the whole
behaviour of update_metrics, so it is the thing worth pinning: a typo in the
table silently renames a metric or drops it, and the exporter keeps running.
"""

from weather_exporter import exporter

# Every key weather-service emits, plus the two shapes that must be skipped.
FORECAST = {
    "dt": 1757260800,  # not a dict
    "temperature": {"value": 21.5, "uom": "celsius"},
    "apparentTemperature": {"value": 20.1, "uom": "celsius"},
    "dewpoint": {"value": 12.0, "uom": "celsius"},
    "relativeHumidity": {"value": 55, "uom": "percent"},
    "skyCover": {"value": 40, "uom": "percent"},
    "windDirection": {"value": 270, "uom": "degrees"},
    "windSpeed": {"value": 3.1, "uom": "kph"},
    "windGust": {"value": 7.2, "uom": "kph"},
    "probabilityOfPrecipitation": {"value": 10.0, "uom": "percent"},
    "quantitativePrecipitation": {"value": 0.4, "uom": "mm"},
    "pressure": {"value": 1013, "uom": "millibars"},
    "visibility": {"value": 10000, "uom": "meters"},
    "weather": {"value": "light rain"},  # no uom
    "somethingNew": {"value": 1, "uom": "x"},  # unknown key
}

EXPECTED = [
    ("weather_temperature", 21.5, {"type": "current", "unit": "celsius"}),
    ("weather_temperature", 20.1, {"type": "feels_like", "unit": "celsius"}),
    ("weather_dew_point", 12.0, {"unit": "celsius"}),
    ("weather_humidity", 55, {"unit": "percent"}),
    ("weather_clouds", 40, {"unit": "percent"}),
    ("weather_wind", 270, {"type": "direction", "unit": "degrees"}),
    ("weather_wind", 3.1, {"type": "speed", "unit": "kph"}),
    ("weather_wind", 7.2, {"type": "gust", "unit": "kph"}),
    ("weather_precip_probability", 10.0, {"unit": "percent"}),
    ("weather_precip_intensity", 0.4, {"unit": "mm"}),
    ("weather_pressure", 1013, {"unit": "millibars"}),
    ("weather_visibility", 10000, {"unit": "meters"}),
]


def _capture(forecast, base_labels):
    """Run update_metrics with metric_set stubbed out, returning its calls."""
    calls = []
    original = exporter.metric_set
    exporter.metric_set = lambda name, value, labels: calls.append(
        (name, value, dict(labels))
    )
    try:
        metadata = exporter.update_metrics(forecast, dict(base_labels))
    finally:
        exporter.metric_set = original
    return calls, metadata


def test_update_metrics():
    base_labels = {"site": "home", "source": "weathergov", "when": "now"}

    calls, metadata = _capture(FORECAST, base_labels)

    # Names, values and per-metric labels, in forecast key order.
    stripped = [
        (name, value, {k: v for k, v in labels.items() if k not in base_labels})
        for name, value, labels in calls
    ]
    assert stripped == EXPECTED, stripped

    # `dt`, `weather` (no uom) and unknown keys stay out entirely.
    assert len(calls) == len(EXPECTED)
    assert all("weather_weather" != name for name, _, _ in calls)

    # base_labels ride along on every metric — that is what identifies the site.
    for _, _, labels in calls:
        assert labels["site"] == "home"
        assert labels["when"] == "now"

    # The returned metadata is what the caller diffs to decide which gauges to
    # clear, so it must be [name-suffix, labels] with the base labels excluded.
    assert metadata == [
        [name.removeprefix("weather_"), labels] for name, _, labels in stripped
    ], metadata


def test_metric_keys_table_is_not_mutated():
    # update_metrics adds "unit" to the labels it builds; if it did that on the
    # table entry itself, the first forecast's uom would stick forever.
    before = dict(exporter.METRIC_KEYS)
    _capture(FORECAST, {"site": "home"})
    assert exporter.METRIC_KEYS == before, exporter.METRIC_KEYS


if __name__ == "__main__":
    test_update_metrics()
    test_metric_keys_table_is_not_mutated()
    print("OK")
