import cv2
import numpy as np
import pytest
import tifffile

from tma_qc.backends.core_registration.config import Matching
from tma_qc.backends.core_registration.geometry import distinct_pose, geometric_checks, initialize
from tma_qc.backends.core_registration.images import View, resize_to_native, transform_points
from tma_qc.backends.core_registration.transforms import Transform, export_native


def view(mask, shape=None, mpp=None):
    return View(
        mask.astype(np.float32),
        np.repeat(mask[..., None], 3, axis=2),
        mask,
        resize_to_native(shape or mask.shape, mask.shape),
        shape or mask.shape,
        mpp,
        "test",
    )


def test_resize_preserves_pixel_centres_at_unequal_scales():
    matrix = resize_to_native((511, 777), (128, 194))
    point = transform_points(matrix, [[0, 0], [193, 127]])
    np.testing.assert_allclose(point[0], [(777 / 194 - 1) / 2, (511 / 128 - 1) / 2])
    np.testing.assert_allclose(point[1], [777 - (777 / 194 + 1) / 2, 511 - (511 / 128 + 1) / 2])


def test_native_transform_accounts_for_both_resizes():
    moving = resize_to_native((890, 1110), (300, 400))
    fixed = resize_to_native((1500, 1700), (512, 600))
    affine = np.array([[0.8, -0.2, 31], [0.2, 0.8, -13], [0, 0, 1.0]])
    transform = Transform(affine, fixed, moving)
    points = np.array([[100.0, 111.0], [600.0, 580.0]])
    predicted = transform_points(transform.affine_native, points)
    np.testing.assert_allclose(transform.af_to_he(predicted), points)
    forward, valid = transform.he_to_af(points)
    np.testing.assert_allclose(forward, predicted)
    assert valid.all()


def test_local_pull_sign_and_inverse():
    field = np.zeros((64, 64, 2), np.float32)
    field[..., 0] = 3
    field[..., 1] = -2
    transform = Transform(np.eye(3), np.eye(3), np.eye(3), field)
    source = np.array([[20.0, 25.0], [30.0, 40.0]])
    target, valid = transform.he_to_af(source)
    assert valid.all()
    np.testing.assert_allclose(target, source - [3, -2], atol=1e-5)
    np.testing.assert_allclose(transform.af_to_he(target), source, atol=1e-5)


def test_noninvertible_local_field_does_not_report_success():
    yy, xx = np.indices((64, 64))
    field = np.stack([-2 * xx, np.zeros_like(yy)], axis=-1).astype(np.float32)
    transform = Transform(np.eye(3), np.eye(3), np.eye(3), field)
    _, valid = transform.he_to_af([[20.0, 25.0]])
    assert not valid.all()


def test_geometry_centres_multiple_fragments_without_inventing_rotation():
    mask = np.zeros((100, 120), bool)
    mask[20:50, 25:55] = True
    mask[70:90, 90:110] = True
    fixed, moving = view(mask), view(np.roll(mask, 10, axis=0))
    initialized = initialize(fixed, moving, 0)
    np.testing.assert_allclose(initialized, [[1, 0, 0], [0, 1, -10], [0, 0, 1]], atol=1e-8)


def test_equivalent_rotation_hypotheses_do_not_create_ambiguity():
    mask = np.ones((100, 100), bool)
    assert not distinct_pose(np.eye(3), np.eye(3), mask, 8)
    rotated = np.array([[-1, 0, 99], [0, -1, 99], [0, 0, 1.0]])
    assert distinct_pose(np.eye(3), rotated, mask, 8)


@pytest.mark.parametrize(
    "linear,reason",
    [
        ([[-1, 0], [0, 1]], "reflection_or_singular_transform"),
        ([[2, 0], [0, 0.5]], "excessive_anisotropy"),
        ([[4, 0], [0, 4]], "implausible_relative_scale"),
    ],
)
def test_global_transform_constraints(linear, reason):
    affine = np.eye(3)
    affine[:2, :2] = linear
    assert reason in geometric_checks(affine, 1, Matching())


