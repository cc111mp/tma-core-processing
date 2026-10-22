# Version 0.4.1 — 2026-10-22

Repairs reproduced problems in the initial common-error QC paths. Raw TIFF
checks now reject ambiguous planes, missing/truncated segments and external OME
pixel references; the shared native-reader guard also covers H&E and correction
fields. Malformed individual inputs remain isolated during preflight.

Focus and phase measurements are stable against bright count offsets. Tile
summaries use native uint16 histograms and area-averaged FOV previews. Detail
windows expose unavailable reasons and geometric window/interior coverage.
Geometry excludes mathematically degenerate directions and bounded exact
translation symmetries without automatically changing tile positions.

Scale-normalized tile-pitch fits retain genuine fractions after count rescaling.
Reports preserve low-signal seam evidence, distinct-seam counts and global/local
fit availability; failed stages cannot display retained fits as assessed.
No calibrated artifact classifier or automatic scientific acceptance is added.

Tutorial edition 5.1 explains the review scope and measurement limits. See the
[common-error review](REVIEW_20261022.md) and [stage QC guide](STAGE_QC.md).

# Version 0.4.0 — 2026-10-21

Adds shared method selectors for `run`, `plan`, `qc` and the public Python API.
`tma-qc methods` lists choices, defaults, fixed algorithms and prerequisites;
`--json` exposes the same metadata. Repeated typed `--set` overrides and Python
parameter dictionaries use one strict resolver. `api.settings()` inspects the
configuration without opening images or creating outputs. Defaults remain the
starting point; explicit selectors override file and parameter settings.

Core matching, refinement and RoMa settings can live in the main TOML under
`registration.matching`, `registration.refinement` and `registration.roma`.
Sparse inline settings override individual keys in existing optional research
TOMLs. A pure standard-library configuration module keeps inspection independent
of imaging extras. Old configuration files and backend imports remain supported.
Invalid keys, types and numeric values fail with controlled errors; saved and
caller-owned settings are copied rather than mutated. Path origins are retained
across file, Python and CLI overrides.

Tutorial edition 5 adds method selection, tuning, precedence and reproducibility
examples. The full settings reference lists defaults, units and applicable
backends. Processing algorithms and scientific QC formulas are unchanged;
supplied geometry/transforms retain precedence, and method selection does not
automatically release image quality. The new code identity conservatively
invalidates receipts created by earlier versions.

# Registration setup documentation — 2026-10-21

Tutorial edition 4.2 starts registration from the existing AF manifest,
individual H&E core crops and a four-column pairing CSV. The beginner template
no longer references an optional landmark file. A separate advanced template
keeps optional fields blank until reviewed assets are available.

The recipe shows how to find the exact AF UID, distinguishes it from a readable
pair name, uses built-in geometry/SIFT defaults without a second TOML, and starts
with one selected AF core. Later CSV edits are explicitly re-imported from the
original AF manifest into a new paired manifest. Backend code and package
version remain unchanged at 0.3.1.

# Version 0.3.1 — 2026-10-21

Repairs defects reproduced by a second adversarial source review. Output/cache
trees must be disjoint from raw inputs. Stable SHA256 checks and pinned source
identities detect changes during decoding, processing and cache verification,
including raw tiles, H&E and external fitting assets. Changed external core
settings, model identities and residual fields invalidate historical pair views.
All manifest importers publish without replacing concurrent outputs; transfer
imports validate before publication and preserve raw archives. Canonical FOV
aliases and invalid landmark minimum counts are rejected.

Registration preserves supported floating grayscale structure and float64
contrast through working-view preparation, matching and previews. The grayscale
H&E proxy is encoding independent. Local refinement now checks exact bilinear
cell-corner and nearest-boundary Jacobians, catching folds that central node
differences missed. These checks describe the computed map, not physical folds
or biological correspondence.

QC remains finite for constant float32 corrections and extreme positive fields.
DC-centred periodicity fits avoid false harmonics at large constant intensities.
Bounded native sampling recovers reference evidence when a coarse grid misses
sparse coverage; unsampled tissue stays unknown and preview omissions are flagged.
Native AF correction/blending formulas and explicit geometry remain unchanged.
Tutorial edition 4.1 documents the updated safeguards. See
[the deep review](REVIEW_20261021.md) for reproductions, repairs and verification.

