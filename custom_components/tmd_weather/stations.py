"""Immutable catalog supplied by the user; never discover station IDs online."""

from dataclasses import dataclass
from functools import lru_cache
import json
import logging
import math
from pathlib import Path

from homeassistant.core import HomeAssistant

CATALOG_PATH = Path(__file__).parent / "data/tmd_aws_stations.json"
_LOGGER = logging.getLogger(__name__)


class TMDStationDatabaseError(ValueError):
    """The packaged station database is missing or malformed."""


@dataclass(frozen=True, slots=True)
class TMDAWSStationRecord:
    """Preserve the catalog's display text and normalized API ID."""

    number: str
    aws_id: int
    name: str
    station_type: str
    region: str
    latitude: float
    longitude: float


def parse_station_database(payload: object) -> tuple[TMDAWSStationRecord, ...]:
    """Reject the entire invalid catalog rather than silently dropping records."""
    if not isinstance(payload, list) or not payload:
        raise TMDStationDatabaseError("Station database must be a nonempty list")
    result = []
    seen: set[int] = set()
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise TMDStationDatabaseError(f"Station record {index} is not an object")
        for key in ("number", "name", "type", "region"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise TMDStationDatabaseError(
                    f"Station record {index} has an invalid {key}"
                )
        value = item.get("value")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float, str))
            or (isinstance(value, str) and not value.isascii())
        ):
            raise TMDStationDatabaseError(f"Station record {index} has an invalid ID")
        try:
            aws_id = int(value)
        except ValueError, OverflowError:
            raise TMDStationDatabaseError(
                f"Station record {index} has an invalid ID"
            ) from None
        if aws_id <= 0 or (isinstance(value, float) and value != aws_id):
            raise TMDStationDatabaseError(f"Station record {index} has an invalid ID")
        if aws_id in seen:
            raise TMDStationDatabaseError(f"Duplicate AWS station ID {aws_id}")
        seen.add(aws_id)
        coordinates = []
        for key, limit in (("lat", 90), ("long", 180)):
            coordinate = item.get(key)
            if (
                isinstance(coordinate, bool)
                or not isinstance(coordinate, (int, float))
                or not math.isfinite(coordinate)
                or not -limit <= coordinate <= limit
            ):
                raise TMDStationDatabaseError(
                    f"Station record {index} has an invalid {key}"
                )
            coordinates.append(float(coordinate))
        result.append(
            TMDAWSStationRecord(
                item["number"],
                aws_id,
                item["name"],
                item["type"],
                item["region"],
                coordinates[0],
                coordinates[1],
            )
        )
    return tuple(result)


@lru_cache(maxsize=1)
def get_all_stations() -> tuple[TMDAWSStationRecord, ...]:
    """Read the supplied packaged file once; call first from an executor."""
    try:
        payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except OSError, ValueError:
        raise TMDStationDatabaseError(
            "Cannot read packaged AWS station database"
        ) from None
    return parse_station_database(payload)


async def async_load_stations(hass: HomeAssistant) -> tuple[TMDAWSStationRecord, ...]:
    """Keep disk access off Home Assistant's event loop."""
    try:
        return await hass.async_add_executor_job(get_all_stations)
    except TMDStationDatabaseError as error:
        _LOGGER.error("AWS station database could not be loaded: %s", error)
        raise


def get_station_by_id(aws_id: object) -> TMDAWSStationRecord | None:
    """Resolve an API ID from the local catalog only."""
    if isinstance(aws_id, bool) or not isinstance(aws_id, int):
        return None
    return next(
        (station for station in get_all_stations() if station.aws_id == aws_id), None
    )


def get_stations_by_region(region: str) -> tuple[TMDAWSStationRecord, ...]:
    """Filter using the original region text."""
    return tuple(station for station in get_all_stations() if station.region == region)


def get_regions() -> tuple[str, ...]:
    """Return the supplied region groups without geographic inference."""
    return tuple(sorted({station.region for station in get_all_stations()}))
