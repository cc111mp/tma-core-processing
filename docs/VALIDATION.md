# Unreleased usability polish — 2026-10-24

Configuration inspection, Python readiness and clearer command/report navigation
were checked using the existing pinned workstation runtime. Scientific formulas
and default settings remain unchanged.

- Strict full suite: **667 passed, four skipped**, with warnings treated as
  errors. The four skips require optional RoMa; SimpleITK checks ran. Ruff lint
  and formatting passed for all **130 Python files**.
- `settings` works without imaging dependencies and leaves output/cache paths
  absent. CLI and Python share typed settings and readiness checks. Core
  selection, explicit selector precedence, controlled errors and execution-only
  exit codes retain integration coverage.
- A missing legacy feature log reproduced `OK` before the fix and now yields
  `CHECK`. Empty logs also require review.
- The reviewed baseline, polished source and isolated installed wheel each
  completed all **12 stages** for one synthetic AF core and its explicit H&E
  pair. All **16 TIFF artifacts** were byte-identical across those variants;
  **18 input/configuration/asset files** remained unchanged.
- The final wheel's **67 package Python files** exactly match the tested source.
  Its installed `settings`, `plan` and `run` commands work outside the checkout.
  No shared packages were installed or upgraded.
- All six edited QC calculation modules have identical syntax trees after
  excluding docstrings. The 26 processing/configuration modules checked against
  the baseline retain identical bytes. Documentation links and Python examples
  were checked.

These controls establish software behavior and preservation of the supplied
synthetic reconstruction. They do not calibrate biological artifact decisions,
dense AF–H&E correspondence or real MIST/CIDRE estimation. Source identity changes
conservatively invalidate older checkpoint reuse; prior artifacts are preserved.

# Common-error audit, version 0.4.1 — 2026-10-22

The [common-error review](REVIEW_20261022.md) records reproduced input,
focus/geometry arithmetic, sampling and measurement-availability defects and
their repairs. No calibrated artifact classifier or acceptance threshold was
added.

- Final base `make check` passed Ruff, formatting for **126 Python files** and
  **616 tests**; four optional RoMa tests were skipped. The optional registration
  environment passed **all 620 tests with warnings treated as errors**.
  Strict checking also exposed unclosed image handles in legacy montage tests;
  those test fixtures now close their images explicitly.
- Independent cross-review found and closed a retained-failed-mosaic report
  inconsistency and an oblique repetitive-texture ambiguity. Final focus/report
  and geometry/TIFF review receipts have no remaining actionable findings in
  their stated scopes, and their source hashes match the final files.
- **182 valid TIFF combinations** preserved exact pixels across native readers.
  Malformed/empty/multiplane TIFFs, missing/truncated segments and external OME
  pixel references are rejected. Fresh-hash corrupt raw/H&E/model inputs fail
  locally while unaffected acquisitions continue. Header preflight intentionally
  leaves compressed payload validation to runtime decoding.
- Public Python and terminal workflows completed **12 stages** each with
  identical effective settings. Identical replay reused all 12 stages. The
  **0.4.1 wheel contains 67 package modules**, supports help/settings without
  imaging imports, and completed all 12 stages outside the checkout.
- All **10 native TIFF hashes** matched across Python, terminal, wheel and the
  saved 0.3.1 reference run. Five referenced source/configuration files remained
  unchanged. Each final run verified **35 artifact hashes and 71 local report
  links**. Input verification and advisory scientific quality remain separate.
- Tutorial edition **5.1** retains **19 A4 pages**, seven valid contents links,
  checked CLI/API/JSON/TOML/CSV examples and readable PDF text/fonts/rendering.
  Desktop footer clearance is at least 29.9 pixels; a 500-pixel viewport has no
  page-level horizontal overflow. Revised QC and pairing pages were visually
  reviewed.

Final package source identity:
`9917f70cfe8c1aad37ce38a31e0f454521f5cc5481a3de2a2b74e2e01c982db2`.
Final logs, wheel, source/artifact hashes, numerical controls, independent review
receipts and guide checks:
`/mnt/4TB_hdd/PhoMedics/qc/work/common_error_audit_20261022/verification.json`.

These checks establish bounded software behavior. They do not establish expert
Excel-label agreement, biological artifact sensitivity/specificity, a validated
blur/fold mask or physical acquisition completeness. Image-quality results
remain advisory and require study-specific validation.

# Method selection and settings, version 0.4.0 — 2026-10-21

Shared CLI/Python method selectors, typed parameter overrides, sparse inline
core settings and their precedence were verified on synthetic inputs. Scientific
processing formulas and the default parameter values remain unchanged.

- `make check` passed lint, formatting for **118 Python files**, and **496 tests**;
  four RoMa tests were skipped in the base environment. The environment with
  optional registration dependencies passed **all 500 tests**, without skips.
- Independent review passed **96 focused tests with warnings treated as
  errors**, plus pure standard-library inspection and synthetic resume probes.
  It found and closed two interface issues: full settings dictionaries could
  not round-trip as parameter overrides, and extreme numeric inputs could
  escape validation as `OverflowError`. No actionable findings remained in the
  reviewed settings, path, provenance and cache contracts.
