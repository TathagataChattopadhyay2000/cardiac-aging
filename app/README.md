# MS/MS Target Finder

Double-click **Start App.cmd**. The browser opens at http://127.0.0.1:8765/.
Keep the console open while using the app. Press Ctrl+C in the console to
stop it. If port 8765 is busy, close the earlier app instance or run
`..\.venv\Scripts\python.exe server.py --port 8766` from this folder.

The launcher uses the project’s `.venv` when present. With a new checkout,
set up the Python environment from the project README first. The app itself
uses Python, NumPy and the standard library. The Browse buttons use Tkinter.
You can paste the paths if the file picker is unavailable.

## Review a study

1. Choose a local folder with paired imzML and ibd files and a CSV sample
   sheet. Required columns are `measurement_id`, `imzml_filename`, and
   `group` (the notebook’s `age_group` also works). Optional columns are
   `animal_id` and `section_id`. Include one 2D export per independent animal
   and at least two animals in each comparison group.
2. Choose the groups and bin width. Each half-open interval contains one
   maximum from each animal’s native mean spectrum. The difference is
   comparison minus reference. The Higher and Lower views sort by raw
   difference by default. The Stable filter keeps directions that survive
   leaving out any single animal. Effect is the pooled-SD standardized
   difference. These scores are descriptive.
3. Click an interval to review the spectra and mouse-level maxima. Drag in
   the spectrum to compare custom bounds. Scroll to zoom.
4. Pick a section, check the proposed precursor and map bounds, and generate
   an ion map. The map sums native channel intensities in the selected bounds
   at every measured pixel. The crosshair starts at the center of the
   strongest fully measured 3 × 3 patch, or the strongest pixel if no such
   patch exists. Click other pixels to inspect their native spectra. The
   isolation input is the **full width** and shows the nominal range.
5. Pin reviewed spots and export the target sheet. The standalone HTML file
   includes the map, imzML coordinates, local spectrum, isolation range and
   mouse-level comparison. Open it in a browser to print to PDF if needed.
   Confirm slide identifiers, coordinate transfer and isolation settings
   with the operator.

To overlay histology, choose the image and a registration JSON containing a
`matrix` (2 × 2) and `shift` (two values) such that
`image_xy = matrix @ imzml_xy + shift`, using zero-based image pixel centers
after EXIF orientation. Include `measurement_id` and `filename` so the app can
check they match. The existing pilot registration for 1465 also works.

## Data and scope

The app supports continuous, uncompressed, shared-axis imzML exports with
contiguous 32-bit or 64-bit floating-point spectra and unique XY coordinates.
The app validates the file pair and shared axis. Processed, compressed and 3D
exports require a separate ingestion path.

The first load of a new study reads all the spectra in bounded chunks and
caches one average spectrum per animal and the pixel coordinates in
`results/msms_target_finder/cache`. The existing cardiac study can use the
notebook’s completed native spectrum cache. Map jobs read only the requested
mass channels, but can take several minutes on a mechanical drive.

The server listens on the loopback interface. Files stay on this computer.
The shortlist is saved in this browser for the current study. Export it to
retain a portable copy.
