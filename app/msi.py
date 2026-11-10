"""Local MSI ingestion, window ranking, and tissue targeting."""
from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import uuid
import xml.etree.ElementTree as ET

import numpy as np


@dataclass(frozen=True)
class Layout:
    xml: Path
    binary: Path
    count: int
    points: int
    mz_offset: int
    intensity_offset: int
    mz_dtype: str
    intensity_dtype: str

    def mass_axis(self):
        with self.binary.open("rb") as handle:
            handle.seek(self.mz_offset)
            mz = np.fromfile(handle, dtype=self.mz_dtype, count=self.points).astype(float)
        if len(mz) != self.points or not np.isfinite(mz).all() or np.any(np.diff(mz) <= 0):
            raise ValueError(f"{self.xml.name} has an invalid shared mass axis.")
        return mz

    def profile(self):
        return np.memmap(self.binary, mode="r", dtype=self.intensity_dtype,
                         offset=self.intensity_offset, shape=(self.count, self.points))

    def fingerprint(self):
        return [str(self.xml), self.xml.stat().st_size, self.xml.stat().st_mtime_ns,
                str(self.binary), self.binary.stat().st_size, self.binary.stat().st_mtime_ns]


@dataclass
class Sample:
    id: str
    animal: str
    group: str
    section: str
    layout: Layout
    mz: np.ndarray
    mean: np.ndarray
    xy: np.ndarray
    cache: str

    def public(self):
        return {"id": self.id, "animal": self.animal, "group": self.group,
                "section": self.section, "pixels": self.layout.count, "cache": self.cache}


def tag(element):
    return element.tag.rsplit("}", 1)[-1]


def cv(element):
    return {item.get("accession"): item.get("value") for item in element if tag(item) == "cvParam"}


def binary_arrays(spectrum, groups):
    arrays = {}
    for element in spectrum.findall("./{*}binaryDataArrayList/{*}binaryDataArray"):
        params = {}
        for ref in element.findall("./{*}referenceableParamGroupRef"):
            params.update(groups.get(ref.get("ref"), {}))
        params.update(cv(element))
        for kind in ("MS:1000514", "MS:1000515"):
            if kind in params:
                arrays[kind] = params
    return arrays["MS:1000514"], arrays["MS:1000515"]


def inspect(path):
    binary = next((p for p in (path.with_suffix(".ibd"), path.with_suffix(".IBD")) if p.is_file()), None)
    if binary is None:
        raise ValueError(f"Missing .ibd pair for {path.name}.")
    groups, count, continuous, file_uuid = {}, 0, False, None
    with path.open("rb") as handle:
        for event, element in ET.iterparse(handle, events=("start", "end")):
            name = tag(element)
            if event == "start" and name == "spectrumList":
                count = int(element.attrib["count"])
            if event == "end" and name == "fileContent":
                params = cv(element)
                continuous = "IMS:1000030" in params
                file_uuid = params.get("IMS:1000080")
            if event == "end" and name == "referenceableParamGroup":
                groups[element.attrib["id"]] = cv(element)
            if event == "end" and name == "spectrum":
                first = element
                break
        else:
            raise ValueError(f"No spectra found in {path.name}.")

    def array(params):
        if "MS:1000576" not in params:
            raise ValueError(f"{path.name} uses compressed or unspecified binary encoding.")
        dtype = next((code for key, code in (("MS:1000521", "<f4"), ("MS:1000523", "<f8"))
                      if key in params), None)
        if dtype is None:
            raise ValueError(f"{path.name} requires 32-bit or 64-bit floating point arrays.")
        length = int(params["IMS:1000103"])
        offset = int(params["IMS:1000102"])
        if offset < 16 or int(params["IMS:1000104"]) != length * np.dtype(dtype).itemsize:
            raise ValueError(f"Invalid external array layout in {path.name}.")
        return length, offset, dtype

    mz, signal = (array(p) for p in binary_arrays(first, groups))
    if (not continuous or count < 1 or mz[0] < 2 or signal[0] != mz[0]
            or signal[1] < mz[1] + mz[0] * np.dtype(mz[2]).itemsize
            or signal[1] + count * mz[0] * np.dtype(signal[2]).itemsize != binary.stat().st_size):
        raise ValueError(f"{path.name} requires a continuous export with a shared axis and contiguous spectra.")
    if file_uuid:
        with binary.open("rb") as handle:
            if handle.read(16) != uuid.UUID(file_uuid).bytes:
                raise ValueError(f"The imzML and ibd UUIDs do not match for {path.name}.")
    return Layout(path, binary, count, mz[0], mz[1], signal[1], mz[2], signal[2])