- Actual public Python and terminal runs each completed all **12 stages** with
  identical effective main settings. Full settings dictionaries round-tripped;
  all 12 Python stages were reused on identical replay. All **10 native TIFF
  hashes** matched between interfaces and the saved 0.3.1 default run. All five
  referenced input/configuration files remained unchanged.
- A separate sparse-override probe retained the external core TOML's matching
  resolution. Changing only inline `matching.ratio` reran registration and its
  QC while caching the other 10 stages; the next identical replay cached all 12.
- The **0.4.0 wheel contains 66 package modules**. Help, method listing and
  settings/core resolution worked outside the checkout without site packages
  or imaging imports. The wheel also completed the 12-stage synthetic workflow
  outside the checkout with the same source code identity.
- Tutorial edition **5** has **19 A4 pages**, seven valid contents links and
  validated CLI/API, JSON, TOML and CSV examples. PDF text, fonts and rendering
  passed. New pages 17–18 were visually reviewed. Desktop footer clearance is
  at least 29.9 pixels across the guide; the 500-pixel browser viewport has no
  page-level horizontal overflow.

Final source identity:
`fd02abc9a187cda1205ef35ac649f8bae7cccdf5065b091e41c0654a209dc4f5`.
Logs, wheel, workflow/source hashes, guide checks and final verification record:
`/mnt/4TB_hdd/PhoMedics/qc/work/settings_polish_20261021`.
Independent review evidence:
`/mnt/4TB_hdd/PhoMedics/qc/work/settings_interface_20261021/independent-settings-review.json`.
These are software and configuration checks; real paired-section performance
and scientific image-quality acceptance require study-specific validation.

# Registration setup recipe — 2026-10-21

Tutorial edition 4.2 reduces the beginner registration setup to a prepared AF
manifest, individual H&E core crops and a four-column pairing table. A synthetic
study executed the shipped basic template and built-in geometry/SIFT defaults:
CLI preflight passed and all **12 stages completed** with no supplied transform,
landmark file, second research TOML or optional model package. The advanced
template also imported successfully with its optional asset fields blank.
Independent landmark accuracy was explicitly unavailable rather than required
for fitting. All nine copied input/source files were unchanged.

The rebuilt guide retains 17 A4 pages and six valid contents links. CLI commands,
UID-listing Python, public API calls and JSON/TOML/CSV examples validate. PDF text,
fonts and rendering passed; visual review covered the revised setup/run pages.
Desktop content/footer clearance is 71.9 pixels on page 14, 103.0 on page 15 and
46.3 on page 16; the minimum across the guide remains 29.9 pixels. The observed
500-pixel browser viewport has no page-level horizontal overflow.

This update changes documentation and CSV templates only. Package code identity
is unchanged from the 411-test audit below; its full suite was not repeated for
these edits. The minimal-recipe proof, template hashes and PDF evidence are under
`/mnt/4TB_hdd/PhoMedics/qc/work/registration_setup_20261021`.

# Deep correctness audit, version 0.3.1 — 2026-10-21

The second adversarial review reproduced and repaired additional defects in
input/output boundaries, concurrent source and manifest changes, native
registration safeguards, supported floating images, QC arithmetic, sparse
coverage and historical report freshness. Findings, their reproductions and
operational assumptions are listed in [the deep review](REVIEW_20261021.md).

- Final `make check` passed lint, formatting for **113 Python files**, and
  **407 tests**. Four optional RoMa tests were skipped in the base environment.
  The full optional registration environment passed **all 411 tests**, with no
  skips or failures. The suite adds 73 cases to the earlier 338-case build.
- Focused numerical, source-race and registration controls also passed with
  warnings treated as errors. Independent cross-review reproduced and closed
  the descriptor-binding race and checked the repaired publication, historical
  identity, source-pin and scientific-measurement paths.
- Actual cached RoMa and actual SimpleITK B-spline/MI each completed all 12
  stages using the final source. RoMa's synthetic held-out median error remained
  0.053 native AF pixels. The applied B-spline field's new exact bilinear/boundary
  minimum Jacobian was 0.953, with no folded cells or edge strips. A separate
  reachable tapered-field control correctly rejected 16 folded cells that the
  earlier central-node check missed. These are software/method controls, not
  biological validation.
- A final AF-only rerun completed all nine stages and reproduced **all 13
  earlier native TIFF hashes**, with raw sources unchanged. The paired Python
  workflow completed all 12 stages, verified 35 artifact hashes and 71 local
  report links, preserved eight input/source files and reused all 12 stages on
  resume.
- The 0.3.1 wheel built with all 64 package modules. Help/API import worked
  without site packages, and a complete 12-stage synthetic study ran outside the
  checkout with matching installed version metadata.
- Tutorial edition 4.1 retained all 17 pages, valid examples and six contents
  links. PDF text/fonts and rendering passed; desktop content/footer clearance
  remained at least 29.9 pixels and the narrow browser had no page-level overflow.

Final logs, source and artifact hashes, exact runtime records, executable
before/after probes, cross-review records and PDF checks are under
`/mnt/4TB_hdd/PhoMedics/qc/work/deep_audit_20261021`.
`verification.json` identifies the final checked source. Earlier records below
remain historical; they do not replace this final audit.

