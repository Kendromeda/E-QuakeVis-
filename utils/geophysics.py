"""Ad-hoc geophysical survey grids for SP, gravity and magnetic anomaly maps.

This is the interpolation core only. It turns a table of scattered station
readings into a regular grid that the map layers can colour. The method-specific
reduction chains (SP reference/drift/closure, gravity drift/tide/latitude/
free-air/Bouguer, magnetic diurnal/IGRF) are deliberately *not* here yet: they
are built on top of this once the core is proven, so a wrong reduction formula
can never be mistaken for a gridding bug.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Collection, Sequence

import numpy as np
import pandas as pd

from utils.map_builder import _ordinary_kriging_grid


class GeophysicsDataError(ValueError):
    """Raised when a survey table cannot be used for mapping."""


@dataclass(frozen=True)
class GeophysicsMethod:
    """A survey method and how its values should be presented."""

    name: str
    unit: str
    # Anomaly values straddle zero, so their legend needs a diverging ramp
    # centred on zero rather than a sequential one.
    diverging: bool = True
    description: str = ""


SELF_POTENTIAL = GeophysicsMethod(
    "Self-Potential (SP)",
    "mV",
    description="Potensial diri, biasanya relatif terhadap elektroda referensi.",
)
GRAVITY = GeophysicsMethod(
    "Gravity",
    "mGal",
    description="Anomali gayaberat; sebutkan jenis anomalinya pada judul peta.",
)
MAGNETICS = GeophysicsMethod(
    "Magnetik",
    "nT",
    description="Anomali medan magnet total.",
)
GEOPHYSICS_METHODS: dict[str, GeophysicsMethod] = {
    method.name: method for method in (SELF_POTENTIAL, GRAVITY, MAGNETICS)
}

INTERPOLATION_KRIGING = "Ordinary kriging"
INTERPOLATION_IDW = "IDW"
INTERPOLATION_MINIMUM_CURVATURE = "Minimum curvature"
INTERPOLATION_METHODS = (
    INTERPOLATION_KRIGING,
    INTERPOLATION_IDW,
    INTERPOLATION_MINIMUM_CURVATURE,
)

# Column names surveys tend to use, so a plain field CSV imports without the
# user having to rename anything first.
_LONGITUDE_ALIASES = ("bujur", "longitude", "lon", "long", "x", "easting")
_LATITUDE_ALIASES = ("lintang", "latitude", "lat", "y", "northing")

MINIMUM_SAMPLES = 3
MAX_MINIMUM_CURVATURE_SAMPLES = 500


@dataclass(frozen=True)
class SurveyTable:
    """Validated survey readings ready for gridding."""

    samples: tuple[tuple[float, float, float], ...]
    method: GeophysicsMethod
    value_label: str
    dropped_rows: int

    @property
    def values(self) -> tuple[float, ...]:
        return tuple(sample[2] for sample in self.samples)

    def bounds(self, padding_percent: float = 8.0) -> tuple[float, float, float, float]:
        """West, south, east, north around the stations, with a margin."""
        longitudes = [sample[0] for sample in self.samples]
        latitudes = [sample[1] for sample in self.samples]
        west, east = min(longitudes), max(longitudes)
        south, north = min(latitudes), max(latitudes)
        # A single line of stations has zero width in one axis; give it a floor
        # so the grid never collapses.
        longitude_span = max(east - west, 0.01)
        latitude_span = max(north - south, 0.01)
        margin_x = longitude_span * padding_percent / 100.0
        margin_y = latitude_span * padding_percent / 100.0
        centre_x = (west + east) / 2.0
        centre_y = (south + north) / 2.0
        return (
            centre_x - longitude_span / 2.0 - margin_x,
            centre_y - latitude_span / 2.0 - margin_y,
            centre_x + longitude_span / 2.0 + margin_x,
            centre_y + latitude_span / 2.0 + margin_y,
        )


def _match_column(frame: pd.DataFrame, aliases: Collection[str]) -> str | None:
    lowered = {str(column).strip().lower(): str(column) for column in frame.columns}
    for alias in aliases:
        if alias in lowered:
            return lowered[alias]
    return None


def read_survey_table(
    frame: pd.DataFrame,
    *,
    method_name: str,
    value_column: str,
    longitude_column: str | None = None,
    latitude_column: str | None = None,
) -> SurveyTable:
    """Validate a survey table and return the finite stations it contains.

    Rows with unusable coordinates or values are dropped and counted rather
    than silently interpolated over: a survey that lost half its stations to
    bad parsing should say so before anyone maps it.
    """
    if method_name not in GEOPHYSICS_METHODS:
        raise GeophysicsDataError(f"Metode geofisika tidak dikenal: {method_name}")
    longitude_column = longitude_column or _match_column(frame, _LONGITUDE_ALIASES)
    latitude_column = latitude_column or _match_column(frame, _LATITUDE_ALIASES)
    if longitude_column is None or latitude_column is None:
        raise GeophysicsDataError(
            "Kolom koordinat tidak ditemukan. Sertakan kolom bujur/longitude "
            "dan lintang/latitude, atau pilih kolomnya secara manual."
        )
    for column in (longitude_column, latitude_column, value_column):
        if column not in frame.columns:
            raise GeophysicsDataError(f"Kolom '{column}' tidak ada pada tabel.")

    longitudes = pd.to_numeric(frame[longitude_column], errors="coerce")
    latitudes = pd.to_numeric(frame[latitude_column], errors="coerce")
    values = pd.to_numeric(frame[value_column], errors="coerce")
    usable = (
        longitudes.notna()
        & latitudes.notna()
        & values.notna()
        & longitudes.between(-180.0, 180.0)
        & latitudes.between(-90.0, 90.0)
    )
    samples = tuple(
        (float(longitude), float(latitude), float(value))
        for longitude, latitude, value in zip(
            longitudes[usable], latitudes[usable], values[usable]
        )
    )
    if len(samples) < MINIMUM_SAMPLES:
        raise GeophysicsDataError(
            f"Hanya {len(samples)} titik yang dapat dipakai; minimal "
            f"{MINIMUM_SAMPLES} titik diperlukan untuk membuat grid."
        )
    return SurveyTable(
        samples=samples,
        method=GEOPHYSICS_METHODS[method_name],
        value_label=str(value_column),
        dropped_rows=int((~usable).sum()),
    )


def _grid_axes(
    bounds: tuple[float, float, float, float],
    width: int,
    height: int,
) -> tuple[np.ndarray, np.ndarray]:
    west, south, east, north = bounds
    xs = np.linspace(west, east, width)
    # Row 0 is the north edge, matching how the raster layers are drawn.
    ys = np.linspace(north, south, height)
    return xs, ys


def _local_metres_per_degree(
    bounds: tuple[float, float, float, float],
) -> tuple[float, float]:
    """Degrees are not square; scale longitude by the local latitude."""
    _, south, _, north = bounds
    centre_latitude = (south + north) / 2.0
    return (
        111_320.0 * max(0.2, math.cos(math.radians(centre_latitude))),
        110_574.0,
    )


def _idw_grid(
    samples: Sequence[tuple[float, float, float]],
    bounds: tuple[float, float, float, float],
    width: int,
    height: int,
    power: float = 2.0,
) -> np.ndarray:
    """Inverse distance weighting, exact at the stations themselves."""
    array = np.asarray(samples, dtype=np.float64)
    xs, ys = _grid_axes(bounds, width, height)
    x_scale, y_scale = _local_metres_per_degree(bounds)
    grid_x, grid_y = np.meshgrid(xs * x_scale, ys * y_scale)
    sample_x = array[:, 0] * x_scale
    sample_y = array[:, 1] * y_scale
    values = array[:, 2]

    weights_total = np.zeros((height, width), dtype=np.float64)
    weighted_sum = np.zeros((height, width), dtype=np.float64)
    exact = np.full((height, width), np.nan, dtype=np.float64)
    for x, y, value in zip(sample_x, sample_y, values):
        distance = np.hypot(grid_x - x, grid_y - y)
        # A node sitting on a station takes that station's value outright,
        # otherwise the reciprocal weight explodes.
        coincident = distance < 1e-6
        if coincident.any():
            exact[coincident] = value
        weight = 1.0 / np.power(np.maximum(distance, 1e-6), power)
        weights_total += weight
        weighted_sum += weight * value
    grid = weighted_sum / np.maximum(weights_total, 1e-12)
    return np.where(np.isnan(exact), grid, exact)


def _minimum_curvature_grid(
    samples: Sequence[tuple[float, float, float]],
    bounds: tuple[float, float, float, float],
    width: int,
    height: int,
) -> np.ndarray:
    """Interpolate a stable thin-plate (minimum-curvature) spline surface.

    A thin-plate spline minimizes integrated squared curvature while its affine
    terms reproduce planar trends exactly.  Solving the constrained radial-basis
    system avoids the divergent wide-stencil relaxation previously used here.
    """
    array = np.asarray(samples, dtype=np.float64)
    if len(array) > MAX_MINIMUM_CURVATURE_SAMPLES:
        raise GeophysicsDataError(
            "Minimum curvature dibatasi "
            f"{MAX_MINIMUM_CURVATURE_SAMPLES} titik agar penyelesaian tetap stabil."
        )
    if np.allclose(array[:, 2], array[0, 2]):
        return np.full((height, width), float(array[0, 2]), dtype=np.float64)

    x_scale, y_scale = _local_metres_per_degree(bounds)
    coordinates = np.column_stack((array[:, 0] * x_scale, array[:, 1] * y_scale))
    centre = coordinates.mean(axis=0)
    spatial_scale = max(float(np.ptp(coordinates[:, 0])), float(np.ptp(coordinates[:, 1])), 1.0)
    normalized = (coordinates - centre) / spatial_scale

    def kernel(distance_squared: np.ndarray) -> np.ndarray:
        safe = np.maximum(distance_squared, 1e-24)
        values = 0.5 * safe * np.log(safe)
        return np.where(distance_squared <= 1e-24, 0.0, values)

    differences = normalized[:, None, :] - normalized[None, :, :]
    radial = kernel(np.sum(differences * differences, axis=2))
    # Tiny smoothing stabilizes nearly coincident stations without flattening
    # the field. Callers should aggregate exact duplicates before reaching here.
    radial.flat[:: len(array) + 1] += 1e-10
    affine = np.column_stack((np.ones(len(array)), normalized))
    system = np.zeros((len(array) + 3, len(array) + 3), dtype=np.float64)
    system[:-3, :-3] = radial
    system[:-3, -3:] = affine
    system[-3:, :-3] = affine.T
    target = np.concatenate((array[:, 2], np.zeros(3, dtype=np.float64)))
    try:
        coefficients = np.linalg.solve(system, target)
    except np.linalg.LinAlgError:
        coefficients = np.linalg.pinv(system, rcond=1e-11) @ target

    xs, ys = _grid_axes(bounds, width, height)
    grid_x, grid_y = np.meshgrid(xs * x_scale, ys * y_scale)
    targets = (
        np.column_stack((grid_x.ravel(), grid_y.ravel())) - centre
    ) / spatial_scale
    predicted = np.empty(len(targets), dtype=np.float64)
    chunk_size = 4096
    for start in range(0, len(targets), chunk_size):
        stop = min(start + chunk_size, len(targets))
        target_chunk = targets[start:stop]
        delta = target_chunk[:, None, :] - normalized[None, :, :]
        basis = kernel(np.sum(delta * delta, axis=2))
        target_affine = np.column_stack((np.ones(len(target_chunk)), target_chunk))
        predicted[start:stop] = (
            basis @ coefficients[:-3] + target_affine @ coefficients[-3:]
        )
    return predicted.reshape(height, width)


def interpolate_survey_grid(
    samples: Sequence[tuple[float, float, float]],
    bounds: tuple[float, float, float, float],
    width: int,
    height: int,
    method: str = INTERPOLATION_KRIGING,
    *,
    idw_power: float = 2.0,
    cache_kriging: bool = True,
) -> np.ndarray:
    """Grid scattered station readings with the chosen interpolator."""
    if width < 2 or height < 2:
        raise GeophysicsDataError("Ukuran grid minimal 2x2 sel.")
    if not samples:
        raise GeophysicsDataError("Tidak ada titik pengukuran untuk diinterpolasi.")
    if method == INTERPOLATION_KRIGING:
        kriging = _ordinary_kriging_grid
        if not cache_kriging:
            kriging = getattr(_ordinary_kriging_grid, "__wrapped__", _ordinary_kriging_grid)
        return kriging(tuple(samples), bounds, width, height)
    if method == INTERPOLATION_IDW:
        return _idw_grid(samples, bounds, width, height, idw_power)
    if method == INTERPOLATION_MINIMUM_CURVATURE:
        return _minimum_curvature_grid(samples, bounds, width, height)
    raise GeophysicsDataError(f"Metode interpolasi tidak dikenal: {method}")


def symmetric_breaks(values: Collection[float], classes: int = 8) -> tuple[float, ...]:
    """Class edges centred on zero, for anomalies that swing both ways.

    A sequential scale would hide the sign of an anomaly, which is the first
    thing a reader of an SP or gravity map looks for.
    """
    if classes < 2:
        raise GeophysicsDataError("Jumlah kelas minimal 2.")
    finite = [float(value) for value in values if math.isfinite(float(value))]
    if not finite:
        raise GeophysicsDataError("Tidak ada nilai berhingga untuk membuat kelas.")
    extent = max(abs(min(finite)), abs(max(finite)))
    if extent <= 0:
        extent = 1.0
    return tuple(
        -extent + 2.0 * extent * index / classes for index in range(classes + 1)
    )