def test_chunked_rgb_native_export_has_no_seams_and_uses_white_background(tmp_path):
    rng = np.random.default_rng(9)
    image = rng.integers(0, 255, (151, 179, 3), np.uint8)
    affine = np.array([[0.98, -0.08, 13.2], [0.08, 0.98, -5.6], [0, 0, 1.0]])
    transform = Transform(affine, np.eye(3), np.eye(3))
    export_native(image, (180, 201), transform, tmp_path, chunk=64)
    actual = tifffile.imread(tmp_path / "he-registered.tif")
    yy, xx = np.indices((180, 201), dtype=np.float64)
    source = transform.af_to_he(np.column_stack([xx.ravel(), yy.ravel()]))
    expected = cv2.remap(
        image,
        source[:, 0].reshape(180, 201).astype(np.float32),
        source[:, 1].reshape(180, 201).astype(np.float32),
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )
    # remap's 1/32-pixel interpolation bins can differ by one bin after ROI subtraction.
    assert np.percentile(np.abs(actual.astype(float) - expected), 99.9) <= 1
    assert np.abs(actual.astype(float) - expected).max() <= 8
    assert np.all(actual[-1, -1] == 255)


def test_native_export_applies_local_pull_after_global_alignment(tmp_path):
    image = np.arange(100 * 100, dtype=np.uint16).reshape(100, 100)
    field = np.zeros((100, 100, 2), np.float32)
    field[..., 0] = 2
    field[..., 1] = 3
    transform = Transform(np.eye(3), np.eye(3), np.eye(3), field)
    export_native(image, image.shape, transform, tmp_path, 64)
    output = tifffile.imread(tmp_path / "he-registered.tif")
    np.testing.assert_array_equal(output[:90, :90], image[3:93, 2:92])


def test_saved_transform_round_trip_with_local_field(tmp_path):
    import json

    field = np.zeros((64, 64, 2), np.float32)
    field[..., 0] = 1
    affine = np.array([[0.8, -0.1, 5], [0.1, 0.8, 2], [0, 0, 1.0]])
    original = Transform(affine, np.eye(3), np.eye(3), field)
    np.save(tmp_path / "field.npy", field)
    path = tmp_path / "transform.json"
    path.write_text(json.dumps(original.record("field.npy")))
    loaded = Transform.load(path)
    points = np.array([[22.0, 25.0], [32.0, 35.0]])
    np.testing.assert_allclose(loaded.af_to_he(points), original.af_to_he(points))


def test_calibrated_geometry_scale_takes_precedence_over_missing_tissue():
    from tma_qc.backends.core_registration.geometry import scale_estimate

    fixed_mask = np.ones((100, 100), bool)
    moving_mask = fixed_mask.copy()
    moving_mask[:50] = False
    fixed = view(fixed_mask, (400, 400), 0.25)
    moving = view(moving_mask, (200, 200), 0.5)
    scale, source = scale_estimate(fixed, moving)
    assert scale == 1
    assert source == "calibrated_isotropic_pixel_sizes"


def test_consensus_rejects_clustered_but_numerous_matches():
    from tma_qc.backends.core_registration.matching import Matches
    from tma_qc.backends.core_registration.registration import fit_consensus

    points = np.random.default_rng(3).uniform(40, 50, (100, 2))
    result = fit_consensus(
        Matches(points, points + [2, 3], np.ones(100)),
        1,
        np.ones((200, 200), bool),
        np.ones((200, 200), bool),
        Matching(),
        17,
    )
    assert result.metrics["fit_inliers"] == 100
    assert "clustered_spatial_support" in result.reasons


def test_bounded_native_sampling_is_partition_invariant_for_residual_map():
    from tma_qc.backends.core_registration.transforms import sample_native

    rng = np.random.default_rng(71)
    image = rng.integers(0, 65535, (151, 179, 3), np.uint16)
    yy, xx = np.indices((180, 201), dtype=float)
    grid = np.stack([xx + 0.17 * np.sin(yy / 7) - 7.2, yy + 0.23 * np.cos(xx / 6) - 4.1], axis=-1)
    whole, coverage = sample_native(image, grid, grid.shape[:2], 65535)
    pieces = np.empty_like(whole)
    for y in range(0, 180, 31):
        for x in range(0, 201, 43):
            patch = grid[y : y + 31, x : x + 43]
            pieces[y : y + 31, x : x + 43], _ = sample_native(image, patch, patch.shape[:2], 65535)
    np.testing.assert_array_equal(whole, pieces)
    assert coverage.dtype == bool


