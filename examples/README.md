# Input templates

[manifest.example.json](manifest.example.json) is an input contract example. It
contains placeholder paths and SHA256s and needs real assets before it can run.
To explore the software without preparing real inputs, use
`tma-qc demo --out /tmp/tma-qc-demo` in a new directory.

## Generate a manifest from NAS-style folders

When the inputs follow the company NAS acquisition layout, point the importer
at the raw AF root containing the slide folders:

```bash
tma-qc import-folder \
  --raw-root /data/TMA_raw/2026 \
  --dataset-id TMA-2026 \
  --out /data/manifests/manifest.json
```

The same command works on the NAS or a local copy with the same structure. It
does not require the transfer controller or its SQLite database. For our copied
archive, the raw root is `/mnt/pho-nas/home/data/TMA_raw/2026`.

Supported paths under the raw root include:

```text
<acquisition>/<slide>/<slide>/<core>/<lamp>/stitch/<numbered-tile>.tiff
<acquisition>/<slide>/<core>/<lamp>/scan/<X_Y>/<image>.tiff
<slide>/<core>/stitch_folder/FOV_1.tif
```

Additional project/`Raw` wrapper folders are allowed. Core folders use labels
such as `A1` or `B12`; the observed lamp folders start with `middle lamp`.
The importer prefers `stitch`, then `stitch_folder`, then an unambiguous `scan`
set. Numbered tiles must form a contiguous sequence starting at 0 or 1. Multiple
images at one XY position require explicit plane/exposure selection. Corrected
`stitch_folder_bg` exports, autofocus sequences and ZIP files are retained in
the inventory counts but are not selected as raw FOVs. Empty and unresolved
cores remain in the manifest with `blocked_reasons`.

Selected original tiles are read to calculate SHA256s; this can take time on NAS.
Images are not decoded by the importer. `plan` or `run` later checks TIFF headers
and source hashes. Standard metadata entries (`Thumbs.db`, `desktop.ini`,
`.DS_Store`, `@eaDir`, `#recycle`, `.snapshot`) are ignored. Unreadable folders,
symbolic links and detected source/folder changes stop the import without
publishing a manifest. Use a new output file outside the raw archive.

Generated core metadata retains the exact `source_slide_label`,
`source_lamp_label` and `source_folder`. Machine IDs use the same relative-path
rules as `import-copy`, keeping repeat acquisitions and lamp groups distinct.
The generated `source_FOV_set_verified` is **false**: successful discovery and
hashing do not establish that a physical acquisition is complete. Review that
against acquisition records before using the source-first recipe.

The result is a draft with candidate FOV assignments. Review the assignments,
add native geometry and compatible correction-model assets, and supply H&E
pairs when wanted. Folder XY names are retained as source metadata, not converted
to native pixels. An optional `--models models.json` accepts a group-ID mapping
to `flatfield`, `darkfield` and `model_metadata` paths; relative asset paths
resolve beside the output manifest. Geometry and H&E matches remain explicit.

Exit 0 means the manifest was generated, including any blocked acquisitions;
use `tma-qc plan` to check execution readiness.

## Add explicit AF–H&E section pairs

Use [section-pairs.example.csv](section-pairs.example.csv) as the starting point for a
reviewed `pairing/sections.csv`. Reuse the AF manifest from preprocessing and add
already cropped H&E core images. Whole-slide H&E images need core extraction
before this setup. Keep the crops separate from the AF raw hierarchy:

```text
tma-study/
  raw/<acquisition>/<slide>/<core>/<lamp>/stitch/FOV_1.tif
  he/HE_SLIDE_01/A1.tif
  pairing/sections.csv
  manifests/manifest.json
  manifests/paired.json
  configs/af.toml
  runs/run01/
```