# Version 0.3.0 — 2026-10-21

Connects AF reconstruction, advisory QC and the core-registration research
workflow in one package and one manifest-driven run. Explicit `import-pairs`
CSV rows preserve AF core UIDs, H&E source labels, source hashes and section
relationships; asset paths resolve beside the CSV and the new manifest is
published without overwriting existing files. The public `tma_qc.api` provides
folder import, pair import and processing entry points.

The integrated `core` registration backend supports geometry-guided SIFT,
SIFT/ORB comparisons, optional cached-only RoMa and bounded optional B-spline/MI
refinement. Native HE-to-AF coordinates, composed residual pull maps,
candidate/fitting evidence and independent landmark evaluation stay explicit.
Existing supplied-transform and SIFT-affine workflows remain available.
Registration configuration, optional runtime/model identities and supplied
residual assets participate in cache fingerprints. Optional packages are extras;
ordinary AF processing and command help remain independent of them.

QC preserves localized seam errors with tail summaries and native worst-line
locations. Periodicity fits every supported native row/column bin in bounded
patches and checks local strips alongside global profiles. Preview averaging
cannot alias the fitted measurement. Illumination mismatch is scale invariant,
zero-denominator measurements stay unavailable, and reports expose overlap-pair
assessment coverage, exclusion reasons and per-FOV signal-span/collapse evidence.
These remain review diagnostics, with no automatic geometric repair or
scientific image-quality acceptance.

Edition 4 of the PDF/browser guide documents the study folders, Python and
terminal usage, processing-to-QC links, explicit section pairing, registration
configuration and native/independent evaluation outputs. Numerical AF correction
and native blending formulas are unchanged; code-identity changes invalidate
old processing cache keys conservatively.

# Earlier unreleased changes — 2026-10-20

Clarifies the geometry-QC contract in the methods, stage guide and tutorial:
overlap measurements and ranked evidence support review; the QC stage preserves
tile positions and FOV order. Existing advisory states and processing behavior
are unchanged.

Tutorial edition 3 leads with `python -m tma_qc` terminal commands and adds
direct Python examples for folder import and processing in scripts/notebooks.
The 12-page guide explains the installable package, module and `tma-qc`
shortcut. README/research recipes use the same existing entry points.

Refines the tutorial into edition 2, **TMA QC — Getting started**, with task-based
titles, a linked four-part contents list, consistent page alignment, larger body
and code text, and a restrained white/gray palette. The browser edition adapts
to narrow screens. Technical commands and processing behavior are unchanged.

Adds an illustrated PDF/browser user tutorial beginning with the default NAS-style
folder hierarchy, followed by manifest generation, asset assignments, a first
run, output identities and optional registration. The tutorial is linked from
the README and can be rebuilt with `make tutorial` using Chrome/Chromium.

Adds `tma-qc import-folder` to generate a draft AF manifest directly from the
observed NAS-style raw folder layouts, without a transfer database. The adapter
reuses transfer-import FOV selection and ordering, hashes selected original
tiles, preserves exact slide/lamp folder labels, and retains unresolved cores.
Read errors and detected source/folder changes prevent manifest publication.
Acquisition completeness, compatible models, native geometry and AF/H&E pairs
remain explicit review inputs. Image-processing formulas and stage outputs are
unchanged. The added source changes the package code hash; frozen runs retain
their recorded implementation and new processing uses a new run directory.

# Version 0.2.6 — 2026-10-19

Adds [stage-specific artifact review](STAGE_QC.md). Execution errors now have
quality NOT ASSESSED; batch summaries no longer convert a diagnostic crash into
an image-quality failure. Completed quality failures remain visible, and image
delivery remains independent of diagnostic execution.

