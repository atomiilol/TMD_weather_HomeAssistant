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
        == "9144e1d04f8f99e8cb02d6a3914300b31cef95f67e120d6aaa93fc6ec5b3ade1"
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