def test_extreme_scale_native_sampling_subdivides_source_roi():
    from tma_qc.backends.core_registration.transforms import sample_native

    image = np.arange(40000, dtype=np.uint16).reshape(1, 40000)
    points = np.array([[0, 0], [9999, 0], [19999, 0], [29999, 0], [39999, 0]], float)
    result, valid = sample_native(image, points, (1, 5), 65535)
    np.testing.assert_array_equal(result, image[:, [0, 9999, 19999, 29999, 39999]])
    assert valid.all()


def test_residual_landmark_csv_evaluates_full_mapping(tmp_path):
    from tma_qc.qc.registration import evaluate_landmarks

    field = np.zeros((64, 64, 2), np.float32)
    field[..., 0] = 3
    field[..., 1] = -2
    transform = Transform(np.eye(3), np.eye(3), np.eye(3), field)
    path = tmp_path / "held-out.csv"
    path.write_text("he_x,he_y,af_x,af_y\n20,25,17,27\n30,40,27,42\n")
    report = evaluate_landmarks(transform, path, af_shape=(64, 64), he_shape=(64, 64))
    assert report["maximum_error_native_af_pixels"] < 1e-6
    assert report["used_for_fitting_or_candidate_selection"] is False
    path.write_text("he_x,he_y,af_x,af_y\n90,25,17,27\n")
    with pytest.raises(ValueError, match="inside"):
        evaluate_landmarks(transform, path, af_shape=(64, 64), he_shape=(64, 64))


def test_saved_research_transform_and_residual_asset_are_not_dropped(tmp_path):
    import json

    from tma_qc.qc.registration import load_registration_transform, transform_asset_paths

    field = np.zeros((64, 64, 2), np.float32)
    field[..., 0] = 2
    np.save(tmp_path / "field.npy", field)
    path = tmp_path / "transform.json"
    path.write_text(
        json.dumps(Transform(np.eye(3), np.eye(3), np.eye(3), field).record("field.npy"))
    )
    loaded, _ = load_registration_transform(path)
    np.testing.assert_allclose(loaded.af_to_he([[10, 20]]), [[12, 20]])
    assert transform_asset_paths(path) == [path, tmp_path / "field.npy"]
    record = json.loads(path.read_text())
    record["local_pull_displacement_file"] = "../field.npy"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="beside"):
        load_registration_transform(path)


def test_matching_interface_cannot_access_evaluation_landmarks():
    import inspect

    from tma_qc.backends.core_registration import fit
    from tma_qc.backends.core_registration.registration import register_global

    assert "landmarks" not in inspect.signature(fit).parameters
    assert set(inspect.signature(register_global).parameters) == {
        "fixed",
        "moving",
        "method",
        "config",
        "roma",
    }


@pytest.mark.parametrize("color", [False, True])
def test_streamed_area_view_preserves_opencv_frame_for_planar_endian_images(color):
    from tma_qc.backends.core_registration.images import bounded_area_resize

    shape = (151, 213, 3) if color else (151, 213)
    source = np.random.default_rng(52).integers(0, 65000, shape, np.uint16)
    expected = cv2.resize(source, (79, 67), interpolation=cv2.INTER_AREA)
    if color:
        source = np.moveaxis(np.ascontiguousarray(np.moveaxis(source, 2, 0)), 0, 2)
    source = source.astype(">u2")
    actual = bounded_area_resize(source, (67, 79))
    assert np.max(np.abs(actual.astype(float) - expected)) <= 1


