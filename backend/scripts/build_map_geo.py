"""Turn Natural Earth boundaries into the committed SVG paths the centers map draws.

Run once (or when the frame changes); the OUTPUT is what ships. Nothing in the
app fetches geography at runtime — the deployed CSP allows no external host, and
a shop map that needs the internet to draw itself is no use in a stockroom.

    uv run python backend/scripts/build_map_geo.py \
        --admin1 ne_50m_admin_1_states_provinces.geojson \
        --countries ne_110m_admin_0_countries.geojson \
        --out frontend/src/pages/admin/mapGeo.ts

Source data: Natural Earth (public domain). The two files are NOT committed —
they are 2.5MB of input for a 70KB output. Fetch them when you need to re-run:

  ne_50m_admin_1_states_provinces.geojson  (US states + Canadian provinces)
  ne_110m_admin_0_countries.geojson        (the Mexico silhouette at the border)

  https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/<file>

Projection: Albers equal-area conic, the standard choice for North America —
it keeps the relative size of the states honest, which a Mercator does not.
The same constants live in the generated file so the frontend can place a city
from its latitude/longitude with exactly this maths.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

# Albers conic parameters tuned for the contiguous US + southern Canada, the
# only places this app has centers.
LAT0, LON0 = 39.0, -96.0  # origin (roughly the geographic middle of the US)
LAT1, LAT2 = 29.5, 45.5  # standard parallels

# Everything the frame doesn't need. Alaska and Hawaii would stretch the frame
# across a third of the planet for zero centers; the northern territories do
# the same vertically.
SKIP_STATES = {"Alaska", "Hawaii"}
SKIP_PROVINCES = {"Nunavut", "Northwest Territories", "Yukon"}

# Both thresholds are in FINAL PIXELS, applied after the frame scale is known —
# the projection's own units are ~1.0 for the whole continent, so a tolerance
# expressed in them is meaningless. Rings smaller than this are barrier islands
# and lake specks that cost bytes and read as dirt at this scale.
MIN_RING_AREA_PX = 6.0
SIMPLIFY_TOLERANCE_PX = 0.4


def albers(lon: float, lat: float) -> tuple[float, float]:
    lon_r, lat_r = math.radians(lon), math.radians(lat)
    lat0_r, lon0_r = math.radians(LAT0), math.radians(LON0)
    lat1_r, lat2_r = math.radians(LAT1), math.radians(LAT2)
    n = 0.5 * (math.sin(lat1_r) + math.sin(lat2_r))
    c = math.cos(lat1_r) ** 2 + 2 * n * math.sin(lat1_r)
    rho0 = math.sqrt(c - 2 * n * math.sin(lat0_r)) / n
    rho = math.sqrt(c - 2 * n * math.sin(lat_r)) / n
    theta = n * (lon_r - lon0_r)
    # y is NEGATED: the conic's own y grows northward, and SVG's grows down.
    # Miss this and the whole continent renders upside down — which looks
    # plausible enough at a glance to ship (it did, for one screenshot).
    return rho * math.sin(theta), -(rho0 - rho * math.cos(theta))


def ring_area(points: list[tuple[float, float]]) -> float:
    total = 0.0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def simplify(points: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Douglas-Peucker. Coastlines carry far more detail than a 1000px-wide
    map can show, and every kept vertex is bytes in the bundle."""
    if len(points) < 3:
        return points
    first, last = points[0], points[-1]
    dx, dy = last[0] - first[0], last[1] - first[1]
    span = math.hypot(dx, dy)
    worst_i, worst_d = 0, 0.0
    for i in range(1, len(points) - 1):
        px, py = points[i]
        if span == 0:
            d = math.hypot(px - first[0], py - first[1])
        else:
            d = abs(dy * px - dx * py + last[0] * first[1] - last[1] * first[0]) / span
        if d > worst_d:
            worst_i, worst_d = i, d
    if worst_d <= tol:
        return [first, last]
    left = simplify(points[: worst_i + 1], tol)
    right = simplify(points[worst_i:], tol)
    return left[:-1] + right


def rings_of(geometry: dict) -> list[list[list[float]]]:
    kind = geometry["type"]
    if kind == "Polygon":
        return [geometry["coordinates"][0]]
    if kind == "MultiPolygon":
        return [poly[0] for poly in geometry["coordinates"]]
    return []


