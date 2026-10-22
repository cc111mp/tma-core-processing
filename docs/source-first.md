# Process a complete source core while the bulk copy continues

The raw archive does not have to finish copying before an independently complete
AF core can run. The AF-only research recipe in
[process_source_core.py](../examples/process_source_core.py) stages one core from
the original NAS, runs the existing numerical workflow on local storage, then
publishes final products and a separate stage-by-stage QC report in a dedicated
NAS home directory. Diagnostic failures do not suppress a valid reconstruction.

```text
Original NAS: complete raw core + expected source hashes
    → local SSD: verified FOVs + frozen model/geometry assets
    → local SSD: correction, reconstruction and stage QC checkpoints
    → NAS home: native results.zip, when available
    → NAS home: qc/ report, including failed and unrun stages

Bulk raw archive copy continues independently.
```

## Readiness is per core

Before using this recipe, verify the complete expected FOV set against an
acquisition record or an independently complete, read-only source inventory.
A folder containing some images or having an old modification time is not
sufficient evidence. If multiple cores were saved in one folder, resolve their
assignments before processing.

Supply an explicit [manifest](../examples/README.md), with original NAS
`raw_root`, every selected FOV's SHA256, the acquisition-compatible frozen
flatfield/darkfield and model receipt, and explicit native geometry (or all
required MIST inputs). Record the completeness evidence in core metadata and set
`source_FOV_set_verified` to the Boolean `true` only after that check. The flag is
an attestation by the manifest preparer; the recipe does not discover missing
FOVs or monitor acquisition completion. Associated H&E pairs are rejected because
this small recipe exports AF products only.

This route does not relax or alter the completed-transfer importer. It does not
read or write the transfer controller's database. Raw source files remain inputs;
work and publication directories must be outside raw storage and separate from
one another. Use a dedicated publication directory for each frozen job, separate
from the active transfer and CIDRE worker output namespaces.

## Run one core

Use the package environment with the optional TIFF codecs installed. Preserve
the recipe version for a run, since its hash is part of the job identity:

```bash
mkdir -p /data/work/run_001
cp examples/process_source_core.py /data/work/run_001/process_source_core.py
python /data/work/run_001/process_source_core.py \
  --manifest /data/work/run_001/source-manifest.json \
  --core SLIDE-original-A1-AF \
  --workspace /local-ssd/tma/run_001/SLIDE-original-A1-AF \
  --out /nas/home/data/TMA_preprocessing/2026/run_001/SLIDE-original-A1-AF \
  --config /data/config/af.toml
```

A config is optional for standard 2048×2048 uint16 AF inputs. The recipe puts the checkpoint
cache under its local workspace even when a supplied config names another cache.

Each new raw file is streamed to a temporary local file, checked against its
expected SHA256, rehashed independently, then renamed into place. Source size
and modification time must stay stable during that read. Existing staged files
are rehashed against their pinned expected hashes. The numerical pipeline then
performs its own input and output verification.

Repeat the identical command to resume. Inputs, configuration, asset contents,
engine code and recipe code form the frozen job identity; changing them requires
a new workspace and publication directory. A completed local package can be
published again without reconstructing or rereading original raw files. This
publishes the pinned result, rather than making a new claim about current source
contents. A corrupt existing final file is reported and preserved for inspection;
the recipe does not overwrite it silently. Only one process may use a workspace.
Use one publisher per output directory; there is no multi-host scheduling service.

The package is an immutable observation, including report-only results from
failed processing. Repeating the command retries delivery of that observation.
To retry processing after packaging, use a new workspace and output directory;
retain the earlier report. Before packaging, the numerical engine can resume
its verified checkpoints normally.

## Published results and completion

