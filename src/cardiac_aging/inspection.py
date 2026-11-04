"""Inspect imzML headers without loading the large binary intensity matrices."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def load_config(root: Path) -> dict:
    path = root / "config.local.json"
    if not path.exists():
        path = root / "config.example.json"
    return json.loads(path.read_text(encoding="utf-8"))


def inspect_header(path: Path) -> dict:
    """Read metadata through the first spectrum. Does not scan every pixel.

    Binary size consistency is checked only for continuous, uncompressed,
    fixed-length float32 intensities with a shared float64 m/z axis.
    This is a structural check, not a checksum or full integrity validation.
    """
    params: dict[str, str] = {}
    source = ""
    spectrum_count = 0
    first = None
    # Passing an owned file ensures early exit closes the file on Windows.
    with path.open("rb") as stream:
        for event, element in ET.iterparse(stream, events=("start", "end")):
            tag = element.tag.rsplit("}", 1)[-1]
            if event == "start":
                if tag == "cvParam":
                    params.setdefault(element.attrib["name"], element.get("value", ""))
                elif tag == "sourceFile":
                    source = element.get("name", "")
                elif tag == "spectrumList":
                    spectrum_count = int(element.attrib["count"])
            elif tag == "spectrum":
                first = element
                break
    if first is None:
        raise ValueError(f"No spectra found in {path.name}")

    arrays = {}
    for element in first.iter():
        if element.tag.rsplit("}", 1)[-1] != "binaryDataArray":
            continue
        ref = next((x.get("ref") for x in element if x.tag.endswith("referenceableParamGroupRef")), None)
        values = {x.get("name"): x.get("value", "") for x in element if x.tag.endswith("cvParam")}
        arrays[ref] = values
    mz, intensity = arrays.get("mzArray", {}), arrays.get("intensities", {})
    ibd = path.with_suffix(".ibd")
    size = ibd.stat().st_size if ibd.exists() else None
    points = int(mz["external array length"]) if mz else None
    expected = None
    if ("continuous" in params and "no compression" in params
            and "64-bit float" in params and "32-bit float" in params
            and mz and intensity
            and int(mz["external offset"]) == 16
            and int(mz["external encoded length"]) == points * 8
            and int(intensity["external offset"]) == 16 + points * 8
            and int(intensity["external array length"]) == points
            and int(intensity["external encoded length"]) == points * 4):
        expected = 16 + points * 8 + spectrum_count * points * 4
    return {
        "filename": path.name,
        "ibd_exists": ibd.exists(),
        "spectra": spectrum_count,
        "mz_points": points,
        "pixel_x": params.get("pixel size x"),
        "pixel_y": params.get("pixel size y"),
        "mz_min": params.get("lowest observed m/z"),
        "mz_max": params.get("highest observed m/z"),
        "profile": "profile spectrum" in params,
        "baseline_reduction_recorded": "baseline reduction" in params,
        "source_project": source,
        "ibd_bytes": size,
        "expected_ibd_bytes": expected,
        "size_consistent": (size == expected) if expected is not None and size is not None else None,
    }


def inventory(data_dir: Path, sample_sheet: Path) -> list[dict]:
    with sample_sheet.open(encoding="utf-8-sig", newline="") as handle:
        samples = list(csv.DictReader(handle))
    ids = [s["measurement_id"] for s in samples]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate measurement IDs in sample sheet")
    rows = []
    for sample in samples:
        path = data_dir / sample["imzml_filename"]
        if not path.exists():
            raise FileNotFoundError(path)
        rows.append({**sample, **inspect_header(path)})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    config = load_config(root)
    rows = inventory(Path(config["data_dir"]), root / config["sample_sheet"])
    output = root / config["results_dir"] / "inventory.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"Inspected {len(rows)} samples. Inventory saved to {output}")
    for row in rows:
        print(f"{row['measurement_id']}: {row['age_group']}, "
              f"{row['spectra']:,} spectra, size consistent={row['size_consistent']}")


if __name__ == "__main__":
    main()
