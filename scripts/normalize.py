#!/usr/bin/env python3
"""Normalize Overpass API output into ski resort index format."""

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shapely.geometry import Point, Polygon, box, shape
from shapely.prepared import prep
from shapely.validation import make_valid


def collect_name_variants(tags: dict[str, Any]) -> list[str]:
    """Collect all name variants from OSM tags."""
    names = []

    if "name" in tags:
        names.append(tags["name"])

    if "alt_name" in tags:
        alt_names = [n.strip() for n in tags["alt_name"].split(";") if n.strip()]
        names.extend(alt_names)

    for key, value in tags.items():
        if key.startswith("name:") and value:
            names.append(value)

    if "loc_name" in tags:
        names.append(tags["loc_name"])
    for key, value in tags.items():
        if key.startswith("loc_name:") and value:
            names.append(value)

    if "short_name" in tags:
        names.append(tags["short_name"])
    for key, value in tags.items():
        if key.startswith("short_name:") and value:
            names.append(value)

    seen = set()
    deduplicated = []
    for name in names:
        name = name.strip()
        if name and name not in seen and len(name) <= 200:
            seen.add(name)
            deduplicated.append(name)

    return deduplicated


def geometry_to_polygon(geometry: list[dict]) -> Polygon | None:
    """Convert Overpass geometry array to Shapely Polygon."""
    if not geometry or len(geometry) < 3:
        return None

    coords = [(p["lon"], p["lat"]) for p in geometry]
    if coords[0] != coords[-1]:
        coords.append(coords[0])

    try:
        poly = Polygon(coords)
        if not poly.is_valid:
            fixed = make_valid(poly)
            if hasattr(fixed, "exterior"):
                poly = Polygon(fixed.exterior.coords)  # type: ignore[union-attr]
            else:
                return None
        if poly.is_empty:
            return None
        return poly
    except Exception:
        return None


def bounds_to_polygon(bounds: dict) -> Polygon:
    """Convert Overpass bounds to Shapely Polygon (box)."""
    return box(
        bounds["minlon"],
        bounds["minlat"],
        bounds["maxlon"],
        bounds["maxlat"],
    )


def calculate_area_km2(polygon: Polygon) -> float:
    """Calculate approximate area in km² using Haversine-based method."""
    bounds = polygon.bounds
    center_lat = (bounds[1] + bounds[3]) / 2

    lat_km = 111.0
    lon_km = 111.0 * math.cos(math.radians(center_lat))

    minx, miny, maxx, maxy = bounds
    width_km = (maxx - minx) * lon_km
    height_km = (maxy - miny) * lat_km

    bbox_area = width_km * height_km
    poly_ratio = (
        polygon.area / ((maxx - minx) * (maxy - miny))
        if (maxx - minx) * (maxy - miny) > 0
        else 1
    )

    return round(bbox_area * poly_ratio, 2)


def get_padding_meters(area_km2: float) -> float:
    """Get bbox padding based on size category."""
    if area_km2 < 10:
        return 500
    elif area_km2 < 100:
        return 1000
    elif area_km2 < 500:
        return 1500
    else:
        return 2500


def apply_padding(
    bounds: tuple[float, float, float, float], padding_m: float, center_lat: float
) -> list[float]:
    """Apply padding to bounding box in degrees."""
    lat_deg = padding_m / 111000
    lon_deg = padding_m / (111000 * math.cos(math.radians(center_lat)))

    west, south, east, north = bounds
    return [
        round(west - lon_deg, 6),
        round(south - lat_deg, 6),
        round(east + lon_deg, 6),
        round(north + lat_deg, 6),
    ]


def get_country_code(
    lat: float, lon: float, country_index: list[dict[str, Any]] | None
) -> str | None:
    """Get ISO country code from coordinates using polygon containment."""
    if not country_index:
        return None

    point = Point(lon, lat)

    for entry in country_index:
        if entry["prepared"].contains(point) or entry["prepared"].covers(point):
            return entry["iso_a2"]

    nearest_code = None
    nearest_distance = float("inf")
    for entry in country_index:
        distance = entry["geometry"].distance(point)
        if distance < nearest_distance:
            nearest_distance = distance
            nearest_code = entry["iso_a2"]

    if nearest_distance <= 0.02:
        return nearest_code

    return None


