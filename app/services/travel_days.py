"""
Japan city clusters and travel-day bands.

Geography is decided here, not by the model.
Tokyo → Hokkaido → Okinawa is far. Kyoto ↔ Nara is local.
"""

from __future__ import annotations

from dataclasses import dataclass

# cluster name → city tokens (matched as substring of the overnight city)
CLUSTERS: dict[str, tuple[str, ...]] = {
    "kanto": (
        "tokyo", "yokohama", "kamakura", "hakone", "nikko", "chiba",
        "shinjuku", "shibuya", "asakusa", "ginza", "ueno", "odaiba",
    ),
    "kansai": ("osaka", "kyoto", "nara", "kobe", "uji", "himeji"),
    "chubu": ("takayama", "kanazawa", "nagoya", "matsumoto", "shirakawa"),
    "hokkaido": (
        "sapporo", "otaru", "niseko", "furano", "hakodate", "hokkaido",
        "noboribetsu", "kutchan",
    ),
    "okinawa": ("naha", "nago", "ishigaki", "okinawa", "naha-shi"),
    "kyushu": ("fukuoka", "nagasaki", "kagoshima", "beppu", "kumamoto"),
}

# Adjacent clusters: Shinkansen-scale, half day only
NEAR_CLUSTER_PAIRS: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"kanto", "kansai"}),
        frozenset({"kanto", "chubu"}),
        frozenset({"kansai", "chubu"}),
    }
)

LOCAL_MAX_MINUTES = 480
NEAR_MAX_MINUTES = 180
FAR_SHORT_MAX_MINUTES = 90

PACE_WARNING = (
    "These overnight changes are travel days. A full-day tour is too much to ask."
)


@dataclass(frozen=True)
class TravelBand:
    name: str  # local | near | far
    day_kind: str  # stay | travel_near | travel_far
    max_tour_minutes: int


def normalize_city(city: str) -> str:
    return (city or "").strip().casefold()


def cluster_for_city(city: str) -> str | None:
    token = normalize_city(city)
    if not token:
        return None
    for cluster, names in CLUSTERS.items():
        if any(name in token or token in name for name in names):
            return cluster
    return None


def band_for_hop(from_city: str, to_city: str) -> TravelBand:
    """Band for leaving from_city overnight toward to_city the next night."""
    if normalize_city(from_city) == normalize_city(to_city):
        return TravelBand("local", "stay", LOCAL_MAX_MINUTES)

    a = cluster_for_city(from_city)
    b = cluster_for_city(to_city)

    if a and b and a == b:
        return TravelBand("local", "stay", LOCAL_MAX_MINUTES)

    if a and b and frozenset({a, b}) in NEAR_CLUSTER_PAIRS:
        return TravelBand("near", "travel_near", NEAR_MAX_MINUTES)

    # Unknown cities on a name change: treat as far so we never invent a full-day tour
    return TravelBand("far", "travel_far", 0)


def is_local_pair(city_a: str, city_b: str) -> bool:
    return band_for_hop(city_a, city_b).name == "local"
