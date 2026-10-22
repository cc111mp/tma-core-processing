# Stage QC and artifact review

Design updated: 22 October 2026 (Hong Kong). Current native research registration
is integrated; additional acceptance policies remain explicitly proposed.

QC accompanies reconstruction and produces an independent review record. A
completed diagnostic with a quality failure does not discard the reconstructed
core. A diagnostic crash records execution `error` and quality `not_assessed`;
it is not evidence of a tissue defect. Missing or corrupt essential inputs,
geometry or correction can prevent reconstruction. Automatic image-quality
acceptance remains disabled.

## Pipeline and output contract

The AF runner executes these stages in order; the
[pipeline reference](pipeline.md) describes their dependencies and file contracts:

```text
index → tile_qc → geometry → geometry_qc → illumination → illumination_qc
      → mosaic → mosaic_qc → tissue
```

For an explicitly identified H&E pair, the additional sequence is:

```text
AF mosaic + original H&E → he_qc → registration → registration_qc
```

Keep native reconstruction/export and scientific QC as separate deliverables.
A technically valid image can remain available when its quality requires review.
Execution errors, measured quality problems and unassessed quality must retain
different records. Technical completion never releases an image for analysis.

## Read the stages in this order

| Stage | Measurements and evidence | Interpretation |
| --- | --- | --- |
| `index` | Source identity, expected hashes, single-plane TIFF payload and image profile | Input integrity can PASS; physical acquisition completeness still requires external evidence. |
| `tile_qc` | Native local-detail windows, saturation, zero fractions and labelled FOV grid | Contrast-normalized detail is experimental, not blur probability. |
| `geometry_qc` | Correlation, phase residuals in native XY pixels, overlap bounding boxes and centre crops | Weak texture limits the result; correspondence matches do not independently validate geometry. |
| `illumination_qc` | Same raw-supported overlaps before/after correction; mean mismatch and matched-pixel differences, median, p95, worst pair and centre crops | Excluded pairs remain unknown. Better agreement does not prove biological intensity accuracy. |
| `mosaic_qc` | Shared-window comparison, actual overlap midlines, boundary steps and tile-pitch projection fits | These measurements provide evidence for review, not confirmed artifact pixels. |
| `tissue` | Possible-tissue and unknown covered-area maps | Provisional support, not an accepted area denominator or fold segmentation. |
| `registration_qc` | Explicit AF/H&E pairs and independent evaluation landmarks | No dense correspondence or fold-restoration claim for neighboring sections. |

Geometry QC remains a check-and-review step. It records overlap similarity and
residual shifts, and prioritizes the largest measured residuals in preview crops.
Per-overlap assessments are `review` when a finite residual is available and
`not_assessed` otherwise. These are advisory review states, not calibrated
pass/fail decisions or proof of an incorrect FOV sequence. This stage leaves FOV
order and tile positions unchanged. Any geometry correction is a separate
reviewed change before rerunning reconstruction.

## Initial common-error scope

The initial review targets are uneven illumination/GRID, stitching errors, weak
local detail that may indicate focus problems, and missing/corrupt declared
inputs. The first three retain measurements and evidence for expert review;
there is no calibrated artifact classifier or automatic Excel-label decision.
General artifacts, dust and physical tissue folds are not validated detectors.

`index` verifies the files actually listed in the manifest, including fresh
SHA256 verification, one raw camera plane and usable TIFF segment tables.
Missing, zero-length or out-of-file segments cannot silently become dark
acquired pixels. Empty, truncated and ambiguous multi-plane files are rejected.
Image readers also reject external OME pixel references: a separately stored
file cannot supply pixels under the declared file's hash. Valid self-contained
OME files remain supported. Preflight checks bounded headers; full runtime
decode is still needed to detect damaged compressed data. A missing physical
core or FOV absent from the manifest needs an external expected inventory.

Local-detail windows retain their native bounds and the smaller gradient/variance
measurement interiors. Reports give the geometric union of both, including
unavailable windows; these are neither valid-focus area nor tissue area.
Constant, insufficient-variance and nonfinite windows have explicit unavailable
reasons. Centering in float64 before filtering removes a numerical dependence
on bright count offsets. It does not remove biological or noise confounding.
Per-FOV and pooled tile-display percentiles use all native uint16 counts in
bounded histograms. FOV grid previews use area averages; detail measurements
still use native windows.

