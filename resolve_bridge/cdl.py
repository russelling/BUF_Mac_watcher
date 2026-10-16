"""
cdl.py — ASC CDL (.cc / .ccc) parsing and per-shot lookup.

Resolve's scripting API has no file-based CDL import: `TimelineItem.SetCDL()`
only takes slope/offset/power/saturation as strings, so a .cc/.ccc on disk
has to be parsed here first, not just handed to Resolve by path.

The shot-lookup half deliberately mirrors find_shot_cdl() in
../scripts/qt_bake_oiio.py (same {shot}_{layer}_v{version} naming, same
highest-version-wins tie-break, same everything-found-gets-logged
philosophy) rather than importing it, for the same reason
../drop_app/preview.py keeps its own shot_cdl_path() mirror instead of
importing: this also has to run from a bare checkout of this folder with no
guarantee ../scripts is on sys.path. It ADDS .ccc (ColorCorrectionCollection
- multiple graded shots/looks in one file, selected by id) on top of the
existing .cc handling, so treat the two lookups as one convention with two
extensions, not as diverging logic - if the naming convention changes again,
update both this and qt_bake_oiio.find_shot_cdl.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

CDL_EXTENSIONS = (".cc", ".ccc")

_SHOT_CDL_RE_TEMPLATE = r"^%s_(?P<layer>[^_]+)_v(?P<version>\d+)\.(?P<ext>cc|ccc)$"


@dataclass(frozen=True)
class CDLValues:
    """One ASC CDL correction: SOP (slope/offset/power) + saturation."""

    slope: tuple = (1.0, 1.0, 1.0)
    offset: tuple = (0.0, 0.0, 0.0)
    power: tuple = (1.0, 1.0, 1.0)
    saturation: float = 1.0
    cc_id: str = ""
    source_path: str = ""

    def is_identity(self) -> bool:
        return (
            self.slope == (1.0, 1.0, 1.0)
            and self.offset == (0.0, 0.0, 0.0)
            and self.power == (1.0, 1.0, 1.0)
            and abs(self.saturation - 1.0) < 1e-9
        )

    def to_resolve_cdl_map(self, node_index: int) -> dict:
        """Shape SetCDL() expects: space-separated triplets, string values."""
        return {
            "NodeIndex": str(node_index),
            "Slope": "%.6g %.6g %.6g" % self.slope,
            "Offset": "%.6g %.6g %.6g" % self.offset,
            "Power": "%.6g %.6g %.6g" % self.power,
            "Saturation": "%.6g" % self.saturation,
        }


def _triplet(node) -> Optional[tuple]:
    if node is None or not (node.text or "").strip():
        return None
    parts = node.text.strip().split()
    if len(parts) != 3:
        return None
    try:
        return tuple(float(p) for p in parts)
    except ValueError:
        return None


def _parse_color_correction(cc_node) -> CDLValues:
    sop = cc_node.find("SOPNode")
    sat_node = cc_node.find("SATNode")

    slope = (1.0, 1.0, 1.0)
    offset = (0.0, 0.0, 0.0)
    power = (1.0, 1.0, 1.0)
    saturation = 1.0

    if sop is not None:
        slope = _triplet(sop.find("Slope")) or slope
        offset = _triplet(sop.find("Offset")) or offset
        power = _triplet(sop.find("Power")) or power
    if sat_node is not None:
        sat_text = sat_node.find("Saturation")
        if sat_text is not None and (sat_text.text or "").strip():
            try:
                saturation = float(sat_text.text.strip())
            except ValueError:
                pass

    return CDLValues(
        slope=slope,
        offset=offset,
        power=power,
        saturation=saturation,
        cc_id=cc_node.get("id", ""),
    )


def parse_cdl(path: str, cc_id: Optional[str] = None) -> CDLValues:
    """
    Parse a .cc (single ColorCorrection) or .ccc (ColorCorrectionCollection).

    For a .ccc with more than one <ColorCorrection>, cc_id selects by the
    `id` attribute; without one, the first entry in the file is used (a .ccc
    holding a single shot's grade — one entry — is by far the common case
    for a per-shot lookup, so this stays a fallback rather than an error).

    Raises ValueError on anything that isn't valid ASC CDL XML, rather than
    returning a silent identity grade — a shot whose CDL fails to parse
    should never quietly bake ungraded.
    """
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise ValueError("not valid CDL XML: %s (%s)" % (path, exc))

    root = tree.getroot()
    root_tag = root.tag.rsplit("}", 1)[-1]  # strip any XML namespace

    if root_tag == "ColorCorrection":
        cc_nodes = [root]
    elif root_tag == "ColorCorrectionCollection":
        cc_nodes = [
            child for child in root
            if child.tag.rsplit("}", 1)[-1] == "ColorCorrection"
        ]
    else:
        raise ValueError(
            "unrecognised CDL root element <%s> in %s (expected "
            "ColorCorrection or ColorCorrectionCollection)" % (root_tag, path)
        )

    if not cc_nodes:
        raise ValueError("no <ColorCorrection> entries in %s" % path)

    chosen = cc_nodes[0]
    if cc_id:
        for node in cc_nodes:
            if node.get("id") == cc_id:
                chosen = node
                break
        else:
            raise ValueError(
                "no <ColorCorrection id=\"%s\"> in %s (found: %s)"
                % (cc_id, path, [n.get("id") for n in cc_nodes])
            )

    values = _parse_color_correction(chosen)
    return CDLValues(
        slope=values.slope,
        offset=values.offset,
        power=values.power,
        saturation=values.saturation,
        cc_id=values.cc_id,
        source_path=path,
    )


def find_shot_cdl(plates_dir: Optional[str], shot_code: str) -> Optional[str]:
    """
    Locate a shot's CDL file (.cc or .ccc) under its plates/ folder.

    Matches {shot_code}_{layer}_v{version}.(cc|ccc), highest version wins
    when more than one candidate matches (logged, never silent) — see the
    module docstring for why this exists alongside, rather than instead of,
    find_shot_cdl() in ../scripts/qt_bake_oiio.py. Falls back to legacy bare
    {shot_code}.cc / {shot_code}.ccc for anything predating the versioned
    naming.
    """
    if not plates_dir or not os.path.isdir(plates_dir) or not shot_code:
        return None

    pattern = re.compile(_SHOT_CDL_RE_TEMPLATE % re.escape(shot_code))
    try:
        names = os.listdir(plates_dir)
    except OSError as exc:
        print("[resolve_bridge.cdl] could not read %s: %s" % (plates_dir, exc))
        return None

    candidates = []
    for fname in names:
        m = pattern.match(fname)
        if m:
            # .cc ranks above .ccc at the same version (an unambiguous
            # single-shot grade beats picking an entry out of a collection).
            ext_rank = 0 if m.group("ext") == "cc" else -1
            candidates.append((int(m.group("version")), ext_rank, fname))

    if candidates:
        candidates.sort()
        chosen = candidates[-1][2]
        if len(candidates) > 1:
            print(
                "[resolve_bridge.cdl] multiple CDL candidates for %s: %s "
                "— using highest version: %s"
                % (shot_code, [c[2] for c in candidates], chosen)
            )
        return os.path.join(plates_dir, chosen)

    for ext in CDL_EXTENSIONS:
        legacy = os.path.join(plates_dir, "%s%s" % (shot_code, ext))
        if os.path.exists(legacy):
            return legacy

    return None