def project_rings(geometry: dict) -> list[list[tuple[float, float]]]:
    """Projected rings, full detail. Filtering and simplification wait until
    the pixel scale is known."""
    return [[albers(lon, lat) for lon, lat, *_ in ring] for ring in rings_of(geometry)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin1", required=True)
    ap.add_argument("--countries", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=float, default=1000.0)
    args = ap.parse_args()

    shapes: list[dict] = []
    admin1 = json.loads(Path(args.admin1).read_text())
    for feature in admin1["features"]:
        props = feature["properties"]
        admin, name = props.get("admin"), props.get("name")
        if not name:
            continue
        if admin == "United States of America" and name not in SKIP_STATES:
            kind, code = "state", props.get("postal") or ""
        elif admin == "Canada" and name not in SKIP_PROVINCES:
            kind, code = "province", props.get("postal") or ""
        else:
            continue
        rings = project_rings(feature["geometry"])
        if rings:
            shapes.append({"kind": kind, "name": name, "code": code, "rings": rings})

    countries = json.loads(Path(args.countries).read_text())
    for feature in countries["features"]:
        if feature["properties"].get("NAME") != "Mexico":
            continue
        rings = project_rings(feature["geometry"])
        if rings:
            shapes.append({"kind": "context", "name": "Mexico", "code": "MX", "rings": rings})

    # Frame on the contiguous STATES, then open the top a little so southern
    # Canada shows. Framing on the provinces too would fit Hudson Bay in and
    # shrink the states to nothing, and every Canadian center here is in
    # southern Ontario — further south than Seattle. Mexico and the Canadian
    # north simply run past the viewBox, which is what scenery is for.
    xs, ys = [], []
    for shape in shapes:
        if shape["kind"] != "state":
            continue
        for ring in shape["rings"]:
            xs.extend(p[0] for p in ring)
            ys.extend(p[1] for p in ring)
    min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
    scale = args.width / (max_x - min_x)
    # With y flipped, north is the SMALL end — open the frame there for a
    # strip of Canada above the border.
    min_y -= (max_y - min_y) * 0.1
    height = round((max_y - min_y) * scale, 2)

    def to_svg(rings: list[list[tuple[float, float]]]) -> str:
        parts = []
        for ring in rings:
            pixels = [((x - min_x) * scale, (y - min_y) * scale) for x, y in ring]
            if ring_area(pixels) < MIN_RING_AREA_PX:
                continue
            pts = [(round(x, 1), round(y, 1)) for x, y in simplify(pixels, SIMPLIFY_TOLERANCE_PX)]
            if len(pts) < 3:
                continue
            head = f"M{pts[0][0]} {pts[0][1]}"
            body = "".join(f"L{x} {y}" for x, y in pts[1:])
            parts.append(head + body + "Z")
        return "".join(parts)

    rows = []
    for shape in shapes:
        path = to_svg(shape["rings"])
        if not path:
            continue
        # repr() so the emitted TS is quoted and escaped correctly; the
        # single quotes become double below, matching the repo's style
        rows.append(
            "  {{ kind: {}, name: {}, code: {}, d: {} }},".format(
                repr(shape["kind"]), repr(shape["name"]), repr(shape["code"]), repr(path)
            )
        )
    body = "\n".join(rows).replace("'", '"')

    header = f'''/* GENERATED by backend/scripts/build_map_geo.py — do not hand-edit.
 *
 * Natural Earth boundaries (public domain), projected with an Albers
 * equal-area conic and simplified for a {int(args.width)}px-wide frame. The app never
 * fetches geography at runtime: the deployed CSP allows no external host.
 *
 * `project()` below is the SAME projection, so a center's latitude/longitude
 * lands exactly where its state does.
 */
export const MAP_WIDTH = {int(args.width)};
export const MAP_HEIGHT = {height};

const LAT0 = {LAT0}, LON0 = {LON0}, LAT1 = {LAT1}, LAT2 = {LAT2};
const MIN_X = {min_x!r}, MIN_Y = {min_y!r}, SCALE = {scale!r};

/** Latitude/longitude → viewBox coordinates (Albers equal-area conic). */
export function project(lat: number, lon: number): {{ x: number; y: number }} {{
  const rad = Math.PI / 180;
  const n = 0.5 * (Math.sin(LAT1 * rad) + Math.sin(LAT2 * rad));
  const c = Math.cos(LAT1 * rad) ** 2 + 2 * n * Math.sin(LAT1 * rad);
  const rho0 = Math.sqrt(c - 2 * n * Math.sin(LAT0 * rad)) / n;
  const rho = Math.sqrt(c - 2 * n * Math.sin(lat * rad)) / n;
  const theta = n * (lon - LON0) * rad;
  return {{
    x: (rho * Math.sin(theta) - MIN_X) * SCALE,
    y: (-(rho0 - rho * Math.cos(theta)) - MIN_Y) * SCALE,
  }};
}}

export interface MapShape {{
  /** state and province draw as territory; context is scenery at the edge. */
  kind: "state" | "province" | "context";
  name: string;
  code: string;
  d: string;
}}

export const MAP_SHAPES: MapShape[] = [
'''
    Path(args.out).write_text(header + body + "\n];\n")
    print(f"wrote {args.out}: {len(shapes)} shapes, viewBox {int(args.width)}x{height}")


if __name__ == "__main__":
    main()