```text
NAS home/<run>/<core>/
  index.html          # Separate links to final output and QC review
  results.zip         # Only present for verified reconstruction + coverage
  qc/
    index.html        # Filterable stage table and available previews
    qc.json           # Identities, stage measurements, findings and hashes
    stages.csv        # All expected stages, including error/blocked/not-run
    review-needed.csv # Incomplete stages and non-PASS quality
    evidence/         # Available previews and tile measurements
  owner.json          # Frozen job identity
  publication.json    # Separate image/report delivery states and copy errors

Local SSD/<run>/<core>/
  raw/, assets/       # Verified inputs for this core
  run/                # Full stage artifacts, provenance and offline reports
  products/           # Exported products and preview
  job.json, status.json, package.json, staging-receipt.json, ...
```

The archive contains native float32 `corrected.tif`, uint8 `coverage.tif`, stage
QC, export hashes, frozen model/geometry assets, source/replay manifests, staging
receipts, effective configuration and recipe source. Provisional
`possible-tissue.tif` and `unknown.tif` are included when their stage succeeds.
Comparison PNGs and tile measurements are separate QC evidence. TIFF
compression is lossless, with every decoded native pixel bit checked locally.
Each delivered file is independently hashed. `publication.json` records
`final_output_published`, `qc_report_published` and per-file errors separately.
`complete: true` means all files in that package were delivered and verified.
A report-only publication can be complete with `final_image_available: false`
and no native archive. An I/O error in one component does not prevent delivery
of the other being attempted. Repeating the command retries incomplete delivery.

If a verified local reconstruction exists but ZIP capacity or writing fails,
`final_image_available` remains true and `native_archive_available` is false.
The TIFFs stay local, the QC report records the export failure, and no nonexistent
archive download is advertised. CLI exit remains nonzero. Archives are built
and CRC-checked in temporary files before their final name is installed.

The archive excludes raw FOVs, corrected individual FOVs, the raw native mosaic
and full intermediate checkpoints. These remain in the local workspace. The
replay manifest references original raw storage and the bundled frozen assets;
it requires the same engine version/code and original raw bytes to reconstruct.
This compact publication is not a replacement for the bulk raw archive copy.

A completed quality check returning FAIL still permits delivery of a verified
core. A diagnostic crash does not establish an image-quality defect. Invalid or
corrupt reconstruction artifacts are never packaged as final images. Missing
raw data, correction or geometry can therefore leave a report without an image.
CLI exit 1 records execution/export/delivery errors, exit 2 blocked processing,
and exit 0 completed execution/delivery. None signifies scientific acceptance.

Execution completion does not mean image quality passed. Scientific QC remains REVIEW;
possible tissue and unknown regions are provisional. No validated blurred-tissue
ratio or automatic image-quality release is added by this recipe.

## Review the prepared batch without changing it

The read-only snapshot tool builds a separate overview from a frozen batch plan
and local core records. Use a new output directory for each snapshot:

```bash
python examples/review_source_batch.py \
  --plan /data/work/run_001/plan.json \
  --out /nas/home/data/TMA_preprocessing/2026/qc_snapshot_001
```

The overview links image availability and each core's stage report, with core,
stage and review-queue CSVs. Filters separate execution problems, image-quality
failures and incomplete publication. Unrun cores remain visible. Optional
`--workspace-map` accepts a UID-to-local-workspace JSON map for earlier pilots.
Native-image and NAS verification states come from saved receipts; only preview
hashes are rechecked. This snapshot is not a live monitor or a new native-image
integrity check. Human review remains pending.
Missing, unreadable or changed preview files are listed as unavailable evidence;
they do not prevent the remaining cores being reported or substitute stale images.

Existing workers keep their frozen recipe. New export behavior applies to newly
prepared jobs; the snapshot tool can review the earlier jobs directly.

## Capacity and timing

Process one core at a time initially. Local storage must accommodate raw staging,
full numerical checkpoints, final lossless exports and their archive. Existing
storage guards check each phase, but cannot reserve space against other jobs.
Keep completed workspaces until publication is verified and checkpoint retention
has been decided.

NAS reads and publication share network capacity with the raw copy. This avoids
waiting for the whole archive, but does not eliminate per-core transfer time.
Do not apply SSD-only benchmarks to the full NAS-to-home path. The first real
source-first pilot and its timing scope are recorded in
[VALIDATION.md](VALIDATION.md).