def read_coordinates(layout):
    coordinates = np.empty((layout.count, 2), dtype=np.int64)
    groups, rows = {}, 0
    stride = layout.points * np.dtype(layout.intensity_dtype).itemsize
    with layout.xml.open("rb") as handle:
        stack = []
        for event, element in ET.iterparse(handle, events=("start", "end")):
            if event == "start":
                stack.append(element)
                continue
            if tag(element) == "referenceableParamGroup":
                groups[element.attrib["id"]] = cv(element)
            if tag(element) == "spectrum":
                mass, signal = binary_arrays(element, groups)
                if (rows >= layout.count
                        or int(mass["IMS:1000102"]) != layout.mz_offset
                        or int(mass["IMS:1000103"]) != layout.points
                        or int(signal["IMS:1000102"]) != layout.intensity_offset + rows * stride
                        or int(signal["IMS:1000103"]) != layout.points):
                    raise ValueError(f"Unsupported spectrum offsets in {layout.xml.name}.")
                scan = element.find("./{*}scanList/{*}scan")
                position = cv(scan)
                if int(position.get("IMS:1000052", 1)) != 1:
                    raise ValueError("Only a single 2D section per export is supported.")
                coordinates[rows] = (position["IMS:1000050"], position["IMS:1000051"])
                rows += 1
                stack[-2].remove(element)
            stack.pop()
    if rows != layout.count or len(np.unique(coordinates, axis=0)) != rows:
        raise ValueError(f"Expected unique XY coordinates for every spectrum in {layout.xml.name}.")
    return coordinates


def cached_arrays(path, mz, count):
    try:
        with np.load(path, allow_pickle=False) as data:
            axis, mean, xy = data["mz"], data["native_mean"], data["xy"]
        if (not np.array_equal(axis, mz) or mean.shape != mz.shape or xy.shape != (count, 2)
                or not np.isfinite(mean).all() or not np.issubdtype(xy.dtype, np.integer)
                or len(np.unique(xy, axis=0)) != count):
            return None
        return mean, xy
    except (OSError, ValueError, KeyError):
        return None