def test_optional_roma_is_cache_only(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    pytest.importorskip("romatch")
    import torch

    from tma_qc.backends.core_registration.matching import BackendUnavailable, cached_weights

    monkeypatch.setattr(torch.hub, "get_dir", lambda: str(tmp_path))

    def forbidden(*args, **kwargs):
        pytest.fail("Registration attempted a model download")

    monkeypatch.setattr(torch.hub, "load_state_dict_from_url", forbidden)
    with pytest.raises(BackendUnavailable, match="Missing cached"):
        cached_weights()


def test_bounded_refinement_preserves_positive_jacobian():
    pytest.importorskip("SimpleITK")
    from dataclasses import replace

    from tma_qc.backends.core_registration.config import Experiment, Refinement
    from tma_qc.backends.core_registration.refinement import deformation_metrics, refine

    image = view(np.ones((96, 96), bool))
    image.gray = np.random.default_rng(32).random((96, 96), dtype=np.float32)
    config = replace(Experiment(), refinement=Refinement(backend="bspline_mi", iterations=3))
    field, metrics = refine(image, image, np.eye(3), config)
    assert metrics["status"] in {"rejected", "applied_experimental"}
    assert metrics["independently_validated"] is False
    if field is not None:
        assert deformation_metrics(field)["minimum_jacobian"] >= config.refinement.min_jacobian


def test_missing_optional_refinement_runtime_is_explicitly_blocked(tmp_path, monkeypatch):
    import importlib.metadata

    from tma_qc.backends.core_registration import runtime_identity
    from tma_qc.schema import NeedsInput

    path = tmp_path / "core.toml"
    path.write_text("[refinement]\nbackend='bspline_mi'\n")
    original = importlib.metadata.version

    def version(name):
        if name == "SimpleITK":
            raise importlib.metadata.PackageNotFoundError(name)
        return original(name)

    monkeypatch.setattr(importlib.metadata, "version", version)
    with pytest.raises(NeedsInput, match="SimpleITK"):
        runtime_identity({"core_config": str(path), "core_method": "geometry_sift"})


@pytest.mark.parametrize(
    "content",
    [
        "he_x,he_y,af_x,af_y,fit_confidence,fit_inlier\n1,2,1,2,1,1\n",
        "he_x,he_y,af_x,af_y,he_x\n1,2,1,2,1\n",
        "he_x,he_y,af_x,af_y\n1,2,1,\n",
        "he_x,he_y,af_x,af_y\n1,2,1\n",
        "he_x,he_y,af_x,af_y\n\n",
    ],
)
def test_fitting_csv_and_malformed_evaluation_landmarks_are_rejected(tmp_path, content):
    from tma_qc.qc.registration import evaluate_landmarks

    path = tmp_path / "landmarks.csv"
    path.write_text(content)
    with pytest.raises(ValueError):
        evaluate_landmarks(np.eye(3), path)


@pytest.mark.parametrize("kind", ["int", "bool", "empty", "tiny", "archive", "mismatch"])
def test_invalid_residual_arrays_are_rejected_before_export(tmp_path, kind):
    import json

    field = np.zeros((64, 64, 2), np.float32)
    transform = Transform(np.eye(3), np.eye(3), np.eye(3), field)
    record = transform.record("field.npy")
    if kind == "int":
        field = field.astype(np.int16)
    elif kind == "bool":
        field = field.astype(bool)
    elif kind == "empty":
        field = np.zeros((0, 0, 2), np.float32)
    elif kind == "tiny":
        field = np.zeros((1, 1, 2), np.float32)
    elif kind == "mismatch":
        record["local_field_shape_yx"] = [32, 32]
    if kind == "archive":
        np.savez(tmp_path / "field.npz", field=field)
        record["local_pull_displacement_file"] = "field.npz"
    else:
        np.save(tmp_path / "field.npy", field)
    path = tmp_path / "transform.json"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        Transform.load(path)


def test_residual_grid_must_cover_actual_native_af_domain():
    transform = Transform(np.eye(3), np.eye(3), np.eye(3), np.zeros((2, 2, 2), np.float32))
    with pytest.raises(ValueError, match="actual native"):
        transform.validate_af_domain((100, 100))
    with pytest.raises(ValueError, match="outside"):
        transform.af_to_he([[50, 50]])
    scaled = Transform(
        np.eye(3),
        resize_to_native((100, 150), (20, 30)),
        np.eye(3),
        np.zeros((20, 30, 2), np.float32),
    )
    scaled.validate_af_domain((100, 150))


@pytest.mark.parametrize("bad_key", ["roma", "dinov2"])
def test_none_cached_checkpoint_cannot_trigger_upstream_download(tmp_path, monkeypatch, bad_key):
    torch = pytest.importorskip("torch")
    romatch = pytest.importorskip("romatch")
    from tma_qc.backends.core_registration import matching
    from tma_qc.backends.core_registration.config import Roma

    records = {}
    for key in ("roma", "dinov2"):
        path = tmp_path / (key + ".pth")
        torch.save(None if key == bad_key else {"weight": torch.ones(1)}, path)
        records[key] = {"path": str(path)}
    monkeypatch.setattr(matching, "cached_weights", lambda: records)

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid cached checkpoint reached a model loader/download")

    monkeypatch.setattr(romatch, "roma_outdoor", forbidden)
    monkeypatch.setattr(torch.hub, "load_state_dict_from_url", forbidden)
    with pytest.raises(matching.BackendUnavailable, match="nonempty tensor state"):
        matching.RomaMatcher(Roma(device="cpu"), 17).load()


@pytest.mark.parametrize("encoding", ["csv", "json"])
@pytest.mark.parametrize("duplicate", ["he", "af", "id", "empty_id"])
def test_duplicate_evaluation_coordinates_and_ids_cannot_satisfy_minimum(
    tmp_path, encoding, duplicate
):
    import csv
    import json

    from tma_qc.qc.registration import evaluate_landmarks

    points = [{"id": str(i), "he": [i * 10, i * 20], "af": [i * 10, i * 20]} for i in range(5)]
    if duplicate in {"he", "af"}:
        points[-1][duplicate] = points[0][duplicate]
    elif duplicate == "id":
        points[-1]["id"] = points[0]["id"]
    else:
        points[-1]["id"] = " "
    path = tmp_path / ("landmarks." + encoding)
    if encoding == "json":
        path.write_text(
            json.dumps(
                {"role": "evaluation_only", "coordinate_space": "native_pixels", "points": points}
            )
        )
    else:
        with path.open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["id", "he_x", "he_y", "af_x", "af_y"])
            writer.writerows([[point["id"], *point["he"], *point["af"]] for point in points])
    with pytest.raises(ValueError, match="Duplicate|IDs"):
        evaluate_landmarks(np.eye(3), path, minimum=5)