Geometry phase inputs are centered and normalized before correlation. An exact
or numerically degenerate gradient direction cannot constrain a full 2D shift;
the vector remains unavailable with a reason. A bounded exact-symmetry probe
also excludes overlaps unchanged by an integer translation within two sampled
pixels per axis; its radius, sampling step and found vector are recorded. Passing
these checks does not establish a unique correspondence or rule out larger or
approximate repeats. The report lists candidate, assessed and excluded pairs.
Finite residuals still require independent review.

## Locate a finding

Overlap JSON records use `coordinate_space: native_af_pixels` and
`overlap_bbox_xyxy: [left, top, right, bottom]`, with exclusive right/bottom
limits. Phase residuals have X then Y components. Native centre crops retain
their bounding boxes and display count windows. Selected crops show large
measured residuals or large remaining photometric mismatch; they do not locate
every defect within an overlap.

Photometric evidence is ranked by the corrected overlap's p95 absolute
matched-pixel difference in counts. Per-pair mean mismatch remains available,
but equal/opposite local errors can cancel in a mean. The matched-pixel values
retain that discrepancy. They still depend on adequate geometric alignment.

Boundary-view lines show known overlap midlines, not detected artifact masks.
Each cardinal overlap is measured using adjacent eight-pixel strips by default,
plus nearby control cuts when space permits. At least eight valid line samples
are required per stratum. Diagonal and too-narrow overlaps have explicit reasons.

Support is frozen from raw counts. Raw-supported, low-signal and mixed/unknown
line samples are separate. Steps use the same raw sampled p1-to-p99 count range
for normalization before/after correction. Low signal is not confirmed background.
Anatomical edges can create steps; inspect crops, geometry and control cuts.

Seam assessment includes each measurable signal stratum, including low-signal
and mixed/unknown samples. The current artifact summary counts distinct seam
locations once and retains per-stratum counts separately. Historical receipts
with only stratum summaries are labelled as seam/stratum measurements, since
their distinct-seam union cannot be reconstructed.

The existing seam-median summary remains available. Additional
`per_seam_p95` and `per_seam_maximum` summaries preserve local tails. Up to five
`worst_seams` per stratum/image retain tile IDs, the native cut and the maximum
step's native line coordinate, ranked by maximum then p95. These are summaries
of seam statistics, not pooled line percentiles or defect-area estimates.
Isolated peaks require inspection alongside the p95 and nearby cuts.

## Read illumination evidence

The report shows assessed/candidate overlaps and `exclusions_by_reason`.
Eligibility is selected once from raw images: at least 256 supported samples
and raw correlation at least 0.8. These are provisional selection rules, not
acceptance limits. Excluded pairs remain unknown and cannot support a core-wide
claim. `assessed_pair_fraction` is pair assessment coverage, not tissue area.

Mean mismatch, matched-pixel median/p95 differences and worst-pair crops compare
identical positions and raw-selected samples. Relative mean mismatch has no
fixed one-count denominator floor; two zero means are unassessed. Check metric
availability as well as the selected-pair count.

The report also retains sampled raw/selected p01, p50, p99 and dynamic spans for
each FOV, span ratios, model-gain percentiles and exact signal-collapse flags
from native extrema. A ratio change is evidence to interpret; it has no invented
pass/fail threshold. Exact collapse means varying raw signal became constant,
not a diagnosis of its cause. Agreement alone does not establish biological
fluorescence preservation or geometric accuracy.

## Read tile-pitch projections

The periodicity diagnostic estimates pitch from nearest cardinal overlaps.
It accumulates all finite, acquired native pixels in 512×512 patches, retaining
one profile bin per native row or column. Raw-defined signal strata are used
identically before and after correction. Counts are divided by supported sample
counts; missing bins are excluded rather than zero-filled or interpolated.

A linear trend and the first three sinusoidal harmonics at tile pitch are fitted to the
native bins. Four orthogonal strips per axis supplement the whole-core profiles,
so regional patterns of opposite phase have separate evidence. Saved values
include count amplitudes, the fraction of detrended variation explained, pitch,
region coordinates, support counts and abstention reasons. Strongest local
records are ranked by the largest fitted amplitude. A low global score alone
cannot exclude local structure; patterns can still cancel inside a strip.