No remaining confirmed actionable defects were found within the reviewed
contracts. This does not certify the entire codebase bug-free. Inputs/fitted
assets must be immutable, and writable output/cache trees application-owned;
the guards are not a transactional filesystem snapshot. Real paired-section
validation and biological image-quality acceptance remain pending.

# QC polish and integrated registration — 2026-10-21

Version 0.3.0 connects explicit AF/H&E pairing to the integrated
`tma-core-registration` research methods. The final checks used disposable
synthetic inputs and saved local metadata; raw NAS archives and transfer jobs
were not changed.

- `make check TMA_PYTHON=/mnt/4TB_ssd/conda/envs/mggm-mil/bin/python`
  passed Ruff lint, formatting for **104 Python files**, and **335 tests**.
  Three RoMa prerequisite tests were skipped because that environment has no
  `romatch`. The full suite in the research environment, with optional RoMa and
  SimpleITK dependencies available, passed **all 338 tests**.
- A saved end-to-end study imported an explicit pairing CSV through the public
  Python API, passed CLI preflight, completed all **12 stages**, verified **35
  artifact hashes** and **71 local report links**, and reused all 12 stages on
  resume. All eight source/input files remained unchanged. Original AF and H&E
  source identities were retained. The geometry-SIFT held-out evaluation and
  native export completed; all image-quality states remained REVIEW.
- A separate AF-only rerun completed all nine stages and reproduced the earlier
  hashes of **all 13 native TIFF artifacts**. Raw source hashes were unchanged.
  Numerical illumination correction and native mosaic output were preserved.
- The 0.3.0 wheel built successfully with all 64 package modules. Help and the
  public API imported without site packages. The extracted wheel ran a complete
  12-stage synthetic study outside the source checkout with matching package
  metadata and no execution errors or blocked stages.
- Controlled QC regressions cover localized seam tails, individual worst lines,
  fine-texture aliasing, opposite-phase local periodicity, proportional signal
  scaling, unavailable zero-denominator comparisons, frozen raw selection,
  excluded-overlap coverage and corrected signal collapse. These tests establish
  measurement behavior, not acquisition-specific scientific thresholds.
- Actual cached RoMa fitting and actual SimpleITK B-spline/MI refinement each
  completed all 12 stages on synthetic pairs. RoMa's held-out median error was
  0.053 native AF pixels (p95 0.074). The applied local refinement had a minimum
  sampled Jacobian of 0.964 and no sampled folded cells. These saved model runs
  preceded the final landmark-parser hardening; the final full optional suite
  covers that parser and the unchanged registration methods. Synthetic accuracy
  and sampled Jacobians do not establish biological or dense correspondence.

Independent source review found and closed input and measurement defects:
per-seam median aggregation hid localized errors; stride sampling aliased native
texture; global projections could cancel opposite phases; a one-count denominator
floor rewarded severe signal compression; fitting CSVs could masquerade as
evaluation landmarks; malformed residual fields could truncate or extrapolate;
invalid cached model payloads could trigger an upstream download; repeated
landmarks could satisfy a point-count requirement. Regression checks cover each
repair. Pair import also rejects concurrent source changes and protects existing
output manifests.

The final independent illumination review passed 38 focused tests and additional
numerical/reporting probes. The final independent registration/import review
passed 71 focused tests and 20 additional probes, including duplicate coordinates
and IDs, native/residual coordinate domains, malformed model caches, and source
mutation. **No actionable findings remained in those reviewed changes.**

Tutorial edition 4 has **17 numbered A4 pages**, six working contents anchors,
embedded TrueType fonts, valid extracted text and no PDF render warnings.
Documented CLI commands parse, Python examples match public API signatures, and
JSON/TOML/CSV examples validate. Desktop checks found no code overflow and at
least 29.9 pixels of content-to-footer clearance. The observed 500-pixel browser
viewport had no page-level horizontal overflow. The rendered guide was visually
reviewed, including Python usage, explicit pairs and independent registration QC.

Logs, source identities, model-run records, saved synthetic inputs/results,
independent review records and PDF renders are under
`/mnt/4TB_hdd/PhoMedics/qc/work/qc_polish_registration_20261021`.
The separate actual model records are under
`/mnt/4TB_hdd/PhoMedics/qc/work/registration_integration_20261021`.

Geometry QC still checks and flags; it never reorders FOVs or repairs positions.
Illumination and mosaic evidence remains advisory. No real paired-section
benchmark or new biological acceptance threshold was established in this build.

# Python tutorial verification — 2026-10-20

Edition 3 uses `python -m tma_qc` for terminal commands and documents the existing
`import_raw_folder` and `run_pipeline` Python functions. The two tutorial Python
examples were executed with local synthetic paths: folder import discovered one
core with two FOVs, and the prepared demo core completed all nine AF stages.
Running that same core through the Python module completed the same nine stage
states and produced identical hashes for all **13 native TIFF artifacts**.
Module and installed shortcut help matched, and the three processing/import
command blocks parsed against the current CLI. These checks exercise software
usage; they do not validate scientific performance on new acquisitions.

