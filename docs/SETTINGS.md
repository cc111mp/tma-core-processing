# Methods and settings

Run with the defaults, select a supported method, or tune individual parameters.
The terminal and Python interfaces use the same resolver and validation. A
manifest identifies the data and reviewed pair assignments; settings describe
how to process them.

## Start with method choices

```bash
tma-qc methods
tma-qc methods --json
tma-qc stages
tma-qc stages --json
tma-qc settings --help
tma-qc run --help
```

The method list works before imaging dependencies are installed. It describes
available methods and their requirements; it does not download models or choose
a method based on the data.

| Area | Default | Available choices |
| --- | --- | --- |
| Geometry | `provided` | Supplied native positions, or `mist` with a reviewed grid and configured Java runtime |
| Illumination | `cidre` | Apply frozen CIDRE fields, or `none` to use original counts |
| Registration | `provided` | Supplied transform, experimental `sift_affine`, or integrated `sift`, `orb`, `geometry_sift`, `geometry_roma` |
| Core transformation model | `similarity` | `similarity` or `affine` |
| Core local refinement | `none` | `none` or optional `bspline_mi` |
| Tile QC | Fixed | Gradient/detail, contrast and residual-noise diagnostics |
| Mosaic | Fixed | Weighted linear blending; `mosaic.alpha` tunes the weights |
| Tissue support | Fixed | Provisional quantile/MAD thresholds and hysteresis |
| Geometry / illumination / mosaic QC | Fixed | Defined measurements and evidence sampling; no automatic tile reordering |

`geometry_sift` is the built-in core initializer when the `core` backend is
enabled. The package's default registration backend remains `provided`.
Registration runs only for explicit AF–H&E pairs. Supplied tile positions take
precedence over MIST; a supplied pair transform takes precedence over fitting,
including command-line method selections. Remove an assignment only after
reviewing the intended new experiment, and keep a separate manifest/run for it.

Optional RoMa needs the `registration-roma` extra and explicitly cached model
weights. B-spline/MI needs `registration-refine`. An unavailable method is
reported as a missing prerequisite; there is no silent substitute. All current
image-quality conclusions remain advisory review evidence.

The [stage map](STAGES.md) connects each setting section to its inputs, operation,
outputs and dependencies. The [annotated YAML](../configs/methods.example.yaml)
shows all main parameter sections, including optional-backend presets. A key can
be valid but inactive: for example, `registration.max_dimension` affects legacy
`sift_affine`, while `registration.matching.max_dimension` affects `core`.

## Choose for the data you actually have

| Situation | Explicit choice | Requirement / consequence |
| --- | --- | --- |
| Native positions already exist | `geometry.backend: provided` | Position file must match selected FOV IDs and native coordinates. The engine loads positions; it does not estimate missing scan order. |
| Positions need estimation and a reviewed regular grid exists | `geometry.backend: mist` | Requires ordered FOVs, reviewed grid/origin/numbering/overlap metadata, Java and an explicit MIST classpath. Existing positions still take precedence. |
| No compatible frozen illumination model exists | `illumination.mode: none` | Records an uncorrected original-count baseline. This is the annotated YAML's deliberate choice, not the package default. |
| Compatible frozen fields are assigned | `illumination.mode: cidre` | Requires manifest flatfield, darkfield and compatible model metadata. Applies the frozen correction without fitting a new model. |
| No H&E pairs are declared | Keep registration settings as presets | No pair stages run. H&E whole-slide extraction is separate preparation. |
| Reviewed pair transform exists | `registration.backend: provided` | Uses that transform. A supplied transform also takes precedence over a fitting selector. |
| Fit an explicitly paired H&E core | `registration.backend: core`, `core_method: geometry_sift` | Uses reviewed pair identity and initialized shared structure; inspect fit, ambiguity and independent landmark evidence. Other initializers are listed above. |
| Evaluate local refinement | Core `refinement.backend: bspline_mi` | Requires the optional extra and eligible global fit; displacement/Jacobian guards can reject refinement while retaining the global candidate. |

None of these method choices establishes scientific acceptance. Change a method
or scientific parameter in a separate run directory for a reviewable comparison.

## Use YAML or TOML

The engine accepts `.yaml`/`.yml` and `.toml` for `--config`, with the same section
names, validation and precedence. YAML support is installed with the package.
For a short uncorrected AF configuration with existing native positions:

```yaml
profile:
  shape: [2048, 2048]
  dtype: uint16
geometry:
  backend: provided
illumination:
  mode: none
tile_qc:
  window: 256
  stride: 256
  prefilter_sigma: 1.0
mosaic:
  alpha: 1.5
  chunk_rows: 256
  max_pixels: 500000000
```