Fits are normalized before solving so a harmless count rescaling does not
erase a genuine harmonic fraction. Amplitudes are returned in the original
count units. Reports count global and local fits separately and link both
evidence views; neither count is an artifact count. Pitch remains unavailable
without supported cardinal-overlap geometry, including a nonoverlapping layout.

Only serialized display profiles are averaged to at most 1024 bins per axis.
Any display bin containing an unsupported native bin remains unknown. The fit
always uses native coordinates, so preview reduction cannot alias fine image
texture into a false tile-pitch fit. Sampling metadata distinguishes native fit
bins from averaged preview bins.

Rules in `periodicity-qc.json` require at least eight supported native pixels
per projection bin, 32 usable bins, two supported tile periods and eight native
samples per period. Irregular layouts and rank-deficient or ill-conditioned fits
remain unassessed. Threshold estimation uses a bounded raw-only percentile
sample. These are uncalibrated measurement rules. Biology can repeat at tile
pitch; no GRID probability, artifact-area fraction or automatic acceptance is
produced.

## Registration QC design

AF and H&E are neighboring tissue sections. The target is alignment of shared
anatomical structures at the scale required by the downstream analysis. A
one-to-one correspondence between every pixel or nucleus is not assumed.
Tears, folds, absent tissue and genuine section differences can leave regions
without a usable counterpart.

### What is implemented

| Scope | Existing behavior | Validation limit |
| --- | --- | --- |
| This preprocessing package | Supplied native transform, retained SIFT/affine baseline, or integrated core methods (`geometry_sift`, `sift`, `orb`, `geometry_roma`); native export and independent landmark evaluation | All methods require review; fit confidence is not independent accuracy. |
| Optional integrated refinement | Bounded SimpleITK B-spline/MI after the selected global core fit; displacement and bilinear-cell/boundary Jacobian checks | Experimental; improved MI and positive transform Jacobians do not establish anatomical correctness. |
| Registration acceptance | All image-quality results require `REVIEW` | No calibrated automatic acceptance rule or validated dense correspondence/fold mask exists. |

The integrated core fitter rejects insufficient or spatially concentrated consensus and
implausible transforms, flags competing orientations, and rejects unsafe local
refinement while retaining the global candidate. These are fitting safeguards,
not a validated estimate of the chance that a registration is correct. The
three-core real-data pilot (D049Ga01 A2/A3 and I683001 B8) has no independent
evaluation landmarks; all three remain `REVIEW`.

RoMa requires explicit optional dependencies and cached weights. No weights are
downloaded implicitly and no method is silently substituted. Missing optional
runtime dependencies block the selected route. Core configuration and optional
runtime/model identities are recorded in stage fingerprints.

A pair with a supplied transform uses it before fitting-backend settings. When
local refinement is present, retain the full transform, companion residual field
and coordinate maps; its global affine alone does not reproduce the result.
Evaluation landmarks are accepted as independent JSON or
`he_x,he_y,af_x,af_y` CSV and never enter fitting.

On this workstation, the original sibling checkout records the
[registration experiment design](../../tma-core-registration/docs/DESIGN.md)
and [actual validation runs](../../tma-core-registration/docs/VALIDATION.md).

### Proposed gate before downstream use

1. Define acceptable alignment error for the intended region or patch analysis.
   Use native AF pixels until calibrated pixel sizes permit physical units.
2. Establish a representative expert-reviewed reference set, including failures,
   weak shared anatomy, folds and missing fragments. Mark reproducible shared
   structures and areas without a counterpart. Keep evaluation annotations out
   of fitting. Assess annotation ambiguity, including a second reviewer on a
   subset; do not invent correspondences where none can be identified.
3. Develop an automatic screen using local structural agreement, spatial match
   support, deformation plausibility, independently fitted forward/reverse
   consistency and stability under modest resolution/sampling changes. The
   local verifier, reverse-registration test and stability test are proposed,
   not implemented checks. Inverting the existing transform is not a reverse
   registration test; consistency or stability alone cannot certify accuracy.
4. Freeze settings before evaluating on held-out slides. Keep related cores and
   repeated acquisitions together when splitting data. Report wrong accepted
   registrations divided by all accepted registrations, acceptance coverage,
   failure-detection sensitivity and uncertainty accounting for slide grouping.
   No numeric acceptance threshold or acceptable error rate has been selected.

