from __future__ import annotations

import os

import numpy as np

from ..provenance import read_json


def validate_transform(matrix):
    matrix = np.asarray(matrix, np.float64)
    if matrix.shape == (2, 3):
        matrix = np.vstack([matrix, [0.0, 0.0, 1.0]])
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise ValueError("Expected a finite 3x3 affine HE-to-AF matrix")
    if not np.allclose(matrix[2], [0, 0, 1]) or abs(np.linalg.det(matrix[:2, :2])) < 1e-10:
        raise ValueError("Transform is not an invertible affine mapping")
    return matrix


def load_transform(path):
    record = read_json(path)
    if (
        record.get("direction_global") == "he_to_af"
        and record.get("coordinate_space") == "original_native_pixel_centres_xy"
    ):
        from ..backends.core_registration.transforms import Transform

        return validate_transform(Transform(*Transform.matrices_from_record(record)).affine_native)
    if record.get("direction") != "he_to_af" or record.get("coordinate_space") != "native_pixels":
        raise ValueError(
            "Transform must declare direction=he_to_af and coordinate_space=native_pixels"
        )
    return validate_transform(record["matrix"])


def load_registration_transform(path):
    """Load the global affine and, when present, its explicit residual pull map."""
    from ..backends.core_registration.transforms import Transform

    record = read_json(path)
    matrix = load_transform(path)
    if (
        record.get("local_pull_displacement_file")
        or record.get("coordinate_space") == "original_native_pixel_centres_xy"
    ):
        transform = Transform.load(path)
        if not np.allclose(transform.affine_native, matrix, rtol=1e-10, atol=1e-10):
            raise ValueError("Global native affine disagrees with the saved research transform")
        if record.get("local_pull_displacement_file") and record.get("nonrigid_applied") is False:
            raise ValueError("Residual field exists but nonrigid_applied is false")
    else:
        if record.get("nonrigid_applied"):
            raise ValueError("Nonrigid transform is missing its residual pull map")
        transform = Transform(matrix, np.eye(3), np.eye(3))
    return transform, record


def transform_asset_paths(path):
    """Return every content-addressed input of a reviewed saved transform."""
    from pathlib import Path

    path = Path(os.path.abspath(path))
    load_registration_transform(path)
    record = read_json(path)
    paths = [path]
    if name := record.get("local_pull_displacement_file"):
        paths.append(Path(os.path.abspath(path.parent / name)))
    return paths


def evaluate_landmarks(matrix, path, tolerance=20.0, minimum=5, *, af_shape=None, he_shape=None):
    if not path:
        return {
            "independent_evaluation_available": False,
            "landmarks": 0,
            "dense_correspondence_validated": False,
        }
    from pathlib import Path

    if Path(path).suffix.lower() == ".csv":
        import csv

        with Path(path).open(newline="", encoding="utf-8-sig") as stream:
            rows = csv.reader(stream)
            required = {"he_x", "he_y", "af_x", "af_y"}
            header = next(rows, [])
            if (
                len(header) != len(set(header))
                or not required <= set(header)
                or set(header) - required - {"id"}
            ):
                raise ValueError(
                    "Evaluation landmark CSV requires unique he_x,he_y,af_x,af_y and optional id only; fitting correspondences are not independent landmarks"
                )
            points = []
            for number, values in enumerate(rows, 2):
                if len(values) != len(header):
                    raise ValueError(
                        f"Malformed evaluation landmark coordinates at CSV row {number}"
                    )
                row = dict(zip(header, values))
                if any(not row[key].strip() for key in required):
                    raise ValueError(
                        f"Malformed evaluation landmark coordinates at CSV row {number}"
                    )
                try:
                    points.append(
                        {
                            "he": [float(row["he_x"]), float(row["he_y"])],
                            "af": [float(row["af_x"]), float(row["af_y"])],
                            **({"id": row["id"]} if "id" in header else {}),
                        }
                    )
                except ValueError as error:
                    raise ValueError(
                        f"Malformed evaluation landmark coordinates at CSV row {number}"
                    ) from error
    else:
        data = read_json(path)
        if data.get("role") != "evaluation_only" or data.get("coordinate_space") != "native_pixels":
            raise ValueError("Landmarks must declare evaluation_only and native_pixels")
        points = data["points"]
    if not points:
        raise ValueError("Empty independent landmark set")
    af = np.array([r["af"] for r in points], np.float64)
    he = np.array([r["he"] for r in points], np.float64)
    if (
        af.shape != (len(points), 2)
        or he.shape != af.shape
        or not np.isfinite(af).all()
        or not np.isfinite(he).all()
    ):
        raise ValueError("Invalid independent landmark coordinates")
    if len(np.unique(he, axis=0)) != len(points):
        raise ValueError("Duplicate H&E coordinate pairs in evaluation landmarks")
    if len(np.unique(af, axis=0)) != len(points):
        raise ValueError("Duplicate AF coordinate pairs in evaluation landmarks")
    if any("id" in point for point in points):
        identifiers = [point.get("id") for point in points]
        if any(not isinstance(value, str) or not value.strip() for value in identifiers):
            raise ValueError(
                "Optional evaluation landmark IDs must be nonempty strings for every point"
            )
        if len({value.strip() for value in identifiers}) != len(points):
            raise ValueError("Optional evaluation landmark IDs must be unique")
    for values, shape, name in ((af, af_shape, "AF"), (he, he_shape, "H&E")):
        if shape is not None and (
            np.any(values < 0)
            or np.any(values[:, 0] > shape[1] - 1)
            or np.any(values[:, 1] > shape[0] - 1)
        ):
            raise ValueError(f"Evaluation landmarks must lie inside the original {name} image")
    if hasattr(matrix, "he_to_af"):
        predicted, converged = matrix.he_to_af(he)
        if not np.all(converged):
            return {
                "independent_evaluation_available": True,
                "landmarks": len(points),
                "assessment": "not_assessed",
                "reason": "Residual pull-map inversion failed for evaluation landmarks",
                "inversion_converged": converged.tolist(),
                "dense_correspondence_validated": False,
                "used_for_fitting_or_candidate_selection": False,
            }
    else:
        predicted = (np.column_stack([he, np.ones(len(he))]) @ validate_transform(matrix).T)[:, :2]
    errors = np.linalg.norm(predicted - af, axis=1)
    return {
        "independent_evaluation_available": True,
        "landmarks": len(points),
        "median_error_native_af_pixels": float(np.median(errors)),
        "p95_error_native_af_pixels": float(np.percentile(errors, 95)),
        "maximum_error_native_af_pixels": float(errors.max()),
        "errors_native_af_pixels": errors.tolist(),
        "af_points": af.tolist(),
        "within_provisional_tolerance": bool(
            len(points) >= minimum and np.percentile(errors, 95) <= tolerance
        ),
        "tolerance_native_af_pixels": tolerance,
        "tolerance_calibrated": False,
        "dense_correspondence_validated": False,
        "used_for_fitting_or_candidate_selection": False,
    }