The rebuilt PDF has 12 numbered A4 pages, valid extracted text, embedded
TrueType fonts and no render warnings. All four contents links resolve.
Desktop browser measurements found no code overflow and at least 30 pixels
between content and footers; the 500-pixel narrow viewport had no page-level
horizontal overflow. Updated manifest/Python pages were visually inspected.
Sources, synthetic examples, output comparisons and layout evidence are under
`work/tutorial_python_20261020`. This revision changes documentation only.

The geometry-QC clarification retains all 12 pages without overflow; the updated
asset page has 71 pixels of content-to-footer clearance. Its extracted PDF text
includes the check-and-review contract. Existing processing and flag logic are
unchanged. Layout evidence: `work/tutorial_python_20261020/geometry-qc-clarification.json`.

# Folder-import verification — 2026-10-20

Tutorial edition 2 retains all four command-containing code blocks from edition 1.
The shortened manifest-inspection script passes Python syntax validation. All
11 A4 pages retain valid numbering, text extraction and embedded TrueType fonts;
rendering produces no glyph warnings. Browser measurements confirm aligned
page margins, no code overflow and at least 51 pixels between content and footers.
The narrow browser layout was checked at an observed 500-pixel viewport without
page-level horizontal overflow. PDF renders and before/after sources are under
`work/tutorial_design_20261020`.

The accompanying 11-page PDF/browser tutorial was checked against the current
CLI and input contracts. Five documented raw layouts were exercised with local
fixtures: the default acquisition tree, repeated slide folders, lamp/scan XY
folders, and lamp-less stitch/scan folders. All produced the expected candidate
FOV assignments. The documented commands parsed, native XY geometry loaded, and
the TOML profile validated. The PDF was rendered for visual inspection; all 11
numbered pages retained text without replacement glyphs or content/footer
overlap. Tutorial verification artifacts are under
`work/tutorial_pdf_20261020`. No raw NAS scan was needed for these checks.

`make check TMA_PYTHON=/mnt/4TB_ssd/conda/envs/mggm-mil/bin/python` passed
Ruff lint, formatting for **87 Python files**, and **244 tests**. Fifteen added
controls cover direct NAS-style AF import, exact source labels, relative-path
identity agreement with transfer import, repeated acquisitions/lamps, numeric
tile order, unreviewed scan sequence, empty/unsupported/ambiguous cores, selected
source hashes, supplied model mappings, source/folder mutation, unreadable
inputs, symbolic links and output preservation. The command also runs without
image-library site packages.

A synthetic NAS-style tree was imported, supplied with explicit model/geometry
assets and a section pair, and processed through AF reconstruction and H&E
registration with no execution errors or blocked stages. This verifies interface
compatibility; it does not establish real acquisition completeness, geometry
accuracy, model compatibility or biological AF/H&E correspondence. No full NAS
raw import or new real-image processing was performed for this change.

# Build verification — 2026-10-19

## Version 0.2.6 stage QC review and prepared-core processing

- Baseline `make check` passed 208 tests before editing. The reviewed version
  passed lint, formatting for 84 Python files, and **229 tests**. Added controls
  cover diagnostic/quality separation, unknown constant overlaps, a bad seam
  hidden by a median, opposite local errors hidden by means, native displacement,
  missing acquisition strips, known tile-pitch patterns, tissue-occupancy bias,
  unsupported/rank-deficient projections, invalid QC/support parameters and
  version consistency between the checkout shim, source package and project.
  Editable installation metadata now reports 0.2.6; stale generated 0.2.5 source
  metadata was preserved outside the checkout. Numerical run identity is unchanged.
- Read-only operational preflight rechecked **751 source hashes and headers**
  for the existing **16-core / two-group** prepared AF subset. Only A5's supplied
  geometry is marked reviewed. No new CIDRE fitting, MIST estimation or AF/H&E
  registration was run; acquisition compatibility remains unverified.
- A5 and I683001 B8 first completed all 18 AF stages. A subsequent complete
  subset run reused those compatible pilot stages and completed all **144 AF
  stage records**, with no execution errors or blocked stages. Processing used
  independently staged local SSD raw inputs, not another original-NAS read.
- All **895 native TIFFs** were independently rehashed and matched the earlier
  recorded output hashes. All four final products per core also matched the
  pixel hashes of their earlier lossless exports. Correction and native blending
  outputs therefore remain unchanged on this subset. All **735 offline report
  links** resolved; a desktop browser screenshot and native evidence views were
  inspected for the B8 pilot.
- The new review publication has **695 validated links**, including 16 existing
  NAS archive references with the expected sizes. Native archives retain their
  prior destination-verification dates; this review does not claim a new network
  archive rehash. New QC HTML/JSON/CSV/PNG files are delivered and independently
  verified separately, with progress in `publication-status.json`.

Code, pre-edit snapshot, tests, exact manifest/configuration, preflight, current
processing logs, native equivalence and publication receipts:
`/mnt/4TB_hdd/PhoMedics/qc/work/stage_qc_review_20261019`.
Full native results and current reports:
`/mnt/4TB_ssd/PhoMedics/qc/data/stage_qc_review_20261019/results`.