Overlap records gain native bounding boxes and assessment/exclusion reasons.
Illumination QC retains p95 and the worst remaining pair beside the median.
Matched-pixel differences also expose opposing local errors that cancel in a
mean; the photometric evidence is ranked by corrected p95 differences in counts.
Labelled overlap crops, tile-midline views, coverage-aware boundary steps and
mask-normalized tile-pitch projections provide review evidence. Missing bins and
insufficient/degenerate fits remain unknown. Correction/blending formulas are
unchanged; no validated GRID probability, fold/blur mask or tissue-area ratio is
introduced.

Batch and source-first reports include per-artifact assessment records and
available evidence. Diagnostic settings invalidate QC without recomputing
compatible image stages. Nonfinite/invalid tissue-support parameters are rejected
before silently producing an empty candidate mask.

Aligns the source, standalone compatibility shim and editable package at 0.2.6;
a version-contract check prevents the checkout from reporting an older version.

# Version 0.2.5 — 2026-10-18

Readiness review closes two failure paths in source-first delivery/review:
archive-space/write failures retain verified local TIFFs and publish QC with an
explicit unavailable-archive state; missing or changed previews are omitted with
a recorded reason while other cores remain reviewable. Native archive creation
uses a checked temporary file. The numerical engine is unchanged.

Separates source-first native-image delivery from QC review. Diagnostic errors
no longer prevent export of a verified reconstruction; unavailable images still
get a stage report. A separate `qc/` HTML/JSON/CSV report distinguishes execution
errors, image-quality failures and unassessed stages. Image and report delivery
are verified separately and incomplete delivery can resume. A read-only batch
snapshot tool reviews existing jobs without changing their frozen recipes or
the numerical engine. Details: [source-first guide](source-first.md).

Adds an AF-only [source-first research recipe](source-first.md): independently
complete original NAS inputs can stage one core on SSD, run the existing engine,
and publish a verified compact archive and preview to NAS home while the bulk
raw copy continues. The recipe pins its own code identity, validates lossless
native exports, and writes a completion marker after destination verification.
It does not change the numerical engine or transfer controller. Synthetic
staging/publication failure and resume controls are included in `make check`.

Organizes the package for research use without changing the numerical methods:

- `workflows.py` declares the AF-core and AF–H&E stage sequences, dependencies
  and deferred asset identities; `context.py` owns per-unit inputs and transient
  caches. `pipeline.py` retains checkpointing, provenance and failure isolation.
- `cli.py` handles parsing/dispatch; `commands.py` contains the existing command
  handlers. Commands, options and exit-code behavior are preserved. Module help
  remains available without installing imaging dependencies.
- The short README links a research run/review guide, methods/units/limitations,
  development guide, input templates, test map and separate legacy-tool guide.
  Stale documentation about full-canvas blend scratch files is corrected.
- Ruff 0.13.2 provides consistent formatting, import ordering and basic static
  checks. `make check` runs lint, formatting verification and the complete suite.
  Generated data/results are kept outside the source checkout.

All 42 numerical function definitions in the 19 backend/QC/stage modules retain
their syntax apart from formatting. Real and synthetic output-equivalence checks
and software verification are recorded in [VALIDATION.md](VALIDATION.md).

The code hash changes with this cleanup, so existing processing cache keys are
conservatively invalidated. Saved artifacts remain intact; use a new run directory
when preserving an earlier experiment.

# Version 0.2.4 — 2026-10-18

Blends raw and selected mosaics together using row bands, sharing tile weights
and geometric denominators. Full-canvas float64 temporary files are eliminated;
the storage planner now reports zero blend scratch bytes for this backend.
Native output capacity checks, per-pixel tile addition order, float64 sums and
float32 output counts are preserved. The standalone single-mosaic API remains
available with its existing signature.

Tile-detail QC uses a bounded thread pool (four workers by default; configurable
with `runtime.tile_workers`, or `1` for serial execution). Only that many FOV
measurement jobs can be outstanding; raw cache access stays on the main thread
and output rows retain manifest order. Existing percentiles, median residuals,
filters and contrast-normalized detail formulas are unchanged. Worker failures
remain isolated within the core's tile QC stage.

# Version 0.2.3 — 2026-10-18