Omitted values use package defaults. Inspect the complete effective configuration
without opening images or creating a run:

```bash
tma-qc settings --config configs/methods.example.yaml --format yaml
```

Default output remains JSON; use `--format yaml` to print a portable YAML settings
file. Inline core overrides remain sparse until pair registration resolves its
core defaults. File format does not change method selection or save missing
input assets. Use the same `--config` path with `plan` and `run`.

This is the portable **engine settings** schema. The study-specific GX10 workflow
wrapper uses a different schema with `schema_version`, `source`, `selection`,
`paths`, `execution`, `settings` and `policy`. For the engine, parameter sections
are at the YAML root; manifest/output/selection are supplied with `--manifest`,
`--out` and repeated `--core` arguments. Do not pass a wrapper YAML directly to
`tma-qc --config`. Numbered-layout preparation from that wrapper is not ported
by accepting YAML here.

The engine has no global overlap-ratio parameter. Native positions establish
overlap for supplied geometry. MIST requires per-acquisition reviewed
`horizontal_overlap` and `vertical_overlap` percentages and other grid metadata
in the manifest, rather than an invented dataset-wide YAML prior.

## Choose from the terminal

Inspect the effective configuration before starting a run:

```bash
tma-qc settings \
  --config /data/tma-study/configs/af.toml \
  --registration-method geometry_sift \
  --set tile_qc.stride=128
```

This prints validated JSON (or YAML with `--format yaml`) without reading a manifest or images and without
creating output/cache directories. It works before imaging dependencies are
installed. Inline core tables remain sparse; the fully resolved core configuration
is recorded when registration runs. Use the same options with `run` or `plan`.

Use an existing AF settings file and add the selected registration method:

```bash
tma-qc run \
  --manifest /data/tma-study/manifests/paired.json \
  --config /data/tma-study/configs/af.toml \
  --out /data/tma-study/runs/geometry-sift \
  --registration-method geometry_sift \
  --registration-transform similarity \
  --registration-refinement none \
  --set tile_qc.stride=128 \
  --set registration.matching.max_dimension=1024 \
  --resume
```

`settings` and `plan` accept the same method selectors and `--set` arguments. For a comparison,
use another run directory and change, for example, `--registration-method orb`.
Hold the source manifest, illumination model and evaluation landmarks fixed.
Keep independently evaluated landmarks out of fitting and parameter selection.

`--set SECTION.KEY=VALUE` may be repeated for different paths. Values use TOML
literal syntax: integers (`128`), decimals (`1.5`), booleans (`false`), arrays
(`[0,90,180,270]`) or quoted strings. Quote the entire argument when the shell
must preserve the TOML string quotes:

```bash
tma-qc plan \
  --manifest /data/tma-study/manifests/paired.json \
  --config /data/tma-study/configs/af.toml \
  --out /data/tma-study/runs/intensity \
  --registration-method geometry_sift \
  --set 'registration.matching.feature_view="intensity"'
```

Unknown names, invalid types/ranges and conflicting duplicate `--set` paths are
errors. Method selectors are named options; a new algorithm still requires an
implementation, an input/output contract and validation.

## Choose from Python

```python
from tma_qc.api import plan, run, settings

parameters = {
    "tile_qc": {"stride": 128},
    "registration": {"matching": {"max_dimension": 1024}},
}
effective = settings(
    "/data/tma-study/configs/af.toml",
    registration_method="geometry_sift",
    parameters=parameters,
)
print(effective["registration"])

readiness = plan(
    manifest="/data/tma-study/manifests/paired.json",
    output="/data/tma-study/runs/geometry-sift",
    config="/data/tma-study/configs/af.toml",
    registration_method="geometry_sift",
    parameters=parameters,
)
print(readiness["ready_for_execution"], readiness["issues"])

summary = run(
    manifest="/data/tma-study/manifests/paired.json",
    output="/data/tma-study/runs/geometry-sift",
    config="/data/tma-study/configs/af.toml",
    registration_method="geometry_sift",
    registration_transform="similarity",
    registration_refinement="none",
    parameters=parameters,
    resume=True,
)
```

The selector names are `geometry_method`, `illumination_method`,
`registration_method`, `registration_transform` and `registration_refinement`.
The last two apply to integrated core registration. Select a core method before
using them. `parameters` is a nested dictionary with the same sections as YAML/TOML.
`settings()` returns the validated main configuration without processing images;
its inline core tables remain sparse. Fully resolved core settings are recorded
when a registration stage runs.

`plan()` reads the manifest and checks source hashes/headers, assets, runtime
prerequisites and planned storage without creating a run. Supply `output` for an
operational readiness result. Its `ready_for_execution` describes prerequisites,
while `run()` still verifies inputs when processing starts.