This establishes execution, output identity and inspectable diagnostic evidence.
It does not validate GRID classification, fold/blur segmentation, absolute
fluorescence preservation, a tissue-area denominator or whole-dataset geometry.
Image-quality acceptance remains REVIEW; automatic release is disabled.

# Earlier build verification — 2026-10-18

The package is an executable review workflow. Software completion and scientific
image-quality acceptance are separate throughout the stage records and reports.

## Current-data readiness review

- `make check` passed lint, formatting for **78 Python files**, and **208 tests**.
  Four new regression cases reproduced and then closed archive-capacity/write
  failures and missing/changed preview failures. A failed ZIP now leaves verified
  local TIFFs available and delivers the QC report, with an `archive_export`
  error, a false `native_archive_available` flag and a nonzero CLI status.
  Batch review omits invalid previews with a recorded reason and continues
  reporting other cores. Scientific image quality is not failed by these errors.
- Fresh read-only preflights verified hashes and TIFF headers for **98 staged
  raw FOVs**: D049Ga01 A2 (42) and I683001 B8 (56). Both were execution-ready;
  the checks used existing local raw snapshots and created no processing outputs.
- All **16 prepared cores / 751 FOVs** retained valid native geometry declarations
  and source-completeness attestations; both groups' frozen field hashes matched
  their receipts. This does not independently validate geometry accuracy or the
  physical acquisition's completeness.
- The workstation environment, required TIFF codecs and local capacity were
  available. The active batch's numerical-engine hash and frozen source recipe
  remained unchanged. Runtime stages continue checking each selected source.

Evidence: `/mnt/4TB_hdd/PhoMedics/qc/work/code_readiness_review_20261018`.
This establishes current prepared-AF execution readiness. Additional acquisitions
still need input/geometry/model preparation; explicit H&E pairs and independent
evaluation are required for registration. Scientific acceptance remains manual.

## Independent native output and QC delivery

The source-first recipe now exports a valid reconstruction independently of
diagnostic execution. Every expected stage is represented in separate HTML,
JSON and CSV review records. The numerical engine and active workers' frozen
recipes remain unchanged.

- `make check` passed lint, formatting checks for **78 Python files**, and
  **204 tests**. Twelve added controls cover five diagnostic crash locations,
  completed quality rejection, failed reconstruction, corrupt native artifacts,
  failed raw staging, QC-record tampering, partial report delivery/restart and
  read-only batch review with unrun cores.
- A real **D049Ga01 A2** replay used its existing SHA256-verified local raw
  snapshot, without rereading original NAS raw. All nine stages matched the
  earlier scientific metrics, findings and execution/QC states. All four native
  exports matched both decoded-pixel hashes and encoded TIFF byte hashes.
- Nine delivered native/report files were independently rehashed in a dedicated
  local validation publication. Browser checks exercised stage filters, preview
  loading and desktop layout. This establishes delivery behavior and output
  equivalence, not new scientific artifact-detection accuracy.

Evidence and the exact validation recipe:
`/mnt/4TB_hdd/PhoMedics/qc/work/output_qc_separation_20261018`.

New jobs use the revised recipe; existing frozen workers keep their original
behavior. The separate batch snapshot tool can review their saved observations
without changing processing or publication status.

## Source-first AF recipe

The standalone [source-first recipe](source-first.md) adds staging and publication
around the existing v0.2.5 engine. The engine hash remains
`8023dc19ccf56690026b1b1c0ac04d4ad4ed845daccaa4918c98a4afb675cc3b`.

- `make check` passed Ruff lint, formatting checks for 77 Python files, and
  **192 tests**. Ten new synthetic controls cover pinned source staging, failed
  hashes, completeness attestation, AF-only pair handling, native float32/uint8
  export, replay assets, frozen job conflicts, restart and corrupt publication.
- A real pilot streamed all **49 original NAS FOVs** of D049Ga01 A5 directly to
  its own SSD workspace. The **249,619,658 bytes** matched the frozen expected
  source hashes and independently checked staged hashes. That initial source
  read/staging took **431.6 seconds**, excluding later processing/publication.
  No home raw-copy progress condition was used to authorize the selected core.
- All nine processing stages completed. All stage metrics, findings and QC
  states matched the earlier A5 result. All **61 TIFF/PNG stage artifacts** were
  byte-identical to the v0.2.4 full-run baseline. Source/path provenance changes
  are intentional and are recorded separately from image equivalence.
- Final 12012×12180 corrected float32 and three uint8 mask/coverage exports were
  compressed losslessly and checked against every native pixel bit. The archive
  is 478,877,825 bytes; full intermediate checkpoints remain on local SSD.
- The destination archive was independently SHA256-checked and publication
  completed at **2026-10-18 10:58:13 UTC**. The NAS home review page is
  `/mnt/pho-nas/home/data/TMA_preprocessing/2026/source_first_20261018/D049Ga01-original-A5-AF/index.html`.
- The active transfer controller scripts and configuration remained unchanged.
  The bulk copy continued independently. This pilot establishes an alternative
  data route, not new illumination, focus, tissue-mask or registration accuracy.