Reduces repeated compressed raw FOV decoding across stages using a per-core
read-only LRU cache. The default retained-array budget is 512 MiB, configurable
through `runtime.decoded_cache_bytes` (0 disables it). Entries are invalidated on
file identity changes and cleared at core boundaries, including interruption.
Raw SHA256 verification remains mandatory on every processing invocation.

Reuses geometry QC's unmasked raw overlap measurements for illumination QC when
raw file identities, native positions and sampling match. Raw support selection
and corrected measurements keep their existing calculations; missing in-memory
measurements on resume are recalculated. Performance counters are recorded
separately from scientific QC. Core processing remains serial.

Regression coverage checks pixel identity with caching disabled, bounded memory,
source replacement, invalid budgets, identical overlap measurements, invalidation
after input changes, interrupted cleanup and a resumed geometry-QC fallback.

# Version 0.2.2 — 2026-10-18

Addresses the five remaining readiness findings:

1. Position files require explicit `native_pixels`, `XY`, and `upper_left`
   declarations. Incompatible/missing frames and legacy bare arrays fail safely;
   convert reviewed native legacy geometry to a new wrapped copy explicitly.
2. Local SQLite initialization and integrity checking run under the output lock.
   Corrupt/incompatible databases and sidecars are preserved in quarantine, then
   a fresh index recovers validated stage receipts. Operational I/O/permission/
   capacity errors do not trigger destructive database recovery.
3. `plan` checks source hashes, TIFF headers, model assets/receipts, native
   geometry, applicable MIST prerequisites, writable locations and AF capacity.
   Missing prerequisites give a nonzero exit status. It performs no processing.
4. Per-unit cohort records preserve compatible historical results across
   selected-core runs. Reports label observation times and historical results,
   invalidate changed definitions, and retain partial status after interruption.
5. AF capacity estimates cover a cohort rebuild and peak serial blending scratch,
   combine requests sharing a filesystem and include configurable headroom.
   Large native AF allocations have stage guards that block before writing.

Scientific image-quality acceptance remains disabled. See the v0.2.2 section
of [VALIDATION.md](VALIDATION.md) for checks and remaining limits.

# Version 0.2.1 — 2026-10-18

This release addresses the nine confirmed findings from the preprocessing code
review. Existing valid manifests remain usable; empty FOV sets require explicit
`blocked_reasons`, and core/pair UIDs must be globally distinct.

1. Cache identity includes the ordered FOV records, preventing reuse of geometry
   and mosaics after an acquisition-sequence correction.
2. Numbered FOV imports sort by integer index. Scan-folder imports retain XY
   identity and require explicit sequence review before MIST estimation.
3. Ambiguous, incomplete, unsupported-only and empty core acquisitions remain
   visible as blocked entries. Reports include an inventory coverage CSV and
   count reconciliation, including inputs not processed in the current attempt.
4. The legacy registration checker validates complete finite similarity matrices
   and finite, correctly encoded evaluation values. Invalid evidence yields
   CHECK/ALERT rather than OK.
5. CIDRE fitting rejects reused canonical source paths and identical file hashes
   across labels. Model receipts retain verified source assignments.
6. Corrupt or mismatched cache records trigger recovery/regeneration. Invalid
   stage measurements fail within the stage boundary, preserving unrelated core
   processing. The batch landing page marks unfinished attempts immediately.
7. Compressed H&E uses temporary local SSD mappings and bounded decode segments.
   Native export uses bounded source/destination regions, including dimensions
   beyond OpenCV's single-image remap limit. Global interpolation coordinates
   make output pixels independent of processing-block partitioning.
8. Floating-point H&E is validated at every native pixel; NaNs/infinities outside
   preview samples cannot reach a completed registered export.
9. Raw uint16 inputs accept either TIFF byte order without changing counts.

Regression controls live in `tests/unit/test_review_regressions.py`,
`tests/unit/test_native_he.py` and
`tests/integration/test_review_regressions.py`, with additional contract/backend
checks in the existing suite. See [VALIDATION.md](VALIDATION.md) for measured
verification results and their limits.

Focus/fold segmentation, an accepted tissue-area denominator, CIDRE signal
preservation and dense correspondence between neighboring sections still need
scientific validation. Automatic image-quality release remains disabled.