@pytest.mark.parametrize("encoding", ["csv", "json"])
def test_distinct_five_point_evaluation_set_retains_provisional_tolerance(tmp_path, encoding):
    import csv
    import json

    from tma_qc.qc.registration import evaluate_landmarks

    points = [
        {"id": str(i), "he": [x, y], "af": [x, y]}
        for i, (x, y) in enumerate([(10, 10), (80, 10), (10, 80), (80, 80), (50, 50)])
    ]
    path = tmp_path / ("landmarks." + encoding)
    if encoding == "json":
        path.write_text(
            json.dumps(
                {"role": "evaluation_only", "coordinate_space": "native_pixels", "points": points}
            )
        )
    else:
        with path.open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["id", "he_x", "he_y", "af_x", "af_y"])
            writer.writerows([[point["id"], *point["he"], *point["af"]] for point in points])
    result = evaluate_landmarks(np.eye(3), path, minimum=5)
    assert result["landmarks"] == 5
    assert result["within_provisional_tolerance"]
    assert result["maximum_error_native_af_pixels"] == 0


def test_grayscale_he_proxy_is_invariant_to_supported_numeric_encodings(tmp_path):
    from tma_qc.backends.core_registration.images import prepare_view, structural_gray

    yy, xx = np.indices((128, 128))
    image = np.full((128, 128), 255, np.uint8)
    support = (xx - 64) ** 2 + (yy - 64) ** 2 < 45**2
    image[support] = (30 + (xx + yy)[support] % 120).astype(np.uint8)
    reference = structural_gray(image, "he")
    encodings = [
        image.astype(np.float32),
        image.astype(np.float32) / 255,
        image.astype(np.float64) * 20 - 10000,
        image.astype(np.uint16) * 257,
        image.astype(np.float64) + 1e12,
        image.astype(np.float32) * 1e-12,
        image.astype(np.float64) * 1e-20,
    ]
    for encoded in encodings:
        np.testing.assert_allclose(structural_gray(encoded, "he"), reference, atol=2e-6)
        prepared = prepare_view(encoded, "he", 128, None, None, tmp_path)
        assert prepared.mask.sum() > 500


def test_tapered_permitted_bspline_field_cannot_hide_bilinear_cell_folds():
    from scipy.ndimage import distance_transform_edt

    from tma_qc.backends.core_registration.refinement import deformation_metrics

    support = np.zeros((96, 96), bool)
    support[16:80, 16:80] = True
    field = np.full((96, 96, 2), -12 / np.sqrt(2), np.float32)
    field *= np.minimum(distance_transform_edt(support) / 16, 1)[..., None]
    metrics = deformation_metrics(field)
    assert metrics["maximum_displacement_view_pixels"] <= 12 + 1e-6
    assert metrics["minimum_jacobian"] < 0
    assert metrics["folded_cell_fraction"] > 0
    assert metrics["jacobian_sampling"].startswith("exact_minimum")