Evidence, source receipts, exact frozen recipe, checks and output equivalence:
`/mnt/4TB_hdd/PhoMedics/qc/work/source_first_20261018`.

Local workspace:
`/mnt/4TB_ssd/PhoMedics/qc/data/source_first_20261018/D049Ga01-original-A5-AF`.

### Prepared source-first batch

A subsequent read-only audit of the completed selected inventory found 1,139
recognized acquisition/core/lamp records, including 799 with a candidate FOV set
and 340 without a supported candidate set. Original/repeat acquisitions are
separate records. Candidate numbering and folder discovery do not establish
complete acquisition, correct core assignment or reviewed source roles; 572
records have multiple eligible folder variants. The frozen research source
selection takes precedence over generic folder preferences.

The existing 16-core, 751-FOV research subset matched the exact frozen expected
source sets. Its two acquisition-specific illumination models and explicit native
positions were copied into a dedicated batch workspace. Only A5's geometry is
marked reviewed; all other supplied poses and all image-quality outputs remain
subject to review. This is an execution-ready research subset, not validated
whole-dataset preprocessing.

At 2026-10-18 11:26:45 UTC, a sequential worker was launched for the other 15
prepared cores, preserving the earlier A5 publication. Failure-isolation and
restart controls using synthetic child receipts passed before launch: one failed
core did not prevent the next core from running, a restart retried the failed
core, and already successful work was skipped. These queue controls supplement
the recipe's image/export controls; they do not establish scientific accuracy.

Frozen plan, source manifest, models, poses, worker, logs and live `status.json`:
`/mnt/4TB_hdd/PhoMedics/qc/work/source_first_batch_20261018`.
New verified per-core publications go under
`/mnt/pho-nas/home/data/TMA_preprocessing/2026/source_first_batch_20261018`.
The worker's launch does not mean the remaining cores have finished; read the
live status for completion and errors. The transfer controller is unchanged.

## Version 0.2.5 research-code cleanup verification

- `make check` passed Ruff lint/import checks, formatting verification for all
  **75 Python files**, and **182 pytest checks**. A new subprocess control checks
  that module help works with site packages disabled. The **39 legacy checks**
  also passed separately under system Python.
- An audit against the saved v0.2.4 source confirmed identical abstract syntax
  for all **42 numerical functions across 19 backend, QC and stage modules**,
  ignoring formatting and docstrings. Imports were organized and unused imports
  removed; orchestration and command handling were extracted into named modules.
- A fresh real pilot processed **D049Ga01 A2 (42 LZW FOVs)** and **I683001 B1
  (56 uncompressed FOVs)** using the existing local raw snapshots, native poses
  and frozen fields. All 18 stage records matched v0.2.4 in execution/QC state,
  measurements and findings. All **140 artifacts** matched their prior SHA256s,
  including 116 TIFFs, six PNGs and 18 JSON files. All new artifacts were
  independently rehashed; all 89 offline report links resolved.
- The same fixed synthetic inputs were run through both the saved v0.2.4 code
  and the cleaned code. All **21 AF/H&E stages** matched their scientific records,
  and all **57 artifacts** matched byte hashes, including native aligned H&E and
  correspondence/coverage products. All 67 report links resolved.
- Documentation links were checked against the checkout. The main README now
  describes the actual package tree and points to research, methods, development,
  examples and legacy-tool guides. Full-canvas blend-scratch descriptions were
  updated to the current row-band implementation.

The source backup, numerical-code audit, check logs, comparison scripts and
`output-equivalence.json` are saved under
`/mnt/4TB_hdd/PhoMedics/qc/work/research_cleanup_20261018/`.
These checks establish refactor equivalence on the tested inputs. They do not add
biological focus/fold calibration, accepted tissue-area segmentation or validated
dense neighboring-section correspondence. No real H&E dataset or new CIDRE/MIST
estimation was run for this cleanup. Numerical methods and scientific acceptance
criteria are unchanged. The new code identity invalidates old cache keys by design.

## Version 0.2.4 performance verification

- **181 pytest checks passed**, including paired row-band blending against an
  independent full-canvas formula at four band sizes, preservation of negative
  and high counts, hidden nonfinite inputs, bounded worker reads, serial/parallel
  output equality, worker error isolation and the output capacity guard.
- The same **16 cores / 751 native FOVs** completed all 144 AF stages in a fresh
  rebuild without errors or blocked stages. The stage receipt span fell from
  **345.29 s to 249.19 s**, **27.8% less time** (1.39× faster than v0.2.3,
  3.34× faster than the original v0.2.2 batch). Wall time including reports was
  249.78 s. Tile QC fell from 101.10 s to 32.69 s; paired reconstruction fell
  from 124.08 s to 96.19 s.
- All **1,087 stage artifact hashes** matched v0.2.3, including 895 TIFFs,
  48 PNGs and 144 JSON files. Every new artifact was independently rehashed.
  All 144 stage measurements, execution/QC states and findings matched, and
  all 397 offline report links resolved.
