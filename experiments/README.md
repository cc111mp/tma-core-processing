# Scientific investigations

Use this directory for reproducible empirical benchmarks before a method is
eligible for automatic image-quality acceptance. Current software tests exercise
necessary properties only. They do not establish biological accuracy.

`focus_controls.py` records contrast invariance and actual near/far-Z response on
explicit native patches. It writes diagnostics without generating a blur mask.
Keep patches from one acquisition together when building later training/test sets.

Run the existing investigation after installing the package in your environment:

```bash
python experiments/focus_controls.py --near /data/near.tif --far /data/far.tif --x 512 --y 512 --size 256 --out /results/focus-control.json
```

The two inputs must represent the same native region at different Z positions.
The saved record includes source hashes, patch coordinates and the contrast-only
control alongside both detail measurements. It is a supplied-pair investigation,
not a calibrated focus detector evaluation.

Keep generated inputs and results outside the code checkout. For a new study,
document the hypothesis, acquisition selection, units, settings, controlled
comparison and held-out data before interpreting the measurements. Reuse
functions from `tma_qc` rather than copying their implementations into scripts.
Promotion into production stages follows the
[development guide](../docs/DEVELOPMENT.md).

## Review Excel-selected reconstructions

Select source acquisitions explicitly, prepare a manifest and run the ordinary
pipeline. Include historical OK controls from the same acquisition group where
possible. Keep the original workbook, source tiles and earlier exports unchanged.
The historical label is selection context; the newly reconstructed image needs
its own assessment.

`labelled_reconstruction_review.py` joins an existing run to a CSV with exactly
identified core UIDs:

```csv
uid,historical_label,workbook_cell
EXAMPLE_CORE_UID,GRID,UV!F143
```

```bash
python experiments/labelled_reconstruction_review.py \
  --run /results/pilot \
  --labels /study/labels.csv \
  --out /results/pilot-review
```

The CSV/JSON outputs retain raw and selected mosaic hashes, stage measurements,
availability and failure reasons. Current review starts as `pending` when a
selected mosaic exists, otherwise `not_assessed`. No historical label becomes
a current artifact flag automatically. Exact expert-reviewed image-version
linkage remains unverified by this helper.

An optional `--reviews /study/reviews.json` accepts explicitly authored current
observations. It is a JSON list with `uid`, current `mosaic_sha256`, `status`,
`reason`, `evidence` (objects with run-relative `path` and observation-time
`sha256`), and optional `review_flag`
and `reviewer`. Status is `pending`, `not_assessed`, `review_candidate` or
`no_issue_observed`; none means scientific acceptance. A positive flag requires
`review_candidate`. Record assisted visual inspection as such, rather than as
expert validation. Capture each evidence file's SHA256 when recording the
observation; for stage evidence, copy the path and hash from its completed
stage output record. Duplicate/unknown UIDs, stale image or evidence hashes,
and escaping or missing evidence paths are rejected.

The first four-core real-data pilot used D049Ga01 A3/A8 (historical GRID) and
B2/B3 (historical OK), with 176 raw FOVs and frozen compatible CIDRE fields. All
36 AF stages completed. Fine horizontal banding remained in inspected native
low-signal crops of both groups, supporting provisional regional review flags,
not an Excel GRID classifier or a claim of tissue unusability. The executed
study, native crop hashes and independent review are under
`/mnt/4TB_hdd/PhoMedics/qc/work/labelled_raw_pilot_20261022`.
