# Development guide

The package supports exploratory research with explicit inputs and reviewable
outputs. Start with [workflows.py](../src/tma_qc/workflows.py) to see stage order,
then follow a stage into `backends/` or `qc/` for the image calculation.

## Follow one operation through the code

For an ordinary run, read these modules in order:

1. [cli.py](../src/tma_qc/cli.py) declares arguments;
   [commands.py](../src/tma_qc/commands.py) calls the application operation.
   [api.py](../src/tma_qc/api.py) exposes the same operations to Python callers.
2. [workflows.py](../src/tma_qc/workflows.py) declares the AF and pair stages,
   dependencies and configuration sections. [pipeline.py](../src/tma_qc/pipeline.py)
   executes them with receipts, verification and failure isolation.
3. Follow a stage such as [illumination.py](../src/tma_qc/stages/illumination.py)
   into its [correction backend](../src/tma_qc/backends/cidre.py) and
   [QC calculation](../src/tma_qc/qc/illumination.py).
4. [assessment.py](../src/tma_qc/qc/assessment.py) summarizes measurement
   availability; [core.py](../src/tma_qc/reporting/core.py) and
   [batch.py](../src/tma_qc/reporting/batch.py) present the saved evidence.

Use `tma-qc settings` to inspect validated configuration while reading a method,
and the [test map](../tests/README.md) to locate its controls.

## Readable research code

Use names that describe the image or operation, and keep functions focused on
one task. The common workflow should be easy to follow from the command handler
through the relevant stage to its calculation. Extract shared helpers when they
serve existing repeated operations. Keep scientific choices, units and defaults
close to the code that uses them, with comments explaining the reason for a choice.

Expose options for decisions users actually need to make. Present examples by
task, with one clear command for the common run and optional investigations
alongside it. Let the runner handle stage dependencies and verification; a new
method can use the existing stage interface. Keep algorithm details in the
methods reference so the quickstart stays short.

## Environment and routine checks

Use an activated Python 3.11+ environment:

```bash
python -m pip install -e '.[codecs,dev]'
make check
```

The optional `dev` dependencies include pytest and Ruff 0.13.2. Ruff's formatting,
import ordering and basic error/unused-name checks are configured in
[pyproject.toml](../pyproject.toml). The workstation's tested imaging versions are
recorded in [constraints-workstation.txt](../constraints-workstation.txt).

Without activating the workstation environment:

```bash
make check TMA_PYTHON=/mnt/4TB_ssd/conda/envs/mggm-mil/bin/python
```

Individual targets are `make lint`, `make format-check`, `make format` and
`make test`. `format` applies import/lint fixes and formatting. The equivalent
Python commands are:

```bash
python -m ruff check src tests experiments examples tma_qc check_transfer.py check_registration.py make_montage.py
python -m ruff format --check src tests experiments examples tma_qc check_transfer.py check_registration.py make_montage.py
python -m pytest -q
```

The standard-library legacy checks can also run separately:

```bash
python3 -m unittest tests.test_transfer_check tests.test_registration_check
```

## Responsibilities

| Module | Owns | Keep elsewhere |
| --- | --- | --- |
| `cli.py` | Options, dispatch and command error boundary | Image processing |
| `commands.py` | Application command handlers | Numerical method implementations |
| `api.py` | Public manifest import, settings, readiness and run entry points | CLI parsing and image calculations |
| `settings.py` | Inspectable method registry, selectors and typed parameter overrides | Image processing and runtime downloads |
| `registration_config.py` | Pure core configuration, schema and resolution | Optional imaging/runtime imports |
| `workflows.py` | AF/pair stage order, dependencies and deferred input identities | Checkpoint implementation |
| `context.py` | Input/output access and transient per-core caches | Dataset-wide mutable state |
| `pipeline.py` | Stage transactions, provenance, cache recovery and run reporting | Tissue/artifact algorithms |
| `stages/` | Stage inputs, artifact writing and a `Payload` | New generic scheduling frameworks |
| `backends/`, `qc/` | Numerical algorithms and measurement functions | CLI arguments and report HTML |
| `io/`, `schema.py`, `config.py` | Input contracts, units and validation | Silent guessing of data identity |
| `reporting/` | Views of saved measurements | New scientific measurements during rendering |

The root `tma_qc/` directory is a compatibility shim for standalone scripts.
Implementation belongs under `src/tma_qc/`; do not duplicate it in the shim.

## Add or change a method

1. State the input role, axes, units, dtype, output meaning and assumptions.
2. Put the calculation in `backends/` or `qc/`. Keep numerical choices explicit;
   explain non-obvious precision, ordering or coordinate requirements near the code.
3. Use a stage to resolve inputs, write artifacts inside the supplied output
   directory and return a [Payload](../src/tma_qc/schema.py). Existing stage names
   and output labels form the report/checkpoint contract.
4. Declare dependencies and configuration sections in `workflows.py`. Supply
   potentially failing asset hashes through callable `extra` inputs so the
   runner can isolate failure to that stage.
5. Add meaningful unit, integration or scientific controls. Compare unchanged
   calculations against a previous result when doing structural refactors.
6. Register the public choice and requirements in `settings.py`, validate its
   configuration and give it a stable method name. CLI and Python must use the
   same selector resolver. Preserve the stage output contract, input identities
   and QC evidence when adding a backend. Method metadata/help/configuration
   inspection must work before imaging extras are installed.
7. Update [settings](SETTINGS.md), methods documentation, tutorial examples and
   [CHANGELOG.md](CHANGELOG.md).
   Record verification evidence and any scientific limits in
   [VALIDATION.md](VALIDATION.md).

Use `NeedsInput` for recoverable missing prerequisites. Other exceptions become
execution errors inside a stage. An execution `complete` status is independent of
the `Payload.qc` quality state; keep scientific acceptance assumptions explicit.

## Reproducibility and performance

Preserve native coordinates, float precision and tile addition order when
refactoring reconstruction. Keep raw/corrected comparisons on the same geometry,
fields and display window. Source verification and artifact checksums are part of
the workflow, not optional benchmark overhead to remove.

Use fresh output directories for timing actual work; a cached resume measures a
different task. Save dataset selection, effective configuration, code/runtime
identities, timing scope and machine/storage conditions. Profile before choosing
an optimization. Worker count, retained raw cache bytes and native output space
are distinct limits; document changes to each.

Use comments for scientific intent, units and non-obvious invariants. Keep
functions focused, import optional imaging dependencies only where needed by
command handlers, and avoid introducing abstractions that obscure the stage
sequence. The [test map](../tests/README.md) describes existing control coverage.
