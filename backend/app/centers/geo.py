"""Where each center is, for the map on the centers page.

The roster has city names, not coordinates — it was built for phone calls, not
cartography. This table supplies them, keyed on the center NAME as the roster
spells it (that is what everything else in the app joins on), with a state
fallback so a center added tomorrow still lands somewhere honest rather than
vanishing from the map.

Coordinates are city centres to about a block. This map is for "who is where
and which zone are they in", not for driving directions, so nothing here needs
to be more precise than the dot that draws it.
"""
from __future__ import annotations

# Center name -> (latitude, longitude). Several roster entries name a region
# rather than a city ("Maryland", "New Jersey"); those get the metro the group
# actually meets in, noted where it isn't obvious.
CENTER_COORDS: dict[str, tuple[float, float]] = {
    # --- Zone 1
    "Boston": (42.3601, -71.0589),
    "Maryland": (39.2904, -76.6122),  # Baltimore/Columbia corridor
    "New Jersey": (40.4862, -74.4518),  # central NJ, around Edison
    # midtown rather than the Battery: at this map's simplification the
    # coastline clips lower Manhattan and the dot lands in the harbour
    "New York": (40.7549, -73.9840),
    # --- Zone 2
    "Atlanta": (33.7490, -84.3880),
    "Austin": (30.2672, -97.7431),
    "Dallas": (32.7767, -96.7970),
    "Houston": (29.7604, -95.3698),
    "San Antonio": (29.4241, -98.4936),
    # --- Zone 3
    "Detroit": (42.3314, -83.0458),
    "Nashville": (36.1627, -86.7816),
    "Washington D.C.": (38.9072, -77.0369),
    # --- Zone 4
    "Orange County": (33.7175, -117.8311),  # Santa Ana
    "SF East Bay (San Ramon)": (37.7799, -121.9780),
    "SF South Bay (Mountain View)": (37.3861, -122.0839),
    "Seattle": (47.6062, -122.3321),
    # --- Canada (all southern Ontario, further south than Seattle)
    "Toronto-Downtown": (43.6532, -79.3832),
    "Toronto-East": (43.7764, -79.2318),  # Scarborough
    "Toronto-North": (43.8361, -79.4287),  # North York / Richmond Hill
    "Toronto-West": (43.5890, -79.6441),  # Mississauga
    "Toronto-KWCG": (43.4516, -80.4925),  # Kitchener-Waterloo-Cambridge-Guelph
    "Toronto-London": (42.9849, -81.2453),  # London, Ontario
    # --- unzoned / dormant
    "Charlotte": (35.2271, -80.8431),
    "Chicago": (41.8781, -87.6298),
    "Cincinnati": (39.1031, -84.5120),
    "Cleveland": (41.4993, -81.6944),
    "Columbus": (39.9612, -83.0007),
    "Connecticut": (41.7658, -72.6734),  # Hartford
    "Dayton": (39.7589, -84.1916),
    "Delaware": (39.7391, -75.5398),  # Wilmington
    "Denver": (39.7392, -104.9903),
    "Indianapolis": (39.7684, -86.1581),
    "Kansas City": (39.0997, -94.5786),
    "Los Angeles": (34.0522, -118.2437),
    "Louisville": (38.2527, -85.7585),
    "Memphis": (35.1495, -90.0490),
    "Milwaukee": (43.0389, -87.9065),
    "Minneapolis": (44.9778, -93.2650),
    "New Mexico": (35.0844, -106.6504),  # Albuquerque
    "Orlando": (28.5383, -81.3792),
    "Ottawa": (45.4215, -75.6972),  # Ontario, despite the roster's country flag
    "Philadelphia": (39.9526, -75.1652),
    "Phoenix": (33.4484, -112.0740),
    "Pittsburgh": (40.4406, -79.9959),
    "Portland": (45.5152, -122.6784),
    "Raleigh": (35.7796, -78.6382),
    "Richmond": (37.5407, -77.4360),
    "Rochester": (43.1566, -77.6088),
    "SF City": (37.7749, -122.4194),
    "Sacramento": (38.5816, -121.4944),
    "Salt Lake City": (40.7608, -111.8910),
    "San Diego": (32.7157, -117.1611),
    "Santa Barbara": (34.4208, -119.6982),
    "Sarasota": (27.3364, -82.5307),
    # a mile west of the arch — downtown sits ON the Mississippi, which is the
    # state line, and the simplified border puts the true point in Illinois
    "St. Louis": (38.6350, -90.2450),
    "Tampa Bay": (27.9506, -82.4572),
    "West Palm Beach": (26.7153, -80.0534),
}

