"""Small public Python entry points for the same workflow as the terminal CLI.

Importing this module does not load image libraries. Folder and pair import
prepare explicit manifests; run consumes a prepared manifest and returns its
execution and review summary.
"""

from __future__ import annotations


def import_folder(raw_root, output, models=None, *, dataset_id="TMA-2026"):
    """Discover NAS-style AF folders and write a new draft manifest."""
    from .io.folder import import_raw_folder

    return import_raw_folder(raw_root, output, models, dataset_id=dataset_id)


def import_pairs(manifest, pairs_csv, output):
    """Add explicit reviewed section pairs and write a new manifest."""
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
    """Inspect effective validated settings without creating a run or opening images."""
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
    """Run the shared workflow with optional typed settings and method selectors."""
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