- Measured peak process RSS fell from **4.19 GiB to 2.53 GiB**; RSS includes
  native memory-mapped pages and is not a configured memory bound. Full-canvas
  blending scratch files are eliminated; retained native output size is
  unchanged. The raw-array cache stayed within 512 MiB and was cleared after
  each core. Active worker arrays and band buffers remain additional memory.

Evidence is saved under
`/mnt/4TB_hdd/PhoMedics/qc/work/preprocessing_speed2_20261018/`, including the
previous source backup, profiler, worker probe, tests, benchmark runner, complete
artifact comparison and review page. Cores remain sequential; tile QC now uses
four workers, with the same OpenCV thread setting of two. The worker probe gave
identical values and a 3.5× kernel speedup at four workers on eight warm FOVs;
that isolated result excludes decoding, storage and pipeline overhead.

Both real batches use the same local SSD inputs, native geometry and frozen
fields. They exclude new CIDRE fitting, MIST estimation and AF–H&E registration.
These are separate observed runs; OS cache, storage state and machine load can
differ. The timings are not a full-archive NAS throughput guarantee. No scientific
acceptance criteria changed; automatic image-quality release remains disabled.

## Version 0.2.3 performance verification

- **169 pytest checks passed**, including ten new cache, identity, interruption,
  resume and numerical-equivalence checks. Cache-disabled synthetic processing
  produces the same TIFF/PNG bytes and scientific measurements.
- A fresh rebuild of the previous **16 real cores / 751 native 2048×2048 FOVs**
  completed all 144 AF stages without errors or blocked stages. The stage receipt
  span fell from **831.62 s to 345.29 s**, about **2.4× faster** (58.5% less time).
  Optimized wall time including final reports was 345.94 s.
- Every stage measurement, execution/QC state and finding matched v0.2.2.
  All stage artifact hashes matched, including corrected FOVs, native raw and
  selected mosaics, coverage/support maps and previews. Every optimized artifact
  was independently rehashed after processing; offline report links resolved.
- The retained raw-array cache never exceeded its configured 512 MiB per-core
  budget and was empty after each core. This is a cache budget, not a limit on
  total process memory. Unmasked raw overlap reuse also matched independent
  recomputation, including changed-source/geometry/sampling invalidation and
  resume without in-memory measurements.

Evidence, timings, comparison scripts, test logs, original source backup and a
review page are saved under
`/mnt/4TB_hdd/PhoMedics/qc/work/preprocessing_speed_20261018/`.
The comparison uses the same local SSD raw snapshots, native positions, frozen
fields and thread setting as the earlier batch. Separate runs can differ in OS
cache, storage state and system load; this observed speedup is not a full-archive
throughput guarantee. Stage receipt intervals include I/O, checksum and
checkpoint overhead and exclude report generation. Both runs exclude new CIDRE
fitting, MIST geometry estimation and AF–H&E registration. Scientific acceptance
criteria are unchanged and automatic image-quality release remains disabled.

## Version 0.2.2 readiness fixes and verification

- **159 pytest checks passed**, including 32 added contract, failure-recovery,
  preflight, cohort-reporting and capacity controls over v0.2.1. The 39 legacy
  non-image checks also passed under system Python.
- Incompatible units/axes/origins, missing frame declarations, legacy bare
  arrays and malformed position rows are rejected. An incompatible-frame core
  cannot reach a completed mosaic; an unrelated core remains processable.
- An intentionally corrupted whole SQLite database was preserved in quarantine
  while all 21 valid synthetic stage receipts recovered through verified cache
  reuse. A competing output lock prevents recovery before lock acquisition;
  simulated operational I/O errors do not quarantine the database.
- Preflight detects missing referenced assets, changed source hashes,
  unreviewed acquisition sequence, missing MIST classpath inputs, invalid
  storage settings and insufficient AF batch capacity. It does not create
  output/cache directories or decode raw FOVs. A shared-filesystem control
  checks that output and scratch requests cannot each consume the same free
  bytes independently. Low-space blending/correction stops before allocating
  buffers or writing corrected FOVs, without stopping an unrelated core.
- Separate selected-core runs accumulate compatible observations, including
  section-pair results and observation times. Changing unselected manifest
  order, geometry or model metadata makes the prior result STALE. Interruption
  replaces selected completion with partial status while retaining peer history;
  malformed cohort completion metadata is also treated as stale.
- A real run processed all **49 native 2048×2048 FOVs of D049Ga01 A5**, using
  the existing frozen field and reviewed native positions. The old bare-array
  geometry was preserved; a separate explicit-frame copy retained every numeric
  coordinate and the original file hash. Operational preflight passed, all nine
  AF stages completed, and all nine reused their cache on resume.
- Full native raw/selected mosaics and coverage matched v0.2.1 with **zero
  changed pixels** on the 12012×12180 canvas. All 35 offline report links
  resolved. The conservative retained-output estimate was 2,645,116,224 bytes;
  measured declared stage artifacts occupied 2,588,606,673 bytes. The estimate
  excludes H&E and future versions, and does not reserve free capacity.
- The workstation package is **0.2.2**, installed without dependency changes.
  Raw images, prior results and live transfer/controller scripts were unchanged.

