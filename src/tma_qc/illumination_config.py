"""Core-scoped illumination settings; safe to inspect without imaging extras."""

PROFILE_NAME = "basicpy_mist_core_v1"
FIT_SETTINGS = {
    "fitting_mode": "approximate",
    "working_size": 128,
    "get_darkfield": True,
    "sort_intensity": True,
    "max_iterations": 500,
    "max_reweight_iterations": 10,
    "max_reweight_iterations_baseline": 5,
    "epsilon": 0.1,
    "mu_coef": 12.5,
    "max_mu_coef": 10000000.0,
    "optimization_tol": 0.001,
    "optimization_tol_diff": 0.01,
    "reweighting_tol": 0.01,
    "rho": 1.5,
    "sparse_cost_darkfield": 0.01,
}
ILLUMINATION_DEFAULTS = {
    "mode": "cidre",
    "device": "cuda",
    "basicpy_python": "",
    "min_tiles_warning": 16,
    "holdout": "none",
    "basicpy": FIT_SETTINGS,
}


def validate_profile(config):
    illumination, geometry = config["illumination"], config["geometry"]
    mode = illumination["mode"]
    if mode not in {"cidre", "none", "basicpy"}:
        raise ValueError("illumination.mode must be cidre, none or basicpy")
    if geometry["input"] not in {"raw", "corrected"}:
        raise ValueError("geometry.input must be raw or corrected")
    corrected = geometry["input"] == "corrected"
    if (mode == "basicpy") != corrected or (corrected and geometry["backend"] != "mist"):
        raise ValueError("basicpy requires corrected MIST; corrected MIST requires basicpy")
    if mode != "basicpy":
        return  # New profile settings have no effect on the released modes.
    if geometry["downsample"] != 1:
        raise ValueError("basicpy corrected MIST requires native geometry.downsample = 1")
    budget = config["runtime"]["read_threads"]
    if not 1 <= budget <= 8 or not 1 <= geometry["threads"] <= budget:
        raise ValueError("basicpy requires 1 <= geometry.threads <= runtime.read_threads <= 8")
    if illumination["device"] not in {"cpu", "cuda"}:
        raise ValueError("illumination.device must be cpu or cuda (no automatic fallback)")
    if illumination["holdout"] not in {"none", "declared"}:
        raise ValueError("illumination.holdout must be none or declared")
    if illumination["min_tiles_warning"] < 1:
        raise ValueError("illumination.min_tiles_warning must be positive")
    fit = illumination["basicpy"]
    if fit["fitting_mode"] != "approximate":
        raise ValueError("basicpy_mist_core_v1 requires approximate fitting")
    for name, value in fit.items():
        if name in {"fitting_mode", "get_darkfield", "sort_intensity"}:
            continue
        if value < 0 or (value == 0 and name != "sparse_cost_darkfield"):
            raise ValueError(f"illumination.basicpy.{name} must be positive")
