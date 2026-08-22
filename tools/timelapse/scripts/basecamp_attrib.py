"""Attribute map objects to base camps.

Palworld stamps ``base_camp_id_belong_to = 00000000`` on every object it does
not consider owned by a camp. That covers world spawns (TreasureBox*,
DamagableRock*) but ALSO player-built pieces standing just outside the camp's
registered ``area_range``. The original pipeline skipped every unowned object
outright, which silently dropped 14 hand-painted pieces of the Glass Tower wall
sign -- they sit at 1.03x the camp's area_range, a few metres past the boundary.

So: an unowned object is adopted by the nearest camp whose padded area_range
contains it, and is otherwise skipped.

RADIUS_FACTOR was picked by measurement against the live save, not by feel:

    x1.00   0 objects adopted   (recovers none of the sign -- the panels are outside)
    x1.05   9 objects adopted   0 world spawns
    x1.10   9 objects adopted   0 world spawns   <-- chosen
    x1.20  12 objects adopted   3 world spawns
    x1.50  18 objects adopted   9 world spawns

1.1 clears the sign panels with margin while admitting no TreasureBox or
DamagableRock at all. area_range is a horizontal radius, so distance is planar.
"""
import math

RADIUS_FACTOR = 1.1
UNOWNED = "00000000"
DEFAULT_AREA_RANGE = 3500.0


def camps_from(wsd):
    """{base8: (x, y, area_range)} for every camp in a decoded worldSaveData."""
    camps = {}
    for entry in wsd["BaseCampSaveData"]["value"]:
        rd = entry["value"]["RawData"]["value"]
        tr = rd["transform"]["translation"]
        camps[str(entry["key"])[:8]] = (tr["x"], tr["y"],
                                        rd.get("area_range", DEFAULT_AREA_RANGE))
    return camps


def attribute(base8, x, y, camps):
    """The camp this object belongs to, or None if it should be skipped.

    Objects that already carry a real base_camp_id keep it untouched; only the
    unowned sentinel is resolved by proximity.
    """
    if base8 != UNOWNED:
        return base8
    best = None
    for camp, (cx, cy, area_range) in camps.items():
        d = math.hypot(x - cx, y - cy)
        if d <= area_range * RADIUS_FACTOR and (best is None or d < best[1]):
            best = (camp, d)
    return best[0] if best else None
