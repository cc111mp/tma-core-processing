import numpy as np
import tifffile
from scipy import ndimage

from ..io.images import covered_sample, read_image, reduced
from ..provenance import write_json
from ..schema import Payload
from ..storage import require_space


def run(ctx, out):
    image = read_image(ctx.asset("mosaic", "raw"))
    require_space(
        [(out, image.shape[0] * image.shape[1] * 2 + 16384, "Provisional tissue and unknown maps")],
        ctx.config["storage"],
    )
    covered = read_image(ctx.asset("mosaic", "coverage"))
    small = reduced(image, 1024)
    valid = reduced(covered, 1024).astype(bool)
    values, reference_sampling = covered_sample(image, covered, 1024)
    settings = ctx.config["tissue"]
    if not len(values):
        raise ValueError("Empty acquisition coverage")
    baseline = float(np.quantile(values, settings["quantile"]))
    lower = values[values <= baseline]
    mad = float(1.4826 * np.median(np.abs(lower - np.median(lower))))
    high = baseline + max(1.0, settings["mad_multiplier"] * mad)
    low = baseline + max(0.5, 2 * mad)
    weak = (small > low) & valid
    candidate = ndimage.binary_propagation((small > high) & valid, mask=weak)
    paths = {"candidate": out / "possible-tissue.tif", "unknown": out / "unknown.tif"}
    maps = {
        name: tifffile.memmap(
            path,
            shape=image.shape,
            dtype=np.uint8,
            bigtiff=True,
            metadata={"axes": "YX", "validated_segmentation": False},
        )
        for name, path in paths.items()
    }
    # Coordinates are explicit: this is a coarse provisional support map.
    step = max(1, int(np.ceil(max(image.shape) / 1024)))
    xs = np.minimum(np.arange(image.shape[1]) // step, small.shape[1] - 1)
    for y in range(0, image.shape[0], 256):
        ys = np.minimum(np.arange(y, min(image.shape[0], y + 256)) // step, small.shape[0] - 1)
        block = candidate[ys[:, None], xs[None, :]]
        acquisition = np.asarray(covered[y : y + 256], bool)
        maps["candidate"][y : y + 256] = block & acquisition
        maps["unknown"][y : y + 256] = ~block & acquisition
    for v in maps.values():
        v.flush()
    del maps
    metadata = out / "mask-metadata.json"
    write_json(
        metadata,
        {
            "coordinate_space": "native_af_pixels",
            "coarse_sampling_step": step,
            "reference_sampling": reference_sampling,
            "reference_samples": len(values),
            "coarse_grid_acquired_samples": int(valid.sum()),
            "thresholds": {"weak": low, "strong": high},
            "candidate_is_ground_truth": False,
            "unknown_meaning": "Covered area without adequate signal for provisional tissue support; not confirmed background",
            "blurred_tissue_percentage": None,
            "tissue_denominator_validated": False,
        },
    )
    return Payload(
        {**paths, "metadata": metadata},
        {
            "coarse_sampling_step": step,
            "reference_sampling": reference_sampling,
            "coarse_grid_acquired_samples": int(valid.sum()),
            "tissue_denominator_validated": False,
            "blurred_tissue_percentage": None,
        },
        [
            "Tissue support is provisional. Dim/unclassified covered regions remain unknown.",
            "No fold class, blur segmentation or accepted artifact-area denominator is produced.",
        ]
        + (
            ["Coarse grid missed acquired pixels; all native acquisition remains unknown."]
            if not np.any(valid)
            else []
        ),
    )
