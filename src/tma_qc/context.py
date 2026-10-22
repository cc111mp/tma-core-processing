"""Per-core and section-pair inputs, artifact access, and transient caches."""

from __future__ import annotations

from pathlib import Path

from .io.coordinates import load_positions
from .io.tile_cache import TileCache, file_identity
from .provenance import read_json
from .qc.stitching import OverlapMeasurements


class Context:
    """Inputs and completed-stage results for one processing unit.

    AF stages use ``core`` and its declared raw FOVs. Section-pair stages use
    ``pair`` plus the completed AF results. The context manager clears transient
    raw arrays and overlap measurements even when a stage is interrupted.
    """

    def __init__(self, manifest, config, root, scratch, core=None, pair=None, af_results=None):
        self.manifest, self.config, self.root, self.scratch = manifest, config, root, scratch
        self.core, self.pair = core, pair
        self.results = {}
        self.af_results = af_results or {}
        self.source_hashes, self.verified, self.source_error = {}, {}, None
        self.source_identities = {}
        self._raw_frames = {f.id: self.manifest.raw_path(f) for f in core.fovs} if core else {}
        self.he_path = self.manifest.resolve(pair.he_path) if pair else None
        self.tiles = TileCache(
            self._raw_frames.values(),
            config["runtime"]["decoded_cache_bytes"],
            identities=self.source_identities,
        )
        self.raw_overlaps = OverlapMeasurements()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.tiles.clear()
        self.raw_overlaps.clear()

    def asset(self, stage, name):
        """Resolve a named output of an earlier stage for this unit."""
        return self.root / self.results[stage].outputs[name]["path"]

    def af_asset(self, stage, name):
        """Resolve the paired AF core's output during H&E processing."""
        return self.root / self.af_results[stage].outputs[name]["path"]

    def raw_frames(self):
        """Map manifest FOV identifiers to original raw source paths."""
        return dict(self._raw_frames)

    def validate_sources(self):
        """A changed verified source invalidates the current stage, including cache reuse."""
        for path, identity in self.source_identities.items():
            file_identity(path, identity)

    def frames(self):
        """Read the selected frame index: corrected images or explicit raw paths."""
        path = self.asset("illumination", "frames")
        return {
            k: (Path(v) if Path(v).is_absolute() else path.parent / v)
            for k, v in read_json(path).items()
        }

    def positions(self):
        """Read the saved integer positions in native XY pixels."""
        return load_positions(self.asset("geometry", "positions"), [f.id for f in self.core.fovs])
