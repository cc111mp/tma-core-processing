# PhoMedics TMA preprocessing and QC

A research workflow for reconstructing TMA autofluorescence (AF) cores from raw
FOVs, applying frozen illumination correction, reviewing stage-specific QC, and
registering explicitly paired H&E sections. Version **0.4.1**.

New users: start with [Getting started (PDF)](docs/tutorial/TMA_QC_User_Guide.pdf),
beginning with the default NAS-style folder structure. The
[browser edition](docs/tutorial/user-guide.html) has the same content.

## Run a dataset

Install once in an activated Python 3.11+ environment:

```bash
python -m pip install -e '.[codecs]'
```

For raw AF folders arranged like the company NAS, generate the initial manifest
directly from the folder root:

```bash
python -m tma_qc import-folder --raw-root /data/TMA_raw/2026 --out /data/manifests/manifest.json
```

This discovers slides, cores, acquisitions and lamp groups and hashes the selected
raw tiles. Review completeness and model assignments, add geometry, and supply
explicit H&E pairs when needed. The [input guide](examples/README.md) describes
the supported layouts and remaining assignments.

A [manifest](examples/README.md) describes the input files, core identities,
correction models and tile positions. A [configuration](configs/af2048.toml)
holds processing settings, including the local SSD cache location. Run with:

```bash
python -m tma_qc run \
  --manifest /data/manifest.json \
  --config /data/af.toml \
  --out /results/run_001 \
  --resume
```

Open `/results/run_001/reports/index.html` for the core images and QC evidence.
Repeat the command to resume compatible verified work. Add `--core CORE_UID`
to process one core; repeat that option to select several. Omit it to process
all manifest cores. `tma-qc` is the shorter equivalent of `python -m tma_qc`.

The run verifies its own inputs. Use `python -m tma_qc plan` when you want a separate
readiness report before a batch; it is optional. The
[research guide](docs/RESEARCH_GUIDE.md) covers preparing inputs, inspecting
results and comparing experiments. You can use those tasks independently.

For scripts and notebooks, import the same runner:

```python
from tma_qc.api import run

summary = run(
    manifest="/data/manifest.json",
    output="/results/run_001",
    config="/data/af.toml",
    resume=True,
)
print(summary["execution_errors"], summary["blocked_stages"])
```

