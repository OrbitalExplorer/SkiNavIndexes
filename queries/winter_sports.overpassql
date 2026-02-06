[out:json][timeout:300];

// Alps bounding box (South, West, North, East)
// Covers: Austria, Switzerland, Italy, France, Southern Germany
// Latitude: 44°N to 48°N
// Longitude: 5°E to 16°E

(
  way["landuse"="winter_sports"]["name"](44,5,48,16);
  relation["landuse"="winter_sports"]["name"](44,5,48,16);
);

// Output tags and geometry for ways, bounds for relations
out tags geom;
