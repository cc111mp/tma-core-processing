# Methods and measurement meanings

This document describes the current implementation. The [validation record](VALIDATION.md)
distinguishes software/control evidence from biological validation. The processing
sequence and dependency declarations live in [workflows.py](../src/tma_qc/workflows.py).

## Data and coordinate conventions

- AF inputs are explicitly assigned raw camera FOVs: one YX plane, uint16,
  2048×2048 by default. A different compatible shape requires configuration.
- Tile positions are integer native pixels, XY axes, origin at the upper-left.
  The saved position file declares that frame. Geometry is estimated from raw
  FOVs or supplied explicitly; correction does not redefine its coordinates.
- Model groups represent compatible camera/lamp/exposure/channel acquisitions.
  Group membership and section identity require acquisition knowledge.
- AF/H&E pairs declare their section relationship explicitly; neighboring sections are common. Shared anatomy can be aligned;
  section-specific tissue is retained as a scientific uncertainty.

The [pipeline reference](pipeline.md) specifies accepted file encodings,
position/transform declarations and independent landmark contracts.

## Illumination correction

[backends/cidre.py](../src/tma_qc/backends/cidre.py) applies a frozen flatfield S
and darkfield D to raw counts I:

```text
corrected = (I - D) / S
```

Fields are validated and resized to the native FOV shape using linear
interpolation when needed. Scientific outputs are float32, preserving negative
and above-65535 values. There is no per-core gain matching, output clipping or
post-stitch smoothing in this operation.

Fitting uses the pinned external Java CIDRE runtime. The explicit fitting pool
requires at least 64 unique FOV assignments across four cores from one group;
repeated canonical paths and identical file contents are rejected. Runtime/model
receipts retain hashes. Pool size and uniqueness alone do not establish adequate
tissue diversity, physical calibration or biological intensity preservation.
Model fitting and field application are separate steps.

## Geometry and reconstruction

[backends/mist.py](../src/tma_qc/backends/mist.py) adapts raw FOVs for optional MIST
estimation using reviewed grid/sequence information. Supplied native positions
remain the default. The backend does not implement the earlier pilot's SIFT graph
refinement, and estimated geometry still requires image review.

[backends/blend.py](../src/tma_qc/backends/blend.py) reconstructs raw and selected
frames with the same integer positions and linear edge-distance weights:

```text
weight(x, y) = [min(x+1, width-x) × min(y+1, height-y)] ** alpha
mosaic      = sum(weight × tile_counts) / sum(weight)
```

Alpha is 1.5 by default. Accumulation is float64 in saved tile-position order at
each pixel, followed by float32 export. Raw and selected reconstruction share
weights and row-band denominators. A separate uint8 coverage map records where
at least one FOV was acquired. Zero output counts alone do not establish missing
acquisition or background. The complete acquisition canvas is retained.

## Local detail diagnostics

[qc/focus.py](../src/tma_qc/qc/focus.py) calculates measurements on sliding native
windows, normally 256×256 at stride 256. Each patch has a Gaussian prefilter,
Sobel gradient energy and intensity variance measured inside a border margin.
The gradient-energy/variance ratio reduces sensitivity to simple contrast
scaling. Raw percentiles and a median-filter residual MAD are also recorded.

Filtering uses centered float64 counts to prevent bright offsets from generating
numerical detail. Saved window bounds and gradient/variance interiors expose
their geometric sampling coverage and unavailable-window reasons. These
coverage values include unavailable windows, and do not describe tissue or
valid-focus area. Per-FOV and pooled tile-display percentiles use bounded
histograms of every native uint16 pixel. Tile-grid previews use area averaging;
native windows remain the measurement inputs.

These are experimental detail diagnostics. Weak texture, tissue composition,
noise and contrast can affect them. There is no calibrated defocus probability,
semantic blur mask or accepted blurred-tissue percentage. Parallel tile workers
run the same per-patch formulas and preserve output order.

## Overlap diagnostics

[qc/stitching.py](../src/tma_qc/qc/stitching.py) evaluates geometrically overlapping
FOV pairs, recording correlation, phase residuals and intensity disagreement.
Raw-defined support selects an advisory subset for before/after photometric
comparison. The same support and eligibility are used on corrected images.

Phase-correlation inputs are centered and normalized. A rank-deficient gradient
direction leaves the full 2D shift unassessed, with an explicit exclusion reason;
a bounded exact-symmetry probe also excludes integer-translation invariance
within two sampled pixels per axis. Radius, sampling step and found vector are
recorded. Passing these necessary checks does not establish a unique match;
larger or approximate repeats remain possible. A phase response alone cannot
establish two-axis geometric support.

Geometry QC uses these measurements to support review and highlight the largest
measured shifts in preview crops. It does not modify positions or FOV order.
Unavailable residual measurements remain unassessed; available measurements
require review rather than automatic image-quality acceptance.

The current support rules are provisional: minimum supported samples and raw
correlation thresholds are saved with the report. They are not acceptance limits.
Unmasked raw measurements can be reused between geometry QC and illumination QC
when source identities, positions and sampling match. Corrected measurements and
supported-pixel statistics are still calculated separately.

The assessed/candidate pair fraction records how much overlap evidence was
usable; it is not tissue-area coverage. Exclusion reasons distinguish too few
raw-supported samples, unavailable correlation and correlation below the
selection rule. Raw/selected p01, p50, p99 and dynamic spans are sampled at
identical FOV coordinates. Native extrema identify exact signal collapse;
span ratios and model gains provide additional review evidence without invented
acceptance thresholds. Relative mean mismatch uses the larger absolute mean
without a fixed one-count denominator floor; two zero means are unassessed.