def test_nearest_residual_edge_strips_are_checked_for_folds():
    from tma_qc.backends.core_registration.refinement import deformation_metrics

    yy, xx = np.indices((3, 3), dtype=np.float32)
    field = np.stack([2 * yy, -2 * xx - 2 * yy], axis=-1)
    # Interior Jacobian det([[1,2],[-2,-1]])=3; nearest extension on
    # the left/right half-pixel strips has determinant -1.
    metrics = deformation_metrics(field)
    assert metrics["folded_cell_fraction"] == 0
    assert metrics["minimum_edge_strip_jacobian"] == -1
    assert metrics["minimum_jacobian"] == -1
    assert metrics["folded_edge_strip_fraction"] > 0


def test_expanding_residual_includes_identity_jacobian_at_nearest_corner_patches():
    from tma_qc.backends.core_registration.refinement import deformation_metrics

    yy, xx = np.indices((5, 5), dtype=np.float32)
    metrics = deformation_metrics(np.stack([0.5 * xx, 0.5 * yy], axis=-1))
    assert metrics["minimum_edge_strip_jacobian"] == 1.5
    assert metrics["nearest_corner_patch_jacobian"] == 1
    assert metrics["minimum_jacobian"] == 1


def test_cached_model_records_pin_hashed_inode_and_full_identity(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    pytest.importorskip("romatch")
    from tma_qc.backends.core_registration.matching import cached_weights

    cache = tmp_path / "checkpoints"
    cache.mkdir()
    for name in ("roma_outdoor.pth", "dinov2_vitl14_pretrain.pth"):
        (cache / name).write_bytes(b"Small identity hashing probe; not model weights")
    monkeypatch.setattr(torch.hub, "get_dir", lambda: str(tmp_path))
    records = cached_weights()
    for value in records.values():
        from pathlib import Path

        stat = Path(value["path"]).stat()
        assert value["identity"] == [
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        ]


def test_transform_inspection_preserves_frozen_absolute_main_and_companion_names(
    tmp_path, monkeypatch
):
    import json
    from pathlib import Path

    from tma_qc.qc.registration import transform_asset_paths

    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "frozen-name"
    alias.symlink_to(actual, target_is_directory=True)
    field = np.zeros((64, 64, 2), np.float32)
    np.save(actual / "field.npy", field)
    record = Transform(np.eye(3), np.eye(3), np.eye(3), field).record("field.npy")
    (actual / "transform.json").write_text(json.dumps(record))
    path = alias / "transform.json"
    original = Path.resolve

    def resolve(self, *args, **kwargs):
        if self == path:
            pytest.fail("Frozen main transform pathname was resolved again")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert transform_asset_paths(path) == [path, alias / "field.npy"]
    loaded = Transform.load(path)
    np.testing.assert_allclose(loaded.af_to_he([[20, 30]]), [[20, 30]])


def test_residual_companion_symlink_cannot_escape_or_replace_pinned_sibling(tmp_path):
    import json

    field = np.zeros((64, 64, 2), np.float32)
    np.save(tmp_path / "actual-field.npy", field)
    (tmp_path / "field.npy").symlink_to(tmp_path / "actual-field.npy")
    path = tmp_path / "transform.json"
    path.write_text(
        json.dumps(Transform(np.eye(3), np.eye(3), np.eye(3), field).record("field.npy"))
    )
    with pytest.raises(ValueError, match="symlinks"):
        Transform.load(path)


def test_contained_relative_residual_subdirectory_keeps_bundle_compatibility(tmp_path):
    import json

    from tma_qc.qc.registration import transform_asset_paths

    (tmp_path / "residuals").mkdir()
    field = np.zeros((64, 64, 2), np.float32)
    field[..., 0] = 2
    np.save(tmp_path / "residuals/field.npy", field)
    path = tmp_path / "transform.json"
    path.write_text(
        json.dumps(Transform(np.eye(3), np.eye(3), np.eye(3), field).record("residuals/field.npy"))
    )
    assert transform_asset_paths(path) == [path, tmp_path / "residuals/field.npy"]
    loaded = Transform.load(path)
    np.testing.assert_allclose(loaded.af_to_he([[20, 30]]), [[22, 30]])
    (tmp_path / "alias").symlink_to(tmp_path / "residuals", target_is_directory=True)
    record = json.loads(path.read_text())
    record["local_pull_displacement_file"] = "alias/field.npy"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="symlinks"):
        Transform.load(path)