The intended decision is to release only cases or regions supported by that
validation, route uncertain results to review, and flag clearly invalid fits.
This is a proposed policy, not a change to current QC statuses or automatic
release. Sparse landmarks establish local evidence at sampled structures;
they do not establish dense accuracy throughout a core.

### Residuals and suspected tissue folds

Residuals should measure disagreement of shared structures or modality-appropriate
features after alignment. Direct AF-minus-H&E intensity differences mainly mix
registration, modality and appearance effects. RANSAC residuals on fitting
correspondences are fitting diagnostics, not an independent local error map.
No validated local structural residual map is currently produced.

A localized mismatch is more suspicious for fold-related registration problems
when surrounding anatomy aligns, the mismatch persists under bounded refinement,
and the original section shows supporting evidence of overlapping tissue or a
crease. Darkness or saturation alone is insufficient. Tears, blur, missing tissue
and ordinary differences between neighboring sections can give similar signals.
An overly flexible warp can reduce residuals without recovering correspondence.

The proposed review sequence is:

```text
local structural disagreement → unreliable correspondence candidate
                             → inspect original AF and H&E for the cause
                             → suspected fold only with supporting fold evidence
```

Physical tissue folding and folding of the computed coordinate map are distinct.
A deformation Jacobian check addresses the latter. Positive transform Jacobians
neither excludes a physical fold nor proves correct anatomical correspondence.
Do not force overlapping or absent tissue into a one-to-one mapping.

Proposed additional records should retain the region's AF coordinate frame,
measurement scale, support/unknown status, evidence links, reason, decision-rule
version and review outcome. Keep suspected fold and unreliable correspondence
as separate findings. Validate any region exclusion against expert annotations
before reporting a trusted usable-tissue fraction. Acquired FOV coverage and
provisional tissue support are not that denominator or a correspondence mask.

### Registration QC evidence

- [Comparison of consecutive and restained sections for image registration](https://pmc.ncbi.nlm.nih.gov/articles/PMC10704256/)
- [VALIS: registration method and limitations of estimated accuracy](https://www.nature.com/articles/s41467-023-40218-9)
- [Consistency metrics: useful checks, insufficient evidence of accuracy alone](https://pmc.ncbi.nlm.nih.gov/articles/PMC2798737/)
- [Tissue-fold detection and limitations of color-based evidence](https://pmc.ncbi.nlm.nih.gov/articles/PMC3779385/)

## Follow the report

The batch report links cores, native TIFFs, coverage, stage records, overlap
crops, tile midlines and global/local projection evidence. The core page surfaces
worst-seam tail/location tables, local harmonic amplitudes and
`local-projections.png` alongside the global profiles.

- `core-qc.csv` separates execution, reconstruction completion, quality status,
  execution-error stages and completed quality-failure stages.
- `artifact-review.csv` lists targets, stages, measured-region counts and
  REVIEW/NOT ASSESSED status. REVIEW means measurements are available, not a
  confirmed defect. NOT ASSESSED is not a pass.
- `artifact-assessments.json` retains evidence links and validation limits.
- The source-first publisher includes available evidence PNG/JSON files in its
  separate `qc/` report. Original correction/blending formulas remain unchanged.

Report reconstruction completion is a saved-stage observation, not an independent
rehash of native pixels. Historical and stale observations retain their labels.
Use preflight/processing or verified export to establish current image integrity.

## Configure and rerun

```toml
[artifact_qc]
evidence_pairs = 4
crop_size = 256
boundary_band_pixels = 8
```

Changing these settings reruns diagnostics while reusing compatible geometry,
correction and reconstruction. Source/model/geometry changes follow normal
dependencies. New code conservatively invalidates prior caches: preserve earlier
experiments and use a new output directory for this reviewed version.

```bash
tma-qc plan --manifest /data/manifest.json --config /data/af.toml --out /results/run_002
tma-qc run --manifest /data/manifest.json --config /data/af.toml --out /results/run_002 --resume
```

Calibrate decisions with expert-marked seams and regions, including disagreement
cases, and evaluate on held-out slides before automated acceptance. Acquisition
compatibility, geometric accuracy and biological intensity preservation remain
separate validation questions.