Lower overlap mismatch supports improved agreement. It does not establish true
biological intensities or independent geometric accuracy, particularly when the
same image structures contributed to estimating positions.

[qc/boundaries.py](../src/tma_qc/qc/boundaries.py) measures intensity steps at
known cardinal-overlap midlines and nearby control cuts. The original summary
of per-seam medians is retained alongside summaries of per-seam p95 and maximum
steps. Ranked records retain pair IDs, native midline coordinates and the largest
measured line, so localized errors cannot disappear solely because the median
is zero. A single extreme line may be noise; these are review measurements.

[qc/periodicity.py](../src/tma_qc/qc/periodicity.py) accumulates every finite,
acquired native pixel in 512×512 source patches, with profile storage proportional
to height plus width. The raw-defined support is fixed for both images. Global
profiles and four orthogonal strips per axis are fitted at the first three harmonics of the estimated tile
pitch after linear detrending. Native profile bins preserve
the sampled spatial frequencies; display profiles alone are averaged to a
bounded size. Display bins containing an unknown native bin remain gaps.

The harmonic fraction describes explained projection variation, rather than
artifact probability. Strip evidence exposes some patterns that cancel in a
global average, but cancellation inside a strip and other spatial periods remain
possible. Anatomy can also repeat at tile pitch. Low scores cannot certify a
clean mosaic. The [stage QC guide](STAGE_QC.md) records sampling rules and limits.

Periodicity fits use normalized values before returning amplitudes in count
units, keeping explained fractions stable under count rescaling. Reported
availability includes both global and local fits, and seam measurements across
all signal strata. These are measurement counts rather than detected-artifact
counts or tissue-area fractions.

## Provisional tissue support

[stages/tissue.py](../src/tma_qc/stages/tissue.py) derives coarse support from raw
reconstruction using low-intensity statistics and seeded propagation. It exports
two native-coordinate maps: possible tissue and unknown covered area. Together
they account for acquisition coverage. Dim unclassified regions remain unknown.

The candidate map is not ground truth. It supplies no validated tissue-area
denominator, fold class or artifact-area percentage. Do not interpret missing
support as confirmed missing tissue or background.

## AF–H&E registration

[stages/registration.py](../src/tma_qc/stages/registration.py) consumes an
explicit pair of an AF core UID and an original H&E core image. `import-pairs`
hashes the H&E source and preserves reviewed section relationships. CSV asset
paths resolve beside the pairing CSV; the resulting manifest stores normalized
absolute asset paths. Folder names alone never establish correspondence.

Three routes share the same native export and independent QC:

- `provided`: reviewed native H&E-to-AF transform, supplied by the pair record.
- `sift_affine`: retained experimental structural SIFT/affine baseline.
- `core`: the integrated local `tmareg` research implementation, with one
  selected initializer: `geometry_sift`, `sift`, `orb` or `geometry_roma`.

The core route uses bounded structural working views, rotation hypotheses,
feature consensus and spatial/scale safeguards. Its default is a global
similarity transform. A separate research TOML controls matching and optional
local refinement; the main TOML selects `core_method` and `core_config`.
Provided transforms take precedence when assigned. Use a pair without a supplied
transform when evaluating a fitting backend.

Grayscale H&E uses inverse robust-normalized intensity as its structural proxy,
preserving the same feature contrast across integer, normalized floating and
positively rescaled floating encodings. Float64 inputs retain precision through
bounded reduction and normalization before structural features become float32.
RGB H&E retains the optical-density proxy. These working views never alter the
original pixels used for native export.

Optional RoMa uses explicitly installed packages and cached weights. Missing
weights or runtime dependencies block the selected method; the runner never
downloads weights or silently changes initializer. Optional SimpleITK B-spline/MI
refinement uses displacement and bilinear-Jacobian safeguards. The latter checks
the exact minimum determinant at every bilinear pull-map cell's corners and
nearest-extension boundary strips/corners. Central differences at grid nodes
can miss folds between nodes and are not used for acceptance. An ambiguous global
orientation skips local refinement. Rejected or failed local refinement retains
the global candidate with a recorded reason. These are fitting safeguards, not
validated anatomical accuracy tests.

Native export samples original H&E pixels once onto the AF canvas in bounded
chunks. `transform.json` retains the global H&E-to-AF native matrix and the full
transform representation. If a residual local pull-map is present, the matrix
alone is insufficient to reproduce the aligned image; retain its companion
`local-pull-displacement.npy` and working/native coordinate maps. Other evidence
includes candidates, fit correspondences, refinement decisions and overlays.
`geometric-overlap.tif` marks acquired geometric support, not established dense
tissue correspondence or usable tissue.

[qc/registration.py](../src/tma_qc/qc/registration.py) evaluates independently
assigned landmarks after fitting, including the residual field when present,
and reports errors in native AF pixels. Landmarks use JSON marked
`evaluation_only` / `native_pixels`, or CSV columns `he_x,he_y,af_x,af_y`.
Fitting functions never receive these evaluation annotations. Positive verified
isotropic `af_mpp` and `he_mpp` metadata can describe physical scale; pixel-size
metadata do not resolve absent anatomy. Sparse landmarks do not establish dense
accuracy, fold restoration or one-to-one correspondence between neighboring
sections. All registered images require review.

## Interpreting status

`execution` describes whether software completed, errored or was blocked.
`qc` describes quality assessment separately. Input integrity may PASS while
image-quality stages remain REVIEW. Automatic image-quality release is disabled.
The [research guide](RESEARCH_GUIDE.md) maps review questions to saved stage
outputs; the [development guide](DEVELOPMENT.md) explains how to extend a method.
