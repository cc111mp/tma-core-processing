# Research workflow

For a guided start, use [Getting started (PDF)](tutorial/TMA_QC_User_Guide.pdf)
or its [browser edition](tutorial/user-guide.html). It begins with the default
NAS-style raw hierarchy, distinguishes imported fields from reviewed inputs,
and follows a core into reconstruction, reports and optional registration.

Use this as a reference for the task you are doing. With a prepared manifest,
you can go straight to the [normal run command](../README.md#run-a-dataset).
The synthetic demo and separate readiness check are optional. The processing
command handles its stage dependencies and verifies inputs itself.

The [pipeline reference](pipeline.md) specifies file contracts; [METHODS.md](METHODS.md)
connects measurements to their assumptions and current validation limits.

Terminal examples use `python -m tma_qc` to select the package installed in the
active Python environment. The installed `tma-qc` command is its shorter equivalent.
For scripts and notebooks, see [Python usage](#use-the-workflow-from-python).

## Explore the software with synthetic data

```bash
python -m tma_qc demo --out /tmp/tma-qc-demo
```

Use a new directory. The example creates two AF cores, frozen synthetic fields,
provided native positions and an explicit H&E pair. Review
`results/reports/index.html`, then open `results/stage-results.json` to see the
relationship between a displayed result and its saved stage measurements.
The demo's inputs are generated with fixed seeds in [demo.py](../src/tma_qc/demo.py).

## Organize inputs and results

A dataset workspace can be organized as:

```text
tma-study/
  raw/                          # Original NAS-style AF acquisitions
  he/HE_SLIDE_01/A1.tif         # Original H&E core image
  pairing/sections.csv          # Reviewed AF/H&E assignments
  manifests/manifest.json        # Generated AF inventory
  manifests/paired.json          # AF manifest plus explicit section pairs
  geometry/                     # Native tile positions and held-out landmarks
  models/<group>/               # Frozen flatfield, darkfield and model receipt
  configs/af.toml                # Main workflow settings
  configs/core-registration.toml # Optional research matching/refinement settings
  runs/run01/                   # acquisitions/, pairs/, reports/
```

Put `runtime.cache_root` on local SSD outside `raw/`. The run root must also be
outside the raw archive. Asset paths other than FOVs resolve relative to the
manifest; FOV paths resolve under `raw_root`. Directory names are flexible once
the manifest is explicit. From `manifests/manifest.json`, geometry/model paths
start with `../geometry/` and `../models/`, respectively, or use absolute paths.
Pairing CSV asset paths resolve beside the CSV. `import-pairs` writes normalized
absolute asset paths into its output manifest, preserving the source inputs.

For a completed transfer inventory, the importer provides the initial manifest:

```bash
python -m tma_qc import-copy --database /local/inventory.sqlite3 --raw-root /data/raw --out /data/manifests/manifest.json
```

For NAS-style AF folders without a transfer database, generate the same input
contract directly from the raw tree:

```bash
python -m tma_qc import-folder --raw-root /data/raw --out /data/manifests/manifest.json
```

This hashes selected original FOVs and preserves exact source folder labels.
It leaves incomplete/ambiguous selections blocked and does not certify physical
acquisition completeness. See [folder import](../examples/README.md#generate-a-manifest-from-nas-style-folders)
for supported layouts and the model/geometry/pair assignments to review.

The transfer importer refuses incomplete inventories and preserves ambiguous acquisitions
as blocked records. A ready subset of an ongoing transfer needs an independently
verified, frozen manifest; do not make an incomplete inventory appear complete.
Supply compatible group/model assignments and geometry for the selected backend.
Add explicit H&E section pairs when you want registration. The
[input templates](../examples/README.md) show the format.

## Choose processing settings

Copy [configs/af2048.toml](../configs/af2048.toml) to your dataset configuration.
Set the local cache location. Unspecified settings come from validated defaults
in [config.py](../src/tma_qc/config.py); the complete effective configuration is
saved in each run's `run.json` and `attempts/<id>.json`.

Use `tma-qc methods` to discover algorithm choices. `run` and `plan` accept
`--geometry-method`, `--illumination-method`, `--registration-method`,
`--registration-transform`, `--registration-refinement` and repeated typed
`--set SECTION.KEY=VALUE` overrides. Python `run()` accepts the corresponding
underscored selector names and a nested `parameters` dictionary; `api.settings()`
inspects the validated main configuration. Defaults are overridden by the TOML,
then parameter overrides, then explicit method selectors. See
[methods and settings](SETTINGS.md) for complete defaults, units and examples.

Record acquisition compatibility and section relationships in the manifest.
Keep original and repeat acquisitions distinct. Use frozen correction fields
when comparing code or parameters. Fit a different illumination model as a
separate experiment with an explicit pool and its own saved receipt.

## Inspect readiness or process a selection

`plan` provides an optional readiness report. Use `run` to process selected cores:

```bash
# Optional readiness check
python -m tma_qc plan --manifest /data/manifests/manifest.json --config /data/configs/af.toml --out /data/runs/pilot --core example-original-A1-AF
# Process or resume this core
python -m tma_qc run --manifest /data/manifests/manifest.json --config /data/configs/af.toml --out /data/runs/pilot --core example-original-A1-AF --resume
```

Use real manifest UIDs. Repeat `--core` to select several cores. Preflight reads
source hashes and headers, checks model/geometry prerequisites and estimates AF
capacity. It creates no output/cache directories and does not process images.
Processing verifies sources again. Full source hashing can take time on NAS.

## Use the workflow from Python

The installed distribution is `phomedics-tma-qc`; its importable module is
`tma_qc`. The public `tma_qc.api` module exposes the same import/run workflow for scripts
and notebooks. The earlier lower-level functions remain available.

Generate a draft manifest once, using a new output filename:

```python
from tma_qc.api import import_folder

draft = import_folder(
    raw_root="/data/raw",
    output="/data/manifests/manifest.json",
    dataset_id="TMA-STUDY-01",
)
print("Core acquisitions:", len(draft["cores"]))
```

This has the same folder selection, hashing and review requirements as
`import-folder`. Review the draft and supply geometry and compatible models
before processing it. The importer refuses an existing output file.

Run a prepared manifest:

```python
from tma_qc.api import run

summary = run(
    manifest="/data/manifests/manifest.json",
    output="/data/runs/pilot",
    config="/data/configs/af.toml",
    selected=["CORE_UID_FROM_MANIFEST"],
    resume=True,
)
print("Errors:", summary["execution_errors"])
print("Blocked stages:", summary["blocked_stages"])
```

Replace the placeholder with a real manifest core UID. Omit `selected` to
process all cores, or pass a list of UIDs. The function returns a summary
dictionary; inspect `execution_errors`, `blocked_stages` and `stage_results`
before starting dependent work. Input/configuration failures can raise Python
exceptions. It writes the same provenance, stage artifacts and
`reports/index.html` as the terminal workflow. Scientific quality review
remains separate from execution completion.

## Register explicitly paired H&E cores

Start with the AF manifest you already prepared and **one already cropped H&E
core image per pair**. This setup registers core images; whole-slide H&E core
extraction is a separate preparation step. Existing AF inputs remain in place.

For a first pair, prepare only:

```text
tma-study/
  he/HE_SLIDE_01/A1.tif    # H&E core crop
  pairing/sections.csv    # Your reviewed assignments
  manifests/manifest.json # Existing AF manifest
  configs/af.toml         # Existing AF settings
```

The importer creates `manifests/paired.json`; the runner creates outputs and QC.
Users do not calculate hashes or write manifest JSON by hand.

### Find the AF core, then write one pairing row

From `tma-study/`, list AF UIDs with their source folder/acquisition and core label:

```bash
python - <<'PY'
from tma_qc.io.manifest import Manifest
for c in Manifest("manifests/manifest.json").cores:
    print(c.uid, c.metadata.get("source_folder", c.acquisition), c.core)
PY
```

Choose the acquisition you already reviewed/reconstructed. Repeated scans or
lamp groups have different UIDs even if their core labels are both `A1`.
Copy the exact UID into `af_core`; choose your own readable pair `uid`, such as
`PAIR_01`. Save the [four-column template](../examples/section-pairs.example.csv)
as `pairing/sections.csv`:

```csv
uid,af_core,he_path,relationship
PAIR_01,CORE_UID_FROM_MANIFEST,../he/HE_SLIDE_01/A1.tif,neighboring_sections
```

`he_path` points to the H&E core crop, relative to the CSV. `relationship` is
`neighboring_sections` or `same_section`, based on reviewed section records.
AF and H&E may have different original slide IDs. Matching folder/core labels
alone cannot establish that relationship.

### Import the row and enable the default fitter

```bash
python -m tma_qc import-pairs \
  --manifest /data/tma-study/manifests/manifest.json \
  --pairs /data/tma-study/pairing/sections.csv \
  --out /data/tma-study/manifests/paired.json
```

The importer validates pair references and records H&E hashes. It refuses an
existing output file. Review paired identities and relationships before running.
The equivalent Python call is:

```python
from tma_qc.api import import_pairs

paired = import_pairs(
    manifest="/data/tma-study/manifests/manifest.json",
    pairs_csv="/data/tma-study/pairing/sections.csv",
    output="/data/tma-study/manifests/paired.json",
)
```

Edit the existing `[registration]` section of `configs/af.toml`, or add it if
absent. Keep the ordinary AF and cache settings:

```toml
[registration]
backend = "core"
core_method = "geometry_sift"
```

The built-in defaults perform global geometry/SIFT fitting and export aligned
H&E. For this basic setup, omit `core_config`. No second TOML, supplied transform,
landmark file or optional model package is required for this first run.

### Run one AF core and its assigned H&E pairs

Run the paired manifest using the normal command:

```bash
python -m tma_qc run \
  --manifest /data/tma-study/manifests/paired.json \
  --config /data/tma-study/configs/af.toml \
  --out /data/tma-study/runs/run01 \
  --core CORE_UID_FROM_MANIFEST --resume
```

The pipeline resolves AF dependencies and adds `he_qc`, `registration` and
`registration_qc` for the selected cores' pairs. Outputs under
`pairs/<pair_uid>/registration/<stage-key>/` include the native transform,
aligned H&E, geometric-overlap image and overlay. Core fitting adds candidate
and fit-correspondence evidence plus refinement decisions. Retain the residual
pull-map alongside the transform when a local warp was applied. Global matrices
alone cannot reproduce that local warp. Open the pair report and any supplied
independent landmark evidence before downstream analysis.

Without supplied landmarks, registration still runs; independent landmark
accuracy is reported unavailable. Open `runs/run01/reports/index.html`, check
execution and review the original images, overlay and geometric overlap. A
completed fit does not certify correspondence between neighboring sections.

### Add evaluation or method tuning when needed

The [advanced CSV](../examples/section-pairs.advanced.example.csv) adds optional
`he_slide_label`, `transform`, `landmarks`, `af_mpp` and `he_mpp`. Blank fields
require no files. Assign a path only when that reviewed asset exists. After
editing the CSV, rerun `import-pairs` from the original AF manifest to a new
output such as `paired-evaluation.json`, then run using that new manifest. CSV
edits do not update an existing paired JSON. Landmarks
are independent native-pixel evaluation points in declared JSON or CSV columns
`he_x,he_y,af_x,af_y`. Coordinates must be distinct on both images; optional IDs
must be nonempty and unique. Coordinate uniqueness alone does not establish
biological independence. Positive pixel sizes require acquisition calibration.

For explicit matching/refinement settings, use `[registration.matching]` and
`[registration.refinement]` in the same main TOML. The
[annotated example](../configs/methods.example.toml) shows both. Existing
[core-registration.toml](../configs/core-registration.toml) files also work:
set `core_config = "core-registration.toml"` inside `[registration]`; its relative
path resolves beside the main TOML. Inline keys override that file's matching,
refinement and RoMa values individually. Other core methods are `sift`, `orb` and
`geometry_roma`; compare methods in separate run directories. A supplied pair
transform takes precedence over fitting. The default `provided` backend is for
that supplied-transform route.

Optional `geometry_roma` requires `registration-roma` and cached weights. Optional
B-spline/MI refinement requires `registration-refine` and
`[registration.refinement] backend = "bspline_mi"` in the main TOML (or
`[refinement]` in the research TOML). The default local
refinement is `none`. See [advanced registration settings](../configs/registration-core.toml)
and [method definitions](METHODS.md#afhe-registration).

## Review images and QC evidence

Open `reports/index.html` in the run directory. Follow the batch table to a
core's raw/selected comparison and the source FOV grid. Review the numerical
measurements together with the original images.

| Question | Inspect |
| --- | --- |
| Did the correct files arrive? | `index`, source SHA256s and inventory coverage |
| Where is local image detail weak? | `tile_qc` windows and raw FOV grid |
| Are tile directions/positions plausible? | `geometry`, `geometry_qc`, raw reconstruction |
| Did correction improve agreement without hiding signal changes? | `illumination_qc`: assessed/candidate pairs, exclusion reasons, mismatch tails, per-FOV spans and gain evidence |
| Are seams, holes or brightness patterns visible? | `mosaic_qc`: per-seam tails, worst native lines, global/local native tile-pitch profiles, coverage and TIFFs |
| How much tissue is established? | `tissue` is provisional; an accepted area denominator is unavailable |
| Does H&E align on shared anatomy? | `registration`, independent `registration_qc`, overlays and original sections |

The stage receipt lists each named output path and SHA256. Float32 TIFFs preserve
scientific counts; PNGs are display products. Raw/selected comparisons share a
display window. Different cores or acquisition groups may use different windows,
so visual brightness across such panels is not an intensity calibration.

## Resume or compare experiments

Rerun the same command with `--resume` after interruption. Source files and cached
artifacts are checked before reuse. `stage-results.json` describes the selected
attempt; `cohort/` and the reports retain compatible observations for other units
with their observation times. Reporting alone does not reverify historical raw
files. Keep the original run directory when preserving an experiment.

```bash
python -m tma_qc report --run /data/runs/pilot
python -m tma_qc qc --run /data/runs/pilot --config /data/configs/qc-overrides.toml
```

`report` rebuilds views from saved records. `qc` replays the frozen manifest with
configuration overrides and reuses unchanged valid image stages. It writes a new
attempt within that run. For a separate experimental comparison, choose a new
run directory and keep the same sources, geometry, fields and relevant settings.

Code and runtime identities are part of cache keys. Even formatting or module
reorganization changes the code hash and invalidates old processing receipts.
Old artifacts are retained; budget storage for a rebuild. Do not edit saved
fingerprints to force reuse. A comparison should retain the manifest/configuration,
code/runtime identities, model receipts, execution/QC states and matched units.

## Investigate a method

Place a reproducible investigation under [experiments/](../experiments/README.md).
Keep generated data/results outside the checkout and write source assignments,
parameters and measurements into its result record. Separate fitting from held-out
acquisitions. When a method is ready for the workflow, follow the
[development guide](DEVELOPMENT.md) and add an appropriate scientific control.

## Build the tutorial PDF

The self-contained source is `docs/tutorial/user-guide.html`. Run
`make tutorial` from the checkout with a local Chrome/Chromium browser installed,
or print the HTML to A4 PDF manually. To select a browser or output path:

```bash
python docs/tutorial/build_pdf.py --browser /path/to/chrome --output /path/to/guide.pdf
```