Like YAML/TOML, generic parameter dictionaries may retain presets for another
backend. Only the active backend's parameters apply. A returned settings
dictionary can be copied, edited and passed as `parameters` in another call.
Explicit transform/refinement selectors require a core method on that call.
Declared calibration assets can still be checked for input provenance when
correction is disabled; inactive numerical presets do not waive asset integrity.

## Keep core parameters in the main settings file

The existing `[geometry] backend`, `[illumination] mode`, and `[registration]
backend/core_method` names remain supported. For core methods, nested tables in
the main YAML or TOML make the second research file optional even when tuning.
For example, in TOML:

```toml
[registration]
backend = "core"
core_method = "geometry_sift"

[registration.matching]
max_dimension = 1024
feature_view = "gradient"
transform = "similarity"
min_inliers = 12

[registration.refinement]
backend = "none"

[registration.roma]
device = "auto"
```

See the annotated [TOML](../configs/methods.example.toml) and
[YAML](../configs/methods.example.yaml) examples. Merge sections into your
acquisition's main file; do not repeat a YAML key or TOML table header.

Existing `registration.core_config = "core-registration.toml"` files continue to
work. Their `[matching]`, `[refinement]` and `[roma]` tables are read first; sparse
inline tables override only the supplied keys. The old file also controls the
core experiment's seed, threads and export chunk size. Its `methods` list is
replaced by the single selected `core_method`; its `export_native` field does
not control integrated exports. Use main `registration.export_aligned_he`.

## Precedence, paths and resume

For a normal run: **built-in defaults → main YAML/TOML → `parameters` / `--set` →
explicit method selectors**. Nested overrides preserve sibling settings.
Selectors map to the existing configuration keys: a core registration method
sets `registration.backend = "core"` and `registration.core_method`; transform
and refinement selectors set their nested core fields.

For `qc --run`, the saved main settings are inserted after defaults and before
new configuration-file overrides. `run --resume` uses the arguments supplied on that invocation;
repeat the configuration and method options to reproduce the intended settings.
Resume checks fingerprints and artifact hashes; changed relevant settings
rebuild the affected stages. Do not alter fingerprints to force reuse.

An external `core_config` set in the main YAML/TOML resolves beside that file. A new
path supplied through Python `parameters` or `--set` resolves from the current
working directory. Saved normalized paths retain their origin during QC replay.
Manifest-relative source assets keep their existing manifest path rules.

Main effective settings are saved under `run.json.parameters` and
`attempts/<id>.json`. A core registration receipt also records the fully
resolved configuration at
`provenance.inputs.core_registration.effective_config`; transform diagnostics
include it too. Missing external research files remain isolated to pair
preflight/registration, allowing unrelated AF stages to run.

## Main parameter reference

Defaults below describe the package, independently of workstation profiles.
Image dimensions and distances are pixels unless another unit is stated.

| Table / key | Default | Meaning / applicability |
| --- | --- | --- |
| `profile.shape` | `[2048, 2048]` | Required raw AF height and width, each at least 8 |
| `profile.dtype` | `"uint16"` | Current supported raw AF encoding |
| `runtime.cache_root` | System temp + `/tma-qc` | Local writable cache, disjoint from raw inputs |
| `runtime.threads` | `2` | OpenCV processing threads, positive integer |
| `runtime.decoded_cache_bytes` | `536870912` | Retained decoded raw arrays per core; 0 disables this cache |
| `runtime.tile_workers` | `4` | Concurrent tile detail workers; 1 selects serial execution |
| `storage.reserve_bytes` | `268435456` | Free-space reserve, nonnegative bytes |
| `storage.safety_factor` | `1.2` | Capacity estimate multiplier, at least 1 |
| `tile_qc.window` | `256` | Detail-measurement window side, at least 8 |
| `tile_qc.stride` | `256` | Window sampling step, positive integer |
| `tile_qc.prefilter_sigma` | `1.0` | Gaussian scale before detail measurement, positive |
| `geometry.backend` | `"provided"` | `provided` or `mist`; existing native positions take precedence |
| `geometry.downsample` | `4` | MIST input reduction factor, positive integer |
| `geometry.mist_classpath` | `""` | Explicit Java classpath for MIST |
| `geometry.timeout_seconds` | `1800` | External MIST timeout in seconds |
| `illumination.mode` | `"cidre"` | `cidre` or `none`; does not refit fields |
| `mosaic.alpha` | `1.5` | Positive exponent on native linear blend weights |
| `mosaic.chunk_rows` | `256` | Positive row-band size for bounded blending |
| `mosaic.max_pixels` | `500000000` | Positive native canvas allocation guard |
| `tissue.quantile` | `0.2` | Reference background quantile in [0, 1] |
| `tissue.mad_multiplier` | `6.0` | Nonnegative high-threshold MAD multiplier |
| `registration.backend` | `"provided"` | `provided`, experimental `sift_affine` or `core` |
| `registration.core_method` | `"geometry_sift"` | One integrated initializer per run |
| `registration.core_config` | `""` | Optional external research TOML |
| `registration.matching` | `{}` | Sparse overrides; core matching reference below |
| `registration.refinement` | `{}` | Sparse overrides; core refinement reference below |
| `registration.roma` | `{}` | Sparse overrides; core RoMa reference below |
| `registration.max_dimension` | `1536` | Legacy `sift_affine` working-view limit only, integer ≥3 |
| `registration.min_matches` | `12` | Legacy `sift_affine` match/inlier minimum only, integer ≥3 |
| `registration.ransac_pixels` | `3.0` | Legacy `sift_affine` RANSAC tolerance in its working view only |
| `registration.export_aligned_he` | `true` | Write native aligned H&E when registration succeeds |
| `qc.landmark_tolerance_pixels` | `20.0` | Provisional positive tolerance in native AF pixels |
| `qc.minimum_landmarks` | `5` | Positive minimum for the provisional landmark tolerance check |
| `artifact_qc.evidence_pairs` | `4` | Positive maximum ranked overlap evidence pairs |
| `artifact_qc.crop_size` | `256` | Evidence crop side, at least 8 |
| `artifact_qc.boundary_band_pixels` | `8` | Positive seam evidence band width |
| `report.preview_size` | `1024` | Core/pair preview limit, at least 8 |
| `report.slide_tile` | `160` | Slide montage tile limit, at least 8 |

