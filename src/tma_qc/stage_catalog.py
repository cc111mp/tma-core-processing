"""Inspectable stage contracts; execution remains explicit in workflows.py.

This metadata explains the existing pipeline without importing image libraries,
changing dependencies, or selecting a new algorithm during inspection.
"""

from __future__ import annotations

from copy import deepcopy

from .schema import CORE_STAGES, PAIR_STAGES
from .settings import FIXED_ALGORITHMS, METHODS

_CONTRACTS = {
    "index": {
        "description": "Verify declared AF tile identities and image encoding",
        "dependencies": [],
        "config_sections": ["profile"],
        "inputs": ["Manifest core and ordered FOV paths/checksums", "Original AF TIFFs"],
        "outputs": ["Verified input inventory"],
        "method_selectors": [],
    },
    "tile_qc": {
        "description": "Measure local detail, saturation and zero counts on native tiles",
        "dependencies": ["index"],
        "config_sections": ["tile_qc"],
        "inputs": ["Verified original AF tiles"],
        "outputs": ["Per-tile measurements", "Tile-grid evidence image"],
        "method_selectors": [],
    },
    "geometry": {
        "description": "Resolve native tile positions for reconstruction",
        "dependencies": ["index"],
        "config_sections": ["geometry"],
        "inputs": [
            "Complete native position file, or reviewed grid/sequence and MIST runtime",
            "Verified original AF tiles",
        ],
        "outputs": ["Native pixel positions"],
        "method_selectors": ["geometry_method"],
    },
    "geometry_qc": {
        "description": "Measure image support for tile overlaps at the selected geometry",
        "dependencies": ["index", "geometry"],
        "config_sections": ["artifact_qc"],
        "inputs": ["Verified original AF tiles", "Native tile positions"],
        "outputs": ["Overlap measurements and availability", "Alignment evidence crops"],
        "method_selectors": [],
    },
    "illumination": {
        "description": "Apply frozen correction fields or retain original selected counts",
        "dependencies": ["index"],
        "config_sections": ["illumination"],
        "inputs": ["Verified AF tiles", "Compatible model fields/receipt when using CIDRE"],
        "outputs": ["Selected frame inventory", "Corrected frames when correction is enabled"],
        "method_selectors": ["illumination_method"],
    },
    "illumination_qc": {
        "description": "Compare raw and selected overlap signals without refitting geometry",
        "dependencies": ["index", "geometry", "illumination"],
        "config_sections": ["artifact_qc"],
        "inputs": ["Raw and selected AF frames", "The same native tile positions"],
        "outputs": ["Photometric overlap measurements", "Raw-selected comparison evidence"],
        "method_selectors": [],
    },
    "mosaic": {
        "description": "Blend raw and selected tiles into native acquisition canvases",
        "dependencies": ["geometry", "illumination"],
        "config_sections": ["mosaic"],
        "inputs": ["Native tile positions", "Raw and selected AF frames"],
        "outputs": ["Native float32 mosaics", "Coverage maps"],
        "method_selectors": [],
    },
    "mosaic_qc": {
        "description": "Measure boundary and grid evidence and render comparison previews",
        "dependencies": ["mosaic"],
        "config_sections": ["report", "artifact_qc"],
        "inputs": ["Native raw/selected mosaics", "Coverage and tile positions"],
        "outputs": ["Mosaic and boundary measurements", "Previews and comparison evidence"],
        "method_selectors": [],
    },
    "tissue": {
        "description": "Estimate provisional tissue support with explicit unknown regions",
        "dependencies": ["mosaic"],
        "config_sections": ["tissue"],
        "inputs": ["Raw mosaic", "Coverage map"],
        "outputs": ["Possible-tissue and unknown masks", "Mask interpretation metadata"],
        "method_selectors": [],
    },
    "he_qc": {
        "description": "Verify an explicitly paired original H&E core image",
        "dependencies": [],
        "config_sections": ["report"],
        "inputs": ["Declared H&E core crop and checksum", "Explicit section-pair identity"],
        "outputs": ["H&E input measurements", "Review preview"],
        "method_selectors": [],
    },
    "registration": {
        "description": "Use a supplied transform or fit the declared H&E partner to AF",
        "dependencies": ["he_qc"],
        "config_sections": ["registration", "report"],
        "inputs": [
            "Explicit section pair and original H&E crop",
            "AF mosaic outputs",
            "Supplied native transform or prerequisites for the selected fitter",
        ],
        "outputs": ["Native H&E-to-AF transform", "Overlay and optional aligned native H&E"],
        "method_selectors": [
            "registration_method",
            "registration_transform",
            "registration_refinement",
        ],
    },
    "registration_qc": {
        "description": "Evaluate registration with independently supplied held-out landmarks",
        "dependencies": ["registration"],
        "config_sections": ["qc"],
        "inputs": ["Native transform", "Independent evaluation landmarks when available"],
        "outputs": ["Landmark errors in native pixels, or an explicit unavailable result"],
        "method_selectors": [],
    },
}


def stage_registry():
    """Return independent JSON-safe descriptions of existing AF and pair stages.

    Config sections are the primary parameters declared by workflows.py. Shared
    runtime, storage and input-profile settings also affect stage operation.
    Available methods describe software choices; plan/run checks actual assets
    and optional runtimes. Inspection performs no processing or source checks.
    """

    def describe(name):
        contract = deepcopy(_CONTRACTS[name])
        selectors = contract["method_selectors"]
        return {
            "name": name,
            **contract,
            "methods": {selector: deepcopy(METHODS[selector]) for selector in selectors},
            "fixed_algorithm": deepcopy(FIXED_ALGORITHMS.get(name)),
        }

    return {
        "schema_version": 1,
        "af_stages": [describe(name) for name in CORE_STAGES],
        "pair_stages": [describe(name) for name in PAIR_STAGES],
        "shared_config_sections": ["runtime", "storage", "profile"],
        "notes": [
            "Python workflows.py defines the fixed execution order and dependencies.",
            "Pair stages run only for explicitly declared AF-H&E section pairs.",
            "Supplied native positions and pair transforms take precedence over estimation.",
            "Only settings for the selected backend affect that backend's calculations.",
            "Metadata inspection does not validate source completeness or method readiness.",
            "QC and tissue support remain advisory; execution completion is not acceptance.",
        ],
    }