def load_arrays(layout, mz, measurement, cache, legacy, report):
    identity = hashlib.sha256(json.dumps(layout.fingerprint()).encode()).hexdigest()
    path = cache / f"{identity}.npz"
    saved = cached_arrays(path, mz, layout.count)
    if saved is not None:
        return *saved, "cache"
    if legacy:
        folder = legacy / measurement
        try:
            info = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
            if (info["source"] == layout.xml.name and not info["preview"]
                    and info["pixels_processed"] == info["pixels_declared"] == layout.count
                    and info["binary_size_bytes"] == layout.binary.stat().st_size
                    and info["native_mz_points"] == layout.points
                    and info["measurement_id"] == measurement):
                saved = cached_arrays(folder / "axes_and_native_mean.npz", mz, layout.count)
                if saved is not None:
                    return *saved, "notebook cache"
        except (OSError, ValueError, KeyError):
            pass
    report("Reading pixel coordinates", 0)
    xy = read_coordinates(layout)
    profile = layout.profile()
    chunk = max(1, min(256, (48 * 1024 * 1024) // (layout.points * np.dtype(layout.intensity_dtype).itemsize)))
    totals = np.zeros(layout.points, dtype=np.float64)
    for start in range(0, layout.count, chunk):
        end = min(start + chunk, layout.count)
        totals += profile[start:end].sum(axis=0, dtype=np.float64)
        report("Averaging every measured spectrum", end / layout.count)
    mean = totals / layout.count
    if not np.isfinite(mean).all():
        raise ValueError(f"Non-finite intensities in {layout.xml.name}.")
    cache.mkdir(parents=True, exist_ok=True)
    temp = cache / f"{identity}.tmp.npz"
    np.savez(temp, mz=mz, native_mean=mean, xy=xy)
    temp.replace(path)
    return mean, xy, "new"


def study(folder, sheet, cache, legacy, report):
    if not folder.is_dir() or not sheet.is_file():
        raise ValueError("Choose an existing export folder and sample sheet.")
    with sheet.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("The sample sheet is empty.")
    cleaned = []
    for row in rows:
        measurement = (row.get("measurement_id") or "").strip()
        animal = (row.get("animal_id") or measurement).strip()
        group = (row.get("group") or row.get("age_group") or "").strip()
        filename = (row.get("imzml_filename") or "").strip()
        if (not measurement or measurement in (".", "..") or not animal or not group or Path(measurement).name != measurement
                or Path(filename).name != filename or Path(filename).suffix.lower() != ".imzml"):
            raise ValueError("Sample sheet rows need measurement_id, group (or age_group), and an .imzML filename.")
        cleaned.append((measurement, animal, group, (row.get("section_id") or "").strip(), filename))
    if any(len({row[i] for row in cleaned}) != len(cleaned) for i in (0, 1, 4)):
        raise ValueError("Use one export per independent animal and unique measurement IDs and filenames.")
    groups = list(dict.fromkeys(row[2] for row in cleaned))
    if sum(sum(row[2] == group for row in cleaned) >= 2 for group in groups) < 2:
        raise ValueError("At least two groups need two independent animals each.")
    layouts = [inspect(folder / row[4]) for row in cleaned]
    samples = []
    for i, ((measurement, animal, group, section, _), layout) in enumerate(zip(cleaned, layouts)):
        report(f"{measurement} · checking cached spectra", i / len(cleaned))
        mz = layout.mass_axis()
        mean, xy, method = load_arrays(layout, mz, measurement, cache, legacy,
                                       lambda message, p: report(f"{measurement} · {message}", (i + p) / len(cleaned)))
        samples.append(Sample(measurement, animal, group, section, layout, mz, mean, xy, method))
    low, high = max(s.mz[0] for s in samples), min(s.mz[-1] for s in samples)
    if high <= low:
        raise ValueError("The exports have no overlapping mass range.")
    key = hashlib.sha256(json.dumps([cleaned, [l.fingerprint() for l in layouts]]).encode()).hexdigest()[:20]
    return {"key": key, "samples": samples, "groups": groups, "low": float(low), "high": float(high),
            "label": sheet.stem if sheet.stem != "samples" else folder.name or sheet.parent.parent.name}


def select_groups(dataset, reference, comparison):
    samples = [s for s in dataset["samples"] if s.group in (reference, comparison)]
    is_comparison = np.array([s.group == comparison for s in samples], dtype=bool)
    if reference == comparison or min(is_comparison.sum(), (~is_comparison).sum()) < 2:
        raise ValueError("Choose two different groups with at least two animals each.")
    return samples, is_comparison


def effects(values, comparison):
    ref, comp = values[~comparison], values[comparison]
    ref_mean, comp_mean = ref.mean(axis=0), comp.mean(axis=0)
    gap = comp_mean - ref_mean
    pooled = np.sqrt(((len(ref) - 1) * ref.var(axis=0, ddof=1)
                      + (len(comp) - 1) * comp.var(axis=0, ddof=1)) / (len(ref) + len(comp) - 2))
    standardized = np.divide(gap, pooled, where=pooled > 0, out=np.full_like(gap, np.nan))
    dropped = np.concatenate([comp_mean - (ref.sum(axis=0) - ref) / (len(ref) - 1),
                              (comp.sum(axis=0) - comp) / (len(comp) - 1) - ref_mean], axis=0)
    stable = (gap != 0) & np.all(np.sign(dropped) == np.sign(gap), axis=0)
    return ref_mean, comp_mean, gap, standardized, stable


def maxima(sample, edges):
    starts = np.searchsorted(sample.mz, edges)
    heights = np.full(len(edges) - 1, np.nan)
    positions = heights.copy()
    for i, (start, stop) in enumerate(zip(starts[:-1], starts[1:])):
        if stop > start:
            j = start + sample.mean[start:stop].argmax()
            heights[i], positions[i] = sample.mean[j], sample.mz[j]
    return heights, positions


def rank(dataset, reference, comparison, width):
    if not np.isfinite(width) or not .05 <= width <= 50:
        raise ValueError("Choose a window width between 0.05 and 50 m/z.")
    samples, is_comp = select_groups(dataset, reference, comparison)
    low, high = max(s.mz[0] for s in samples), min(s.mz[-1] for s in samples)
    first, last = int(np.ceil(low / width - 1e-9)), int(np.floor(high / width + 1e-9))
    if not 1 <= last - first <= 30000:
        raise ValueError("Choose a width that gives between 1 and 30,000 windows.")
    edges = np.round(np.arange(first, last + 1) * width, 8)
    return compare_intervals(samples, is_comp, edges)


def compare_intervals(samples, is_comp, edges):
    heights, positions = map(np.stack, zip(*(maxima(s, edges) for s in samples)))
    valid = np.isfinite(heights).all(axis=0)
    indices = np.flatnonzero(valid)
    if not len(indices):
        return []
    values = heights[:, valid]
    ref, comp, gap, d, stable = effects(values, is_comp)
    strongest = values.argmax(axis=0)
    return sorted([
        {"lo": float(edges[j]), "hi": float(edges[j + 1]), "reference": float(ref[i]),
         "comparison": float(comp[i]), "gap": float(gap[i]),
         "effect": float(d[i]) if np.isfinite(d[i]) else None, "stable": bool(stable[i]),
         "sample": samples[k].id, "apex": float(positions[k, j]), "height": float(values[k, i])}
        for i, j in enumerate(indices) for k in [strongest[i]]
    ], key=lambda row: abs(row["gap"]), reverse=True)


def curve(mz, values, lo, hi):
    start, stop = np.searchsorted(mz, [lo, hi])
    if stop - start > 1500:
        # Retain extrema for the display only. Calculations use the full profiles.
        chunks = np.array_split(np.arange(start, stop), 750)
        indices = sorted({int(chunk[values[chunk].argmin()]) for chunk in chunks}
                         | {int(chunk[values[chunk].argmax()]) for chunk in chunks})
        return [mz[indices].tolist(), values[indices].tolist()]
    return [mz[start:stop].tolist(), values[start:stop].tolist()]


def detail(dataset, reference, comparison, lo, hi, view_lo, view_hi):
    if not np.isfinite([lo, hi, view_lo, view_hi]).all() or hi <= lo or view_hi <= view_lo:
        raise ValueError("Enter increasing, finite mass bounds.")
    samples, is_comp = select_groups(dataset, reference, comparison)
    stats = compare_intervals(samples, is_comp, np.array([lo, hi]))
    if not stats:
        raise ValueError("One or more samples have no readings in this interval.")
    traces = []
    for s in samples:
        height, apex = maxima(s, np.array([lo, hi]))
        x, y = curve(s.mz, s.mean, view_lo, view_hi)
        traces.append({"id": s.id, "group": s.group, "x": x, "y": y,
                       "height": float(height[0]), "apex": float(apex[0])})
    return {"stats": stats[0], "traces": traces}


def ion_map(sample, lo, hi, report):
    if not np.isfinite([lo, hi]).all() or not sample.mz[0] <= lo < hi <= sample.mz[-1]:
        raise ValueError("Choose map bounds inside this sample's mass range.")
    a, b = np.searchsorted(sample.mz, [lo, hi])
    if a >= b:
        raise ValueError("No native channels in the map window.")
    xy = sample.xy
    origin = xy.min(axis=0)
    x, y = (xy - origin).T
    h, w = int(y.max() + 1), int(x.max() + 1)
    if h * w > 2_000_000:
        raise ValueError("The export's XY footprint is too large for this view.")
    profile = sample.layout.profile()
    values = np.empty(len(xy), dtype=np.float64)
    for start in range(0, len(xy), 2048):
        end = min(start + 2048, len(xy))
        values[start:end] = profile[start:end, a:b].sum(axis=1, dtype=np.float64)
        report("Reading target channels", end / len(xy))
    if not np.isfinite(values).all() or not np.isclose(values.mean(), sample.mean[a:b].sum(), rtol=1e-6, atol=1e-9):
        raise ValueError("The source intensities differ from the cached spectrum. Rebuild the study cache.")
    grid = np.full((h, w), np.nan)
    grid[y, x] = values
    padded = np.pad(grid, 1, constant_values=np.nan)
    patch = sum(padded[dy:dy+h, dx:dx+w] for dy in range(3) for dx in range(3)) / 9
    fallback = not np.isfinite(patch).any()
    iy, ix = np.unravel_index(np.nanargmax(grid if fallback else patch), grid.shape)
    low, high = np.percentile(values, [2, 98])
    return {"sample": sample.id, "lo": lo, "hi": hi, "origin": origin.tolist(), "width": w, "height": h,
            "low": float(low), "high": float(high),
            "values": np.where(np.isfinite(grid), grid, None).ravel().tolist(),
            "spot": {"x": int(ix + origin[0]), "y": int(iy + origin[1]),
                     "method": "Strongest measured pixel" if fallback else "Strongest fully measured 3 × 3 patch"}}


def local_spectrum(sample, x, y, lo, hi, center, isolation):
    if (not np.isfinite([lo, hi, center, isolation]).all() or not lo < hi
            or isolation <= 0 or isolation > 50):
        raise ValueError("Enter valid local spectrum bounds and isolation width.")
    matches = np.flatnonzero((sample.xy[:, 0] == x) & (sample.xy[:, 1] == y))
    if len(matches) != 1:
        raise ValueError("Select a measured pixel in the map.")
    row = sample.layout.profile()[matches[0]]
    mass, intensity = curve(sample.mz, row, lo, hi)
    lower, upper = center - isolation / 2, center + isolation / 2
    a, b = np.searchsorted(sample.mz, [lower, upper])
    indices = np.arange(max(1, a), min(len(row) - 1, b))
    peaks = indices[(row[indices] > row[indices - 1]) & (row[indices] >= row[indices + 1])]
    top = peaks[np.argsort(row[peaks])[-5:][::-1]]
    return {"x": x, "y": y, "mass": mass, "intensity": intensity, "isolation": [lower, upper],
            "peaks": [{"mz": float(sample.mz[i]), "height": float(row[i])} for i in top]}