def build_country_index() -> list[dict[str, Any]] | None:
    """Build country polygon index from bundled Natural Earth boundaries."""
    data_path = Path(__file__).parent.parent / "data" / "alps_countries.geojson"
    if not data_path.exists():
        print(f"Warning: Country boundary file not found: {data_path}", file=sys.stderr)
        return None

    try:
        with open(data_path) as f:
            geojson = json.load(f)

        index = []
        iso_a3_to_a2 = {
            "FRA": "FR",
            "DEU": "DE",
            "AUT": "AT",
            "ITA": "IT",
            "CHE": "CH",
            "SVN": "SI",
            "LIE": "LI",
            "HRV": "HR",
            "DE": "DE",
            "AT": "AT",
            "IT": "IT",
            "CH": "CH",
            "SI": "SI",
            "LI": "LI",
            "HR": "HR",
        }

        for feature in geojson.get("features", []):
            props = feature.get("properties", {})
            iso_a2 = props.get("ISO_A2")
            if not iso_a2 or iso_a2 == "-99":
                iso_a2 = iso_a3_to_a2.get(props.get("ADM0_A3"), None)
            geometry_data = feature.get("geometry")
            if not iso_a2 or not geometry_data:
                continue

            geometry = shape(geometry_data)
            if not geometry.is_valid:
                geometry = make_valid(geometry)
            if geometry.is_empty:
                continue

            index.append(
                {
                    "iso_a2": iso_a2,
                    "geometry": geometry,
                    "prepared": prep(geometry),
                }
            )

        if not index:
            print("Warning: No country polygons loaded", file=sys.stderr)
            return None

        return index
    except Exception as exc:
        print(f"Warning: Could not build country index: {exc}", file=sys.stderr)
        return None


def compute_hierarchy(resorts: list[dict]) -> list[dict]:
    """Detect parent/child relationships using 95% area containment."""
    for resort in resorts:
        resort["parent_id"] = None
        resort["parent_name"] = None
        resort["type"] = "resort"

    sorted_resorts = sorted(resorts, key=lambda r: r["area_km2"], reverse=True)

    for i, child in enumerate(sorted_resorts):
        child_poly = child["_polygon"]
        child_area = child_poly.area

        for parent in sorted_resorts[:i]:
            if parent["area_km2"] <= child["area_km2"]:
                continue

            parent_poly = parent["_polygon"]
            try:
                intersection = parent_poly.intersection(child_poly)
                if intersection.area >= 0.95 * child_area:
                    child["parent_id"] = parent["id"]
                    child["parent_name"] = parent["name"]
                    parent["type"] = "domain"
                    break
            except Exception:
                continue

    return resorts


def parse_overpass_output(
    data: dict, country_index: list[dict[str, Any]] | None
) -> list[dict]:
    """Parse Overpass JSON into resort objects."""
    resorts = []

    for element in data.get("elements", []):
        tags = element.get("tags", {})
        name = tags.get("name")
        if not name:
            continue

        if element["type"] == "way" and "geometry" in element:
            polygon = geometry_to_polygon(element["geometry"])
        elif "bounds" in element:
            polygon = bounds_to_polygon(element["bounds"])
        else:
            continue

        if polygon is None or polygon.is_empty:
            continue

        area_km2 = calculate_area_km2(polygon)
        bounds = polygon.bounds
        center_lat = polygon.centroid.y
        center_lon = polygon.centroid.x

        padding = get_padding_meters(area_km2)
        bbox_center_lat = (bounds[1] + bounds[3]) / 2
        bbox = apply_padding(bounds, padding, bbox_center_lat)

        names = collect_name_variants(tags)
        country = get_country_code(center_lat, center_lon, country_index)

        resorts.append(
            {
                "id": element["id"],
                "name": name,
                "names": names,
                "type": "resort",
                "parent_id": None,
                "parent_name": None,
                "bbox": bbox,
                "area_km2": area_km2,
                "country": country,
                "_polygon": polygon,
            }
        )

    return resorts


def normalize(input_path: str, output_path: str) -> dict:
    """Main normalization function."""
    with open(input_path) as f:
        data = json.load(f)

    print(
        f"Loaded {len(data.get('elements', []))} elements from Overpass",
        file=sys.stderr,
    )

    print("Building country index...", file=sys.stderr)
    country_index = build_country_index()

    print("Parsing elements...", file=sys.stderr)
    resorts = parse_overpass_output(data, country_index)
    print(f"Parsed {len(resorts)} valid resorts", file=sys.stderr)

    print("Computing hierarchy...", file=sys.stderr)
    resorts = compute_hierarchy(resorts)

    domains = sum(1 for r in resorts if r["type"] == "domain")
    with_parent = sum(1 for r in resorts if r["parent_id"] is not None)
    print(f"Found {domains} domains, {with_parent} child resorts", file=sys.stderr)

    for resort in resorts:
        del resort["_polygon"]

    output = {
        "version": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "total_resorts": len(resorts),
        "regions": ["alps"],
        "resorts": resorts,
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(resorts)} resorts to {output_path}", file=sys.stderr)
    return output


def main():
    parser = argparse.ArgumentParser(
        description="Normalize Overpass output to resort index"
    )
    parser.add_argument("input", help="Input JSON file from Overpass API")
    parser.add_argument("output", help="Output resorts.json file")
    args = parser.parse_args()

    normalize(args.input, args.output)


if __name__ == "__main__":
    main()
