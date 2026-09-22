#!/usr/bin/env python3
"""
EDGES-3 Date/File Scanner
=========================

Scans the raw data directory and returns:
  * ``calibration``: list of ``YYYY_DDD`` dates that have amb/hot/open/short .acq
  * ``s11``:         list of ``YYYY_DDD_RUN`` stems with O/S/L/ant/lna .s1p
  * ``raw``:         list of ``YYYY_DDD_HH_MM_SS`` timestamps for antenna .acq

The same code is used:
  * As a CLI tool (``python scan_dates.py [--output_file FOO.json]``)
  * As a library imported by ``backend_api.py`` and ``daemon.py``
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set

# Allow ``python scan_dates.py`` to import config.py from the same directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402


CAL_LOADS = ("amb", "hot", "open", "short")
S11_SUFFIXES = ("O", "S", "L", "ant", "lna")


def scan_calibration_dates(root: Path) -> List[str]:
    """Return ``YYYY_DDD`` dates that have all four loads (amb/hot/open/short)."""
    sets: Dict[str, Set[str]] = {load: set() for load in CAL_LOADS}

    for load in CAL_LOADS:
        load_dir = root / "mro" / load
        if not load_dir.exists():
            continue
        pattern = re.compile(rf"(\d{{4}}_\d{{3}})_\d{{2}}_\d{{2}}_\d{{2}}_{load}\.acq")
        for f in load_dir.rglob(f"*_{load}.acq"):
            m = pattern.fullmatch(f.name)
            if m:
                sets[load].add(m.group(1))

    if not all(sets.values()):
        return []
    return sorted(set.intersection(*sets.values()))


def scan_s11_stems(root: Path) -> List[str]:
    """Return ``YYYY_DDD_RUN`` stems that have all five .s1p files."""
    stems: Set[str] = set()
    pattern = re.compile(r"(\d{4}_\d{3}_\d+)_O\.s1p")
    for f in root.glob("*_O.s1p"):
        m = pattern.fullmatch(f.name)
        if not m:
            continue
        stem = m.group(1)
        if all((root / f"{stem}_{suffix}.s1p").exists() for suffix in S11_SUFFIXES):
            stems.add(stem)
    return sorted(stems)


def scan_raw_timestamps(root: Path) -> List[str]:
    """Return ``YYYY_DDD_HH_MM_SS`` timestamps for antenna .acq files."""
    timestamps: List[str] = []
    pattern = re.compile(r"(\d{4}_\d{3}_\d{2}_\d{2}_\d{2})_ant\.acq")
    ant_dir = root / "mro" / "ant"
    if not ant_dir.exists():
        return timestamps
    for f in ant_dir.rglob("*_ant.acq"):
        m = pattern.fullmatch(f.name)
        if m:
            timestamps.append(m.group(1))
    return sorted(timestamps)


def scan_all(root: Path) -> Dict[str, List[str]]:
    """Return the full {calibration, s11, raw} dict for ``root``."""
    return {
        "calibration": scan_calibration_dates(root),
        "s11": scan_s11_stems(root),
        "raw": scan_raw_timestamps(root),
    }


def latest(d: Dict[str, List[str]]) -> Dict[str, str]:
    """Resolve "Latest" for every category."""
    return {k: (v[-1] if v else "") for k, v in d.items()}


def write_results(results: Dict[str, List[str]], output_file: Path) -> Path:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **results,
        "scanned_at": datetime.now().isoformat(),
    }
    with open(output_file, "w") as f:
        json.dump(payload, f, indent=2)
    return output_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EDGES-3 date scanner")
    parser.add_argument(
        "--rawdata_root",
        type=str,
        default=str(config.RAW_DATA_ROOT),
        help="Root of the raw MRO data tree",
    )
    parser.add_argument(
        "--output_file",
        type=str,
        default=str(config.AVAILABLE_DATES_FILE),
        help="Where to write the JSON results",
    )
    args = parser.parse_args(argv)

    root = Path(args.rawdata_root).expanduser().resolve()
    if not root.exists():
        print(f"[scan_dates] WARNING: root does not exist: {root}", file=sys.stderr)

    results = scan_all(root)
    write_results(results, Path(args.output_file).expanduser().resolve())

    print(f"[scan_dates] Wrote {args.output_file}")
    print(f"[scan_dates]   calibration: {len(results['calibration'])}")
    print(f"[scan_dates]   s11:         {len(results['s11'])}")
    print(f"[scan_dates]   raw:         {len(results['raw'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
