"""The supplied catalog is the only station-selection source."""

from copy import deepcopy
import hashlib
import json

import pytest

from custom_components.tmd_weather.stations import (
    CATALOG_PATH,
    TMDStationDatabaseError,
    async_load_stations,
    get_all_stations,
    get_regions,
    get_station_by_id,
    get_stations_by_region,
    parse_station_database,
)


async def test_supplied_catalog(hass):
    """Validate all supplied records and preserve the original file bytes/text."""
    stations = await async_load_stations(hass)
    assert len(stations) == 227
    assert len({station.aws_id for station in stations}) == 227
    assert (
        hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest()
        == "cd8ed37a0ba5c138dab7abc2dd5978572a70ed7602b47c43b18eee15abd61913"
    )
    for aws_id, number, name in (
        (1, "0001", "Chiang Mai Airport Weather Observing Station"),
        (2, "0002", "Tak Weather Observing Station"),
        (60, "0060", "Thaphra Agrometeorlogical Station"),
        (1035, "1035", "Ban Khok Automatic Weather Station"),
    ):
        record = get_station_by_id(aws_id)
        assert record.number == number
        assert record.name == name
    assert get_all_stations() is stations
    assert stations[0].latitude == pytest.approx(18.77111053466797)
    assert stations[0].longitude == pytest.approx(98.96916961669922)
    assert get_station_by_id(99999) is None
    assert get_station_by_id(True) is None
    assert len(get_regions()) == 6
    assert all(s.region == "Northern" for s in get_stations_by_region("Northern"))
    assert not get_stations_by_region("Missing")


@pytest.mark.parametrize("payload", [None, {}, [], [None], [True]])
def test_invalid_catalog_shape(payload):
    with pytest.raises(TMDStationDatabaseError):
        parse_station_database(payload)


@pytest.mark.parametrize(
    "key,value",
    [
        ("number", 1),
        ("name", ""),
        ("region", None),
        ("type", " "),
        ("value", True),
        ("value", None),
        ("value", 0),
        ("value", 1.5),
        ("value", float("inf")),
        ("value", "abc"),
        ("value", "\u0661"),
        ("lat", None),
        ("lat", True),
        ("lat", "18.75"),
        ("lat", 90.1),
        ("lat", -90.1),
        ("lat", float("nan")),
        ("long", None),
        ("long", False),
        ("long", "100.5"),
        ("long", 180.1),
        ("long", -180.1),
        ("long", float("inf")),
    ],
)
def test_invalid_record(key, value):
    record = json.loads(CATALOG_PATH.read_text())[0]
    record[key] = value
    with pytest.raises(TMDStationDatabaseError):
        parse_station_database([record])


def test_duplicate_id():
    record = json.loads(CATALOG_PATH.read_text())[0]
    with pytest.raises(TMDStationDatabaseError, match="Duplicate"):
        parse_station_database([record, deepcopy(record)])


@pytest.mark.parametrize("key", ["lat", "long"])
def test_missing_coordinate(key):
    record = json.loads(CATALOG_PATH.read_text())[0]
    del record[key]
    with pytest.raises(TMDStationDatabaseError):
        parse_station_database([record])


def test_integer_compatible_id():
    record = json.loads(CATALOG_PATH.read_text())[0]
    record["value"] = "1"
    assert parse_station_database([record])[0].aws_id == 1


def test_missing_file(tmp_path, monkeypatch):
    get_all_stations.cache_clear()
    monkeypatch.setattr(
        "custom_components.tmd_weather.stations.CATALOG_PATH", tmp_path / "missing.json"
    )
    try:
        with pytest.raises(TMDStationDatabaseError, match="Cannot read"):
            get_all_stations()
    finally:
        get_all_stations.cache_clear()
