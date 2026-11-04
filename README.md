# Cardiac aging: spatial molecular analysis

Reproducible analysis of MALDI mass spectrometry imaging from twelve independent
mouse hearts: six young and six old. The project begins with file inspection,
quality control and molecular maps, followed by mouse-level statistical
comparisons and exploratory machine learning.

## Setup

Use Python 3.10 or newer. From the repository folder in PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e .
Copy-Item config.example.json config.local.json
.venv\Scripts\python -m ipykernel install --user --name cardiac-aging --display-name "Python (cardiac-aging)"
.venv\Scripts\jupyter lab
```

Edit `config.local.json` if the data move from `F:/`. Open
`notebooks/01_data_inspection.ipynb` and choose the project kernel. Run cells in
order. The spatial preview can take several minutes on an external drive.

For a fast header inventory without installing plotting packages:

```powershell
$env:PYTHONPATH = "src"
python -m cardiac_aging.inspection
```

## Data and metadata

Keep each imzML/ibd pair together with matching base names. The files remain on
the external drive; approximately 688 GB of binary data are not stored in Git.
The ignore rules also exclude generated results, local paths and notebook
checkpoints. Clear notebook outputs before publishing if they contain
restricted information. Confirm laboratory approval before making data or
research results public.

`metadata/samples.csv` maps measurement IDs 1465–1476 to the original Lux IDs and
age groups. Each row is a separate mouse. Exact ages, sex, batches and section
identifiers remain unknown. The supplied record date is preserved verbatim.
Filename dates are stored with their interpretation unconfirmed. Investigation
of date effects is deferred at the researcher's request.

## First inspection

The starting notebook checks all twelve headers and file sizes, reads 24
reproducibly selected spectra from mouse 1465, and creates a spatial preview near
m/z 1531.8. It uses read-only memory maps and narrow binary reads rather than
loading the full intensity matrix. The small binary reader is intentionally
limited to the continuous, uncompressed float32/float64 layout checked in these
exports. Other layouts require a general imzML reader.

Observed headers declare profile spectra, positive polarity, a mass range of
600–2000 m/z, 20 µm pixel spacing, and baseline reduction. Near-constant signal
sums in sampled spectra suggest prior TIC normalization; confirm export
settings before applying additional normalization. Size checks do not establish
complete file integrity or replace checksum validation.

## Analysis plan

1. Inspect all samples and assess masks, signal quality and comparable anatomy.
2. Build compact, aligned molecular features using documented preprocessing.
3. Estimate young–old effects with uncertainty using mice as replicates.
4. Investigate spatial localization and validate candidate molecular identities.
5. Explore simple prediction models with all pixels from each mouse kept in the
   same validation fold. Fit feature selection and tuning within training folds.

The twelve animals provide a small exploratory cohort. Pixels are nested
measurements, not additional independent mice. Existing TIC normalization
means signal differences describe relative exported intensities, not direct
absolute molecular concentrations. No biological or predictive claims are
established by the starter notebook.

## Project layout

```text
configs: config.example.json + ignored config.local.json
metadata/       sample mapping and field definitions
src/            reusable inspection utilities
notebooks/      executable inspection and visualization
results/        local generated outputs (ignored)
```
