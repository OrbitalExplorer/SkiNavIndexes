# Findings: Ski Resort Index Pipeline

## Key Decisions

### Data Source
- Use **only** `landuse=winter_sports` with `name` tag
- Do NOT use `site=piste` (fragmented geometry)
- Alps bounding box: 5°E to 16°E, 44°N to 48°N

### Hierarchy Detection
- Parent/child via 95% area containment
- Parent = "domain", Child = "resort"

### Bbox Padding Rules
| Size | Area | Padding |
|------|------|---------|
| Small | < 10 km² | +500m |
| Medium | 10-100 km² | +1000m |
| Large | 100-500 km² | +1500m |
| Domain | > 500 km² | +2000-3000m |

### Name Collection
Collect from OSM tags:
- `name` (primary, required)
- `alt_name` (semicolon-separated)
- `name:*` (all language variants)
- `loc_name` and `loc_name:*`
- `short_name` and `short_name:*`

---

## Technical Notes

### Overpass API
- Endpoint: https://overpass-api.de/api/interpreter
- Timeout: 300 seconds
- Output: JSON with geometry (`out tags geom`)

### Geometry Handling
- **Ways (896):** Full `geometry` array with lat/lon coordinates
- **Relations (56):** Only `bounds` available (full `out geom` times out)
- For hierarchy detection: bounds are sufficient for 95% containment
- Normalize script should handle both cases

### Dependencies
- shapely (polygon operations)
- geojson (geometry handling)
- requests (API calls)

### Edge Cases to Handle
- Multi-polygon relations → take convex hull or largest
- Invalid geometries → skip or fix
- Self-intersecting → buffer(0) fix
- Duplicate names → deduplicate
- Missing geometry → skip with warning

---

## Research Links
- Overpass API: https://overpass-api.de/
- OSM Landuse: https://wiki.openstreetmap.org/wiki/Key:landuse
- Shapely: https://shapely.readthedocs.io/
