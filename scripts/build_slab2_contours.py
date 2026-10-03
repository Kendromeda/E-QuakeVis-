"""Build Slab2 depth contours for the eastern Indonesian models.

Rebuild-time helper. The official Slab2 release ships GMT contour files per
region, but only the Sunda one (``data/sum_slab2_dep_contours.in``) is present
locally and the ScienceBase distribution cannot be fetched automatically. The
remaining contours are therefore traced from the verified Slab2 depth grids in
``data/slab2/<id>.npz`` (Hayes dkk., 2018; DOI 10.5066/F7PV6JNV).

Tracing is plain marching squares on the native 0.05 degree grid: a contour is
only drawn through cells whose four corners are valid nodes, so the lines never
leave the published slab mask and nothing is interpolated into nodata. The
output uses the same GMT layout as the official file (``> -100 contour -Z-100``
headers, ``lon lat -depth`` rows) so ``utils.slab_data.load_slab_contours``
reads it unchanged.

    python scripts/build_slab2_contours.py            # sul hal png phi
    python scripts/build_slab2_contours.py --models sum --output-dir tmp
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]
GRID_DIR = ROOT_DIR / "data" / "slab2"
DEFAULT_MODELS = ("sul", "hal", "png", "phi")
CONTOUR_INTERVAL_KM = 20
MAX_CONTOUR_KM = 660
MIN_LINE_VERTICES = 3
MAX_EDGE_STUB_VERTICES = 3

Edge = tuple[str, int, int]


def trace_contours(
    lon: np.ndarray,
    lat: np.ndarray,
    depth: np.ndarray,
    level: float,
) -> list[list[tuple[float, float]]]:
    """Return polylines of ``depth == level`` from a ``depth[lat][lon]`` grid."""
    v0 = depth[:-1, :-1]
    v1 = depth[:-1, 1:]
    v2 = depth[1:, 1:]
    v3 = depth[1:, :-1]
    finite = [np.isfinite(corner) for corner in (v0, v1, v2, v3)]
    valid_count = sum(flag.astype(np.int8) for flag in finite)
    with np.errstate(invalid="ignore"):
        low = np.fmin(np.fmin(v0, v1), np.fmin(v2, v3))
        high = np.fmax(np.fmax(v0, v1), np.fmax(v2, v3))
    # Cells with at least three valid corners whose valid values straddle the level.
    candidates = (valid_count >= 3) & (low < level) & (high >= level)

    # Corner k of cell (j, i) sits at node offset CORNER_OFFSETS[k].
    corner_offsets = ((0, 0), (0, 1), (1, 1), (1, 0))

    def edge_point(edge: Edge) -> tuple[float, float]:
        kind, j, i = edge
        if kind == "h":  # node (j, i) -> (j, i + 1)
            start, end = (j, i), (j, i + 1)
        elif kind == "v":  # node (j, i) -> (j + 1, i)
            start, end = (j, i), (j + 1, i)
        else:  # "d<a><b>": diagonal between corners a and b of cell (j, i)
            a, b = int(kind[1]), int(kind[2])
            start = (j + corner_offsets[a][0], i + corner_offsets[a][1])
            end = (j + corner_offsets[b][0], i + corner_offsets[b][1])
        t = (level - depth[start]) / (depth[end] - depth[start])
        return (
            float(lon[start[1]] + t * (lon[end[1]] - lon[start[1]])),
            float(lat[start[0]] + t * (lat[end[0]] - lat[start[0]])),
        )

    segments: list[tuple[Edge, Edge]] = []
    for j, i in zip(*np.nonzero(candidates)):
        corners = (depth[j, i], depth[j, i + 1], depth[j + 1, i + 1], depth[j + 1, i])
        flags = [value >= level for value in corners]
        # Edge k joins corner k and corner (k + 1) % 4.
        edges: tuple[Edge, ...] = (("h", j, i), ("v", j, i + 1), ("h", j + 1, i), ("v", j, i))
        missing = [k for k in range(4) if not np.isfinite(corners[k])]
        if missing:
            # Slab-mask edge: contour the triangle of the three valid nodes, the
            # same footprint the official Slab2 contours reach. The diagonal is
            # internal to this cell, so a line ending there stops at the mask.
            n = missing[0]
            a, b, c = (n + 1) % 4, (n + 2) % 4, (n + 3) % 4
            triangle = (
                (a, b, edges[a]),
                (b, c, edges[b]),
                (c, a, (f"d{min(a, c)}{max(a, c)}", j, i)),
            )
            crossed_edges = [edge for p, q, edge in triangle if flags[p] != flags[q]]
            if len(crossed_edges) == 2:
                segments.append((crossed_edges[0], crossed_edges[1]))
            continue
        crossed = [k for k in range(4) if flags[k] != flags[(k + 1) % 4]]
        if len(crossed) == 2:
            segments.append((edges[crossed[0]], edges[crossed[1]]))
            continue
        # Saddle: the cell centre decides which diagonal stays connected.
        centre_above = float(np.mean(corners)) >= level
        if flags[0] == centre_above:
            pairs = ((0, 1), (2, 3))  # corners 0 and 2 joined; isolate 1 and 3
        else:
            pairs = ((3, 0), (1, 2))  # corners 1 and 3 joined; isolate 0 and 2
        for a, b in pairs:
            segments.append((edges[a], edges[b]))

    by_edge: dict[Edge, list[int]] = {}
    for index, (start, end) in enumerate(segments):
        by_edge.setdefault(start, []).append(index)
        by_edge.setdefault(end, []).append(index)
    used = [False] * len(segments)

    def walk(edge: Edge) -> list[Edge]:
        chain = [edge]
        while True:
            following = [s for s in by_edge[edge] if not used[s]]
            if not following:
                return chain
            used[following[0]] = True
            start, end = segments[following[0]]
            edge = end if start == edge else start
            chain.append(edge)

    lines: list[list[tuple[float, float]]] = []
    for index, (start, end) in enumerate(segments):
        if used[index]:
            continue
        used[index] = True
        chain = list(reversed(walk(start))) + walk(end)
        if len(chain) < MIN_LINE_VERTICES:
            continue
        # A stub that only clips a corner of the ragged mask edge is a staircase
        # artefact, not a contour; the official files carry no such fragments.
        if len(chain) <= MAX_EDGE_STUB_VERTICES and any(
            edge[0].startswith("d") for edge in chain
        ):
            continue
        lines.append([edge_point(edge) for edge in chain])
    return lines


def write_contours(model: str, output_dir: Path) -> Path:
    grid = np.load(GRID_DIR / f"{model}.npz")
    lon, lat, depth = grid["lon"], grid["lat"], grid["depth"]
    deepest = float(np.nanmax(depth))
    output = output_dir / f"{model}_slab2_dep_contours.in"
    rows: list[str] = []
    for level in range(CONTOUR_INTERVAL_KM, MAX_CONTOUR_KM + 1, CONTOUR_INTERVAL_KM):
        if level > deepest:
            break
        for line in trace_contours(lon, lat, depth, float(level)):
            rows.append(f"> -{level} contour -Z-{level}")
            rows.extend(f"{x:.4f}\t{y:.4f}\t-{level:.4f}" for x, y in line)
    output.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"{model}: {sum(row.startswith('>') for row in rows)} lines -> {output.name}")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    parser.add_argument("--output-dir", type=Path, default=GRID_DIR)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for model in args.models:
        write_contours(model, args.output_dir)


if __name__ == "__main__":
    main()