## Core matching parameter reference

Use `registration.matching` in the main YAML/TOML or `[matching]` in the external
core TOML. These settings apply to `backend = "core"`.

| Key | Default | Meaning / limits |
| --- | --- | --- |
| `max_dimension` | `1024` | Working-view size limit, integer 64–4096 |
| `rotations` | `[0, 90, 180, 270]` | 1–24 finite degree hypotheses for geometry initializers; direct SIFT/ORB use 0 |
| `feature_view` | `"gradient"` | `gradient` or `intensity` representation |
| `transform` | `"similarity"` | Similarity (translation, rotation, uniform scale) or affine (also shear / unequal scale) |
| `max_matches` | `3000` | Match budget, integer 12–20000 |
| `ratio` | `0.75` | SIFT/ORB descriptor ratio, strictly between 0 and 1 |
| `ransac_view_pixels` | `4.0` | Positive working-view consensus tolerance, below `max_dimension` |
| `min_inliers` | `12` | Minimum spatially consistent matches, integer ≥3 |
| `min_inlier_fraction` | `0.2` | Required consistent fraction in (0, 1] |
| `min_hull_coverage` | `0.08` | Required match-hull coverage in (0, 1] |
| `max_anisotropy` | `1.4` | Allowed unequal scaling, at least 1 |
| `min_relative_scale` | `0.4` | Positive lower relative scale limit, at most 1 |
| `max_relative_scale` | `2.5` | Upper relative scale limit, at least 1 |
| `ambiguity_score_fraction` | `0.1` | Relative competing-candidate score band in [0, 1) |
| `ambiguity_view_pixels` | `8.0` | Positive working-view disagreement distance |

## Core refinement and RoMa parameters

Use `registration.refinement` / `registration.roma` in the main YAML/TOML or
`[refinement]` / `[roma]` in the optional external TOML.

| Table / key | Default | Meaning / limits |
| --- | --- | --- |
| `refinement.backend` | `"none"` | `none` or `bspline_mi` after eligible global alignment |
| `refinement.mesh_size` | `6` | B-spline mesh size, integer 2–16 |
| `refinement.iterations` | `50` | Optimization iterations, integer 1–500 |
| `refinement.max_displacement_view_pixels` | `12.0` | Positive working-view local displacement guard |
| `refinement.min_jacobian` | `0.25` | Computed-map lower Jacobian guard, strictly between 0 and 1 |
| `refinement.support_taper_view_pixels` | `16.0` | Positive working-view support taper distance |
| `roma.device` | `"auto"` | `auto`, `cpu`, `cuda` or `cuda:0` |
| `roma.coarse_resolution` | `560` | Coarse resolution, integer ≥112 and a multiple of 14 |
| `roma.upsample_resolution` | `864` | Upsampling resolution, integer ≥112 |

The optional external file also accepts `seed = 20261019` (integer 0..2³¹−1),
`threads = 2` (integer 1–32), and `export_chunk = 512` (integer 64–2048).
Thread counts and model runtimes participate in provenance. None of these
defaults are a claim of optimal settings for a biological study.