This runs all manifest cores; use `selected=["CORE_UID"]` for a selection.
The [Python usage recipe](docs/RESEARCH_GUIDE.md#use-the-workflow-from-python)
also shows folder import. Execution status and image-quality review remain
separate in either interface.

## Choose methods and parameters

Defaults work for the supported input profile. Inspect method choices with
`tma-qc methods`, then select a method and override individual settings:

```bash
tma-qc run --manifest /data/manifests/paired.json --config /data/configs/af.toml \
  --out /results/geometry-sift --registration-method geometry_sift \
  --set tile_qc.stride=128 --set registration.matching.max_dimension=1024 --resume
```

The Python equivalent uses `registration_method="geometry_sift"` and
`parameters={"tile_qc": {"stride": 128}, "registration": {"matching": {"max_dimension": 1024}}}`
in `run()`. `tma_qc.api.settings()` inspects validated settings without processing
images. Precedence is defaults → TOML → parameter overrides → method selectors.
`plan` accepts the same options. `run --resume` uses the settings supplied again
on that invocation.

Advanced core matching and refinement can live in the main TOML under
`[registration.matching]`, `[registration.refinement]` and `[registration.roma]`.
Existing external core TOMLs remain supported. Supplied geometry and pair
transforms retain precedence over estimation/fitting. See the complete
[methods and settings reference](docs/SETTINGS.md) for defaults, units,
requirements, CLI/Python examples and the [annotated TOML](configs/methods.example.toml).

## Register paired H&E cores

Reuse your prepared AF manifest. Add already cropped H&E core images under
`he/<source-slide-label>/` and a reviewed `pairing/sections.csv` using
[the four-column template](examples/section-pairs.example.csv). Each row selects
one AF core UID, one H&E core crop and their section relationship. Original AF
and H&E slide labels can differ. Whole-slide H&E core extraction is a separate
preparation step. The [registration setup recipe](docs/RESEARCH_GUIDE.md#register-explicitly-paired-he-cores)
shows how to find the exact AF UID.

```bash
python -m tma_qc import-pairs \
  --manifest /data/manifests/manifest.json \
  --pairs /data/pairing/sections.csv \
  --out /data/manifests/paired.json
```

The command hashes H&E sources and writes the paired manifest; users do not fill
in hashes or edit that JSON. Edit the existing `[registration]` section in your
AF TOML, or add it if absent:

```toml
[registration]
backend = "core"
core_method = "geometry_sift"
```

Run the paired manifest with the same `python -m tma_qc run` command. The workflow
reconstructs AF cores, fits each declared H&E partner, exports aligned native H&E,
and evaluates separately supplied held-out landmarks. Results live under
`pairs/<pair_uid>/` and the shared offline report.

This first run uses the built-in global fitting defaults. Landmarks, supplied
transforms and a second research TOML are optional advanced inputs; use
[the advanced CSV](examples/section-pairs.advanced.example.csv) when needed.

The integrated research backend supports geometry-guided SIFT, ORB and optional
cached RoMa, plus optional bounded B-spline/MI refinement. Install
`.[registration-roma]` or `.[registration-refine]` for those optional paths.
RoMa requires explicitly prepared cached weights. All results require scientific
review. Provided native transforms remain supported, including saved research
transforms with their residual maps. See the [research guide](docs/RESEARCH_GUIDE.md)
for configuration, coordinate conventions and validation limits.

## Try the software with synthetic inputs

If you want to explore the output first, run:

```bash
python -m tma_qc demo --out /tmp/tma-qc-demo
```

Choose a new directory, then open
`/tmp/tma-qc-demo/results/reports/index.html`. This optional example uses known
synthetic inputs and exercises the software.

## What the run produces

```text
Verified raw FOVs → tile QC → native geometry → illumination correction
                → raw/corrected reconstruction → mosaic QC → tissue support
Explicit H&E pair + AF reconstruction → registration → independent landmark QC
```

The command handles the stage sequence. AF–H&E registration runs for explicitly
supplied pairs. Native TIFFs and QC evidence have separate records; a diagnostic
failure can leave a valid reconstruction available for review.

Execution completion and scientific acceptance are recorded separately. Image
quality outputs currently require **REVIEW**. The focus measurements are detail
diagnostics; tissue support is provisional. AF and H&E in the current study are
neighboring sections. See [methods and limitations](docs/METHODS.md).
`run` exit codes describe execution: 0 complete, 1 error, 2 blocked.

The initial QC review scope covers uneven illumination/GRID, stitching evidence,
focus/detail evidence and declared-input integrity. Reports show measurement
availability and reasons for abstention. These measurements are not calibrated
to reproduce the historical Excel decisions; see the [common-error review](docs/REVIEW_20261022.md).

## Find what you need

| Task | Start here |
| --- | --- |
| Learn the default folders, generate a manifest and run a first core | [Getting started (PDF)](docs/tutorial/TMA_QC_User_Guide.pdf) |
| Prepare inputs, run a pilot, inspect outputs, reproduce an experiment | [Research guide](docs/RESEARCH_GUIDE.md) |
| Select methods, tune parameters and inspect defaults | [Methods and settings](docs/SETTINGS.md) |
| Generate a manifest from NAS-style raw AF folders | [Folder import](examples/README.md#generate-a-manifest-from-nas-style-folders) |
| Process a complete AF core before the bulk raw copy finishes | [Source-first recipe](docs/source-first.md) |
| Understand formulas, units, assumptions and current QC limits | [Methods](docs/METHODS.md) |
| Follow the QC design, stage evidence and registration/fold limitations | [Stage QC guide](docs/STAGE_QC.md) |
| Find stages, dependencies, manifests, output records and cache behavior | [Pipeline reference](docs/pipeline.md) |
| Change code, add a method, run formatting and tests | [Development guide](docs/DEVELOPMENT.md) |
| Fill a manifest and native-position file | [Input examples](examples/README.md) |
| Check measured controls and previous speed benchmarks | [Validation record](docs/VALIDATION.md) |
| Inspect reproduced defects, repairs and audit limits | [Deep correctness review](docs/REVIEW_20261021.md) |
| Review the common-error checks and current numerical/input fixes | [Common-error audit](docs/REVIEW_20261022.md) |
| Use the standalone transfer, registration-log or montage tools | [Legacy tools](docs/legacy-tools.md) |
| Plan work that still needs scientific validation | [Roadmap](docs/ROADMAP.md) |

## Code map

```text
src/tma_qc/
  cli.py, commands.py    # Command interface and application handlers
  api.py, settings.py    # Public Python API and shared method selectors
  workflows.py          # Read here for the AF and AF–H&E stage sequence
  context.py            # Per-core/pair inputs, output access and transient caches
  pipeline.py           # Checkpointed execution and failure isolation
  schema.py, config.py  # Data contracts and validated defaults
  registration_config.py # Pure configuration for integrated core methods
  stages/               # One module per processing stage
  backends/             # CIDRE, MIST, native blending and geometric warping
    core_registration/  # Integrated core fitting, optional RoMa/local refinement
  qc/                   # Measurement functions and integrity checks
  io/                   # Manifests, images, coordinates and raw tile caching
  reporting/            # Offline core, slide and batch review pages
tests/                  # Unit, integration, scientific and legacy controls
configs/                # TOML starting points and QC overrides
examples/               # Input templates and the source-first AF recipe
experiments/            # Scientific investigations outside the production path
docs/                   # Research, methods, development and validation guides
```

Keep raw data, fitted models, caches and run outputs outside the code checkout.
Manifests reference those assets explicitly. The repository is private and
contains study identifiers and workstation-specific historical documentation.

To check a change, run `make check` in the activated environment. Tool versions
and individual commands are described in the [development guide](docs/DEVELOPMENT.md).
Release changes are recorded in [CHANGELOG.md](docs/CHANGELOG.md).