Evidence, pre-edit backup, patch and logs are under
`/mnt/4TB_hdd/PhoMedics/qc/work/preprocessing_batch_fixes_20261018`.
The current real-core report is at
`/mnt/4TB_ssd/PhoMedics/qc/data/preprocessing_batch_fixes_20261018/real-D049Ga01-A5/reports/index.html`.

This release did not repeat a numerical CIDRE fitting benchmark, estimate new
real MIST geometry or validate real neighboring-section AF/H&E registration.
No scientific image-quality acceptance is implied by these software controls;
automatic release remains disabled. The unfinished live transfer still requires
a completed inventory and reviewed per-acquisition inputs before a full run.

## Version 0.2.1 repairs and verification

- All nine confirmed code-review findings are addressed; see
  [CHANGELOG.md](CHANGELOG.md) for the changes.
- **127 pytest checks passed**, including 58 added regression/control cases over
  the initial 69-test build. They cover acquisition ordering, blocked/empty core
  coverage, invalid legacy registration evidence, source-alias fitting pools,
  malformed/mismatched cache records, nonfinite measurements, TIFF byte order,
  native H&E finiteness, compressed file-backed decoding and bounded native
  affine warping.
- Native H&E export succeeded for compressed 32,768-pixel-wide and
  32,768-pixel-tall sources. Source-ROI coordinates were checked against known
  native translations. A textured uint16 image produced identical output under
  different output-block and source-ROI partitions. An independent linear
  intensity field was preserved within OpenCV's interpolation precision.
- The **39 legacy non-image tests passed under system Python** as well.
- A fresh run processed all **49 native 2048×2048 FOVs of D049Ga01 A5** with the
  same frozen CIDRE model and provided native geometry as the initial pilot.
  All nine AF stages completed, and all nine reused their cache on resume.
- Full native raw/selected mosaics and coverage were compared against the
  initial build in row chunks: **zero changed pixels**, with the same
  12012×12180 canvas and float32 scientific image values. All 33 offline report
  links resolved.
- The workstation package was updated to **0.2.1** without changing dependency
  versions. Raw sources and active transfer/controller scripts were unchanged.

Repair verification and the pre-edit backup are under
`/mnt/4TB_hdd/PhoMedics/qc/work/preprocessing_polish_20261018`.
The current real-core report is at
`/mnt/4TB_ssd/PhoMedics/qc/data/preprocessing_polish_20261018/real-D049Ga01-A5/reports/index.html`.

MIST/CIDRE cache and input-validation regressions stub the external commands to
isolate the Python behavior. This repair run did not repeat a Java numerical
fitting benchmark or evaluate newly estimated MIST geometry on real data. The
real-core comparison used existing reviewed geometry and a frozen field; pixel
identity verifies preservation of that processing behavior, not its biological
accuracy.

## Initial version 0.2 build checks

- 69 pytest checks passed, covering the existing tools plus manifest/coordinate
  contracts, copy-inventory import, local-state locking, interruption/resume,
  corrupted sources, corrupted cached outputs, manifest snapshot replay, offline
  report links, field arithmetic, native float blending, contrast controls and
  independent registration evaluation.
- The 39 non-image legacy transfer/registration tests also passed under system
  Python without relying on the image-processing environment.
- The package built as a wheel and ran the synthetic demo outside the checkout.
- The external Java MIST adapter ran a four-FOV synthetic grid. The classic-TIFF
  input requirement is covered by a regression test. Native geometry output and
  subsequent reconstruction/registration stages executed successfully.
- The pinned CIDRE runtime fitted an explicit 64-FOV pool across four cached
  D049Ga01 cores. Runtime/dependency hashes were checked before fitting. This
  tests execution of the upstream method, not illumination-estimation accuracy.
- A real AF pilot used all 49 verified native 2048×2048 FOVs of D049Ga01 A5,
  its previously frozen acquisition-specific CIDRE model, and earlier raw-only
  reviewed native positions. It produced full 12012×12180 float32 raw/selected
  mosaics, coverage, per-stage QC records, comparisons and offline reports.

Workstation evidence and the real input manifest are stored under
`/mnt/4TB_hdd/PhoMedics/qc/work/preprocessing_build_20261018`.
The real pilot report is at
`/mnt/4TB_ssd/PhoMedics/qc/data/preprocessing_build_20261018/real-D049Ga01-A5/reports/index.html`.

## Scope of scientific conclusions

No current stage certifies focus/fold segmentation, absolute fluorescence
preservation or dense AF/H&E correspondence. CIDRE's prior method controls
remain NOT PASSED. Raw-supported tissue-overlap diagnostics use a frozen,
provisional raw-defined subset; they are not an accepted tissue segmentation,
independent geometry ground truth or proof of biological intensity accuracy.

The real AF pilot imported prior geometry; it does not validate newly estimated
MIST geometry across acquisitions. AF/H&E execution was tested using synthetic
images/provided transforms and a synthetic known-transform feature-matching
control. Real neighboring-section registration requires explicit H&E pairing
and independent evaluation; no real paired-section benchmark was completed in
this build.

All image-quality stages remain REVIEW and automatic release is disabled.
