"""Small public Python entry points for the same workflow as the terminal CLI.

Importing this module does not load image libraries. Folder and pair import
prepare explicit manifests; settings inspects configuration; plan checks input
readiness; run processes a prepared manifest and returns its review records.
"""

from __future__ import annotations


def import_folder(raw_root, output, models=None, *, dataset_id="TMA-2026"):
    """Discover NAS-style AF folders, write a draft manifest and return its dict.

    ``raw_root`` is the source directory; ``output`` is a new JSON manifest
    outside it. Optional ``models`` names a JSON file of group-to-asset mappings.
    Review completeness, model assignments and geometry before processing.
    """
    from .io.folder import import_raw_folder

    return import_raw_folder(raw_root, output, models, dataset_id=dataset_id)


def import_pairs(manifest, pairs_csv, output):
    """Add reviewed AF–H&E pair CSV rows, write a new manifest and return its dict.

    ``manifest`` names the existing AF JSON; ``output`` names a new JSON file.
    H&E asset paths in ``pairs_csv`` resolve beside that CSV. Sources are hashed
    and explicit core identities are checked; existing manifests are preserved.
    """
    from .io.section_pairs import import_section_pairs

    return import_section_pairs(manifest, pairs_csv, output)


def settings(
    config=None,
    *,
    parameters=None,
    geometry_method=None,
    illumination_method=None,
    registration_method=None,
    registration_transform=None,
    registration_refinement=None,
):
    """Return effective validated settings without opening data or creating a run.

    ``config`` is an optional TOML path. Nested ``parameters`` dictionaries
    override TOML values; explicit method selectors override both. The returned
    dictionary is independent of the caller's input dictionaries. This function
    and importing this module require no image libraries.
    """
    from .settings import resolve_settings

    return resolve_settings(
        config,
        parameters=parameters,
        geometry_method=geometry_method,
        illumination_method=illumination_method,
        registration_method=registration_method,
        registration_transform=registration_transform,
        registration_refinement=registration_refinement,
    )


def plan(
    manifest,
    output=None,
    config=None,
    *,
    selected=None,
    parameters=None,
    geometry_method=None,
    illumination_method=None,
    registration_method=None,
    registration_transform=None,
    registration_refinement=None,
):
    """Check a prepared manifest's readiness without creating run artifacts.

    ``manifest`` is a JSON path; ``output`` is the intended run directory.
    Omitting ``output`` leaves storage readiness incomplete. ``selected`` is an
    optional sequence of manifest core UIDs; their explicit H&E pairs are included.
    Settings and method overrides use the same precedence as :func:`settings`.

    The returned dictionary contains ``ready_for_execution``, ``issues`` and
    ``warnings``. It checks declared source hashes, headers, assets and capacity;
    it does not process images or certify scientific image quality. Inputs and
    available storage can change after this check and are rechecked by :func:`run`.
    """
    effective_config = settings(
        config,
        parameters=parameters,
        geometry_method=geometry_method,
        illumination_method=illumination_method,
        registration_method=registration_method,
        registration_transform=registration_transform,
        registration_refinement=registration_refinement,
    )
    from .io.manifest import Manifest
    from .preflight import check

    return check(Manifest(manifest), effective_config, output, selected)


def run(
    manifest,
    output,
    config=None,
    *,
    resume=True,
    selected=None,
    parameters=None,
    geometry_method=None,
    illumination_method=None,
    registration_method=None,
    registration_transform=None,
    registration_refinement=None,
):
    """Process a prepared JSON manifest and return execution and review records.

    ``output`` is the run directory; ``config`` is an optional TOML path.
    ``selected`` limits processing to manifest core UIDs and their explicit H&E
    pairs. Omit it to process all cores. Overrides follow :func:`settings`.

    With ``resume=True``, matching stages are reused only after input identities
    and artifact hashes are verified. Reuse requires supplying the same settings
    again; ``resume=False`` requests fresh stage execution.

    The returned dictionary includes ``stage_results``, ``execution_errors`` and
    ``blocked_stages``. Completed processing does not imply image-quality
    acceptance; inspect the stage review records and ``reports/index.html``.
    """
    from .pipeline import run_pipeline

    return run_pipeline(
        manifest,
        output,
        config,
        resume=resume,
        selected=selected,
        parameters=parameters,
        geometry_method=geometry_method,
        illumination_method=illumination_method,
        registration_method=registration_method,
        registration_transform=registration_transform,
        registration_refinement=registration_refinement,
    )
