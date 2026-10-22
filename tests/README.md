# Test map

Run the full suite with `python -m pytest -q` or `make check` from the repository
root. Tests create their inputs in temporary directories; they do not require NAS
access, a transfer inventory or real study images. MIST calls are simulated where
needed rather than launching a real stitcher.

| Directory | Purpose | Representative controls |
| --- | --- | --- |
| `unit/` | Input contracts, helpers and failure boundaries | Native coordinate declarations, file identity, source hashes, cache recovery, capacity guards, bounded workers, native H&E decoding |
| `integration/` | Whole workflows and command behavior | Synthetic AF/H&E processing, resume, corruption/failure isolation, historical reporting, CLI help without imaging dependencies |
| `scientific/` | Necessary numerical properties | Contrast controls, known-field arithmetic, independent blending formula, native counts, geometric transforms |
| Root `test_*.py` | Existing standalone tools | Transfer discovery, registration-log validation and montage behavior |

The standard-library subset is:

```bash
python3 -m unittest tests.test_transfer_check tests.test_registration_check
```

`integration/test_source_recipe.py` also exercises source staging, lossless native
TIFF export, replay assets, frozen job conflicts, resumed publication and a damaged
destination archive using local synthetic inputs. It does not require NAS access.
Fault injection checks that diagnostic crashes and quality rejection preserve a
valid reconstruction, failed reconstruction/staging still yields a report, and
partial report delivery resumes independently. Batch-review controls retain
unrun cores without changing processing records.

A passing software/control suite does not establish biological focus, fold,
intensity-preservation or neighboring-section correspondence accuracy. Real
method investigations belong under [experiments/](../experiments/README.md), with
their acquisition selection and evidence documented in
[VALIDATION.md](../docs/VALIDATION.md).

`unit/test_folder_import.py` covers direct NAS-style folder import, original
labels, acquisition/lamp identity, numeric ordering, ambiguous and empty cores,
selected-file hashes, mutation/read failures and compatibility with transfer
import IDs. The CLI integration check runs without image-library site packages.

The 0.3.1 deep-review controls add descriptor-bound source hashing and runtime
asset/cache mutation, writable-tree and report aliases, exclusive manifest
publication, canonical FOV aliases, historical registration dependencies,
float-encoding/preview invariance, bilinear-cell fold detection, constant/extreme
illumination diagnostics, DC-stable periodicity and sparse native coverage.
Their reproductions and scope are indexed in
[the deep review](../docs/REVIEW_20261021.md).
