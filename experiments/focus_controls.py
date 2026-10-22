"""Same-patch contrast control and supplied real Z pair; no focus classification."""

import argparse
from pathlib import Path

import numpy as np

from tma_qc.io.images import read_image
from tma_qc.provenance import sha256, write_json
from tma_qc.qc.focus import detail_metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--near", required=True)
    parser.add_argument("--far", required=True)
    parser.add_argument("--x", type=int, required=True)
    parser.add_argument("--y", type=int, required=True)
    parser.add_argument("--size", type=int, default=256)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    near, far = read_image(args.near), read_image(args.far)
    if (
        min(args.x, args.y) < 0
        or args.size < 8
        or any(
            args.x + args.size > a.shape[1] or args.y + args.size > a.shape[0] for a in [near, far]
        )
    ):
        raise ValueError("Patch is outside the supplied native images")
    region = np.s_[args.y : args.y + args.size, args.x : args.x + args.size]
    near = np.asarray(near[region], np.float32)
    far = np.asarray(far[region], np.float32)
    half = near.mean() + 0.5 * (near - near.mean())
    metrics = {
        k: detail_metrics(v)
        for k, v in [("near", near), ("contrast_only_half", half), ("far", far)]
    }
    write_json(
        args.out,
        {
            "scope": "One supplied Z pair; not a detector benchmark or validated blur mask",
            "sources": {str(Path(p).resolve()): sha256(p) for p in [args.near, args.far]},
            "patch_xy_size": [args.x, args.y, args.size],
            "metrics": metrics,
            "contrast_only_normalized_score_ratio": metrics["contrast_only_half"][
                "gradient_over_variance"
            ]
            / metrics["near"]["gradient_over_variance"],
            "near_over_far_normalized_score": metrics["near"]["gradient_over_variance"]
            / metrics["far"]["gradient_over_variance"],
            "blur_segmentation_validated": False,
        },
    )


if __name__ == "__main__":
    main()