The basic template has only four required columns:
`uid,af_core,he_path,relationship`. Choose a readable pair name for `uid`;
`af_core` is copied from the AF manifest, not guessed from the core label. The
[setup recipe](../docs/RESEARCH_GUIDE.md#register-explicitly-paired-he-cores)
includes a read-only UID listing. CSV asset paths resolve beside the
CSV, so `../he/HE_SLIDE_01/A1.tif` points into the layout above. The importer hashes
H&E and records normalized absolute asset paths in a new manifest:

```bash
python -m tma_qc import-pairs \
  --manifest /data/tma-study/manifests/manifest.json \
  --pairs /data/tma-study/pairing/sections.csv \
  --out /data/tma-study/manifests/paired.json
```

AF-only users leave `pairs` empty. For registration, use the paired manifest and
edit the existing `[registration]` section of your AF TOML (or add it if absent)
to use `backend = "core"` and `core_method = "geometry_sift"`. Built-in fitting
defaults are enough for a first run; a second TOML is optional.

After reviewing the first run, the
[advanced template](section-pairs.advanced.example.csv) adds optional
`he_slide_label`, `transform`, `landmarks`, `af_mpp` and `he_mpp` columns. Blank
optional fields require no assets. Assign a path only when the corresponding
reviewed file exists. After editing this CSV, import it from the original AF
manifest into a new output such as `paired-evaluation.json`, and run that new
manifest. CSV edits do not update an earlier paired JSON. Held-out landmark
CSVs use `he_x,he_y,af_x,af_y`; fitting
never receives them. Method tuning can use `[registration.matching]` and
`[registration.refinement]` in the main configuration, or `core_config` can
reference an existing [core-registration.toml](../configs/core-registration.toml).
See [methods and settings](../docs/SETTINGS.md) and the
[research workflow](../docs/RESEARCH_GUIDE.md#register-explicitly-paired-he-cores)
for Python usage, optional refinements and review requirements.

## What goes in a manifest

| Field | What to supply |
| --- | --- |
| `raw_root` | Root folder containing the selected original FOVs |
| `groups` | Compatible frozen flatfield, darkfield and model-receipt assignments |
| `cores` | Dataset-specific core/acquisition identities, group assignments and complete FOV lists with source SHA256s |
| `cores[].positions` | Reviewed native tile positions when using supplied geometry; see [geometry.example.json](geometry.example.json) |
| `pairs` | Optional AF–H&E section pairs, with source hashes, section relationships and registration assets |

Copy the template into your dataset workspace and replace its example identities,
paths and hashes. FOV paths resolve under `raw_root`; relative model and geometry
paths resolve beside the manifest. FOV IDs must match the geometry records.
Record the geometry source and set `geometry_reviewed` only after review. Model
compatibility comes from acquisition information, such as camera, channel and
exposure. Leave `pairs` empty for AF-only processing.

The completed-transfer importer can populate raw FOV assignments and hashes from
its read-only inventory; it retains unresolved acquisitions for review. A
verified subset of an ongoing transfer can use the
[source-first recipe](../docs/source-first.md). For an individually verified
source, the package's hash helper is also available:

```python
from tma_qc.provenance import sha256

print(sha256("/data/raw/A1/stitch/FOV_00001.tif"))
```

Choose a configuration from [configs/](../configs/) and set its local SSD cache
path. Use `tma-qc run` to process the manifest. `tma-qc plan` is an optional
readiness check; the processing run performs its own verification. The
[research guide](../docs/RESEARCH_GUIDE.md) covers common tasks, and the
[pipeline reference](../docs/pipeline.md) documents file contracts and coordinates.

## Process directly from original NAS storage

[process_source_core.py](process_source_core.py) stages one independently complete
AF core on local SSD, runs the existing workflow, and publishes lossless final
images and QC to a dedicated destination. This can run while the bulk raw copy
continues. Read the [source-first guide](../docs/source-first.md) for completeness
evidence, frozen job identities, output contents and restart behavior.

Final native output and the `qc/` review report are separate deliverables. A QC
diagnostic failure does not suppress an otherwise valid core image. The
[batch snapshot tool](review_source_batch.py) builds an independent review
overview from existing local records, including incomplete and unrun cores.