# The campus itself: every III Departments "center" is a department at the Isha
# Institute of Inner Sciences in McMinnville, Tennessee. They share one point,
# which is the truth — the map groups them rather than pretending otherwise.
III_CAMPUS = (35.6837, -85.7794)

# Last resort so an unlisted center still appears: the middle of its state.
STATE_COORDS: dict[str, tuple[float, float]] = {
    "Alabama": (32.8, -86.8), "Arizona": (34.3, -111.7), "Arkansas": (34.9, -92.4),
    "California": (37.2, -119.5), "Colorado": (39.0, -105.5), "Connecticut": (41.6, -72.7),
    "Delaware": (39.0, -75.5), "Florida": (28.6, -82.4), "Georgia": (32.6, -83.4),
    "Idaho": (44.4, -114.6), "Illinois": (40.0, -89.2), "Indiana": (39.9, -86.3),
    "Iowa": (42.1, -93.5), "Kansas": (38.5, -98.4), "Kentucky": (37.5, -85.3),
    "Louisiana": (31.1, -92.0), "Maine": (45.4, -69.2), "Maryland": (39.0, -76.8),
    "Massachusetts": (42.3, -71.8), "Michigan": (44.3, -85.4), "Minnesota": (46.3, -94.3),
    "Mississippi": (32.7, -89.7), "Missouri": (38.4, -92.5), "Montana": (47.0, -109.6),
    "Nebraska": (41.5, -99.8), "Nevada": (39.3, -116.6), "New Hampshire": (43.7, -71.6),
    "New Jersey": (40.2, -74.7), "New Mexico": (34.4, -106.1), "New York": (42.9, -75.5),
    "North Carolina": (35.5, -79.4), "North Dakota": (47.4, -100.5), "Ohio": (40.3, -82.8),
    "Oklahoma": (35.6, -97.5), "Oregon": (43.9, -120.6), "Pennsylvania": (40.9, -77.8),
    "Rhode Island": (41.7, -71.6), "South Carolina": (33.9, -80.9),
    "South Dakota": (44.4, -100.2), "Tennessee": (35.8, -86.4), "Texas": (31.5, -99.3),
    "Utah": (39.3, -111.7), "Vermont": (44.1, -72.7), "Virginia": (37.5, -78.8),
    "Washington": (47.4, -120.4), "Washington DC": (38.9, -77.0),
    "West Virginia": (38.6, -80.6), "Wisconsin": (44.6, -89.7), "Wyoming": (43.0, -107.6),
    "Ontario": (44.0, -79.5), "Quebec": (46.8, -71.2), "British Columbia": (49.2, -123.1),
    "Alberta": (51.0, -114.1),
}


def coordinates_for(name: str, state: str, zone_kind: str = "") -> tuple[float, float] | None:
    """(lat, lon) for a center, or None when there is nothing honest to say.

    Departments resolve to the campus by their ZONE, not their name — the
    roster calls them "Kitchen" and "Front Office", which no gazetteer will
    ever have, and they are all in the same building anyway.
    """
    if zone_kind == "departments":
        return III_CAMPUS
    exact = CENTER_COORDS.get(name.strip())
    if exact is not None:
        return exact
    return STATE_COORDS.get(state.strip())
