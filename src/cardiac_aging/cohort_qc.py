"""Inspect MSI coverage and sample spectra across the cardiac-aging cohort."""

from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np


def read_pixel_index(path: Path, count: int) -> np.ndarray:
    """Return x, y and the intensity offset for each spectrum, in export order."""
    tag = "{http://psi.hupo.org/ms/mzml}"
    index = np.empty((count, 3), dtype=np.int64)
    seen = 0
    with path.open("rb") as stream:
        for _, spectrum in ET.iterparse(stream, events=("end",)):
            if spectrum.tag != tag + "spectrum":
                continue
            scan = spectrum.find(f"{tag}scanList/{tag}scan")
            position = {p.get("accession"): p.get("value") for p in scan}
            arrays = spectrum.find(tag + "binaryDataArrayList")
            intensity = next(
                array for array in arrays
                if array.find(tag + "referenceableParamGroupRef").get("ref") == "intensities"
            )
            offset = next(
                p.get("value") for p in intensity if p.get("accession") == "IMS:1000102"
            )
            index[seen] = (position["IMS:1000050"], position["IMS:1000051"], offset)
            seen += 1
            spectrum.clear()
    if seen != count:
        raise ValueError(f"{path.name}: found {seen} spectra, expected {count}")
    return index


def inspect_mouse(path: Path, row: dict, seed: int, edge_pixels: int = 3) -> dict:
    """Inspect measured coverage and read a stratified sample of full spectra."""
    if row["size_consistent"] is not True:
        raise ValueError(f"{path.name}: expected a validated continuous export")
    if edge_pixels < 1:
        raise ValueError("edge_pixels must be positive")
    points = row["mz_points"]
    index = read_pixel_index(path, row["spectra"])
    x, y, offsets = index.T
    x, y = x - x.min(), y - y.min()
    mask = np.zeros((y.max() + 1, x.max() + 1), dtype=bool)
    mask[y, x] = True
    if mask.sum() != len(index):
        raise ValueError(f"{path.name}: repeated XY coordinates require a section identifier")

    # Erode measured coverage in all directions, including around holes.
    padded = np.pad(mask, edge_pixels)
    interior = np.logical_and.reduce([
        padded[dy:dy + mask.shape[0], dx:dx + mask.shape[1]]
        for dy in range(2 * edge_pixels + 1)
        for dx in range(2 * edge_pixels + 1)
    ])
    edge = ~interior[y, x]
    rng = np.random.default_rng(seed)
    selected = np.sort(np.concatenate([
        rng.choice(indices, size=min(96, len(indices)), replace=False)
        for indices in (np.flatnonzero(edge), np.flatnonzero(~edge))
    ]))
    sampled = np.empty((len(selected), points), dtype=np.float32)
    with path.with_suffix(".ibd").open("rb") as binary:
        binary.seek(16)
        mz = np.fromfile(binary, dtype="<f8", count=points)
        if len(mz) != points or not np.isfinite(mz).all() or not np.all(np.diff(mz) > 0):
            raise ValueError(f"{path.name}: invalid shared m/z axis")
        for destination, offset in zip(sampled, offsets[selected]):
            binary.seek(int(offset))
            destination[:] = np.fromfile(binary, dtype="<f4", count=points)
    if not np.isfinite(sampled).all():
        raise ValueError(f"{path.name}: sampled intensity contains non-finite values")

    center = 1531.785
    half_width = center * 250e-6
    window = (mz >= center - half_width) & (mz <= center + half_width)
    if not window.any():
        raise ValueError(f"{path.name}: candidate m/z window outside measured range")
    signal = sampled[:, window].sum(axis=1, dtype=np.float64)
    tic = sampled.sum(axis=1, dtype=np.float64)
    sampled_edge = edge[selected]
    return {
        "mask": mask,
        "interior": interior,
        "mz": mz,
        "interior_spectrum": (
            sampled[~sampled_edge].mean(axis=0) if (~sampled_edge).any() else None
        ),
        "x": x[selected],
        "y": y[selected],
        "signal": signal,
        "summary": {
            "measurement_id": row["measurement_id"],
            "animal_id": row["animal_id"],
            "age_group": row["age_group"],
            "measured_pixels": int(mask.sum()),
            "edge_fraction": float(edge.mean()),
            "sampled_edge": int(sampled_edge.sum()),
            "sampled_interior": int((~sampled_edge).sum()),
            "sampled_tic_cv": float(tic.std() / tic.mean()) if tic.mean() > 0 else None,
            "candidate_edge_median": float(np.median(signal[sampled_edge])) if sampled_edge.any() else None,
            "candidate_interior_median": float(np.median(signal[~sampled_edge])) if (~sampled_edge).any() else None,
        },
    }
