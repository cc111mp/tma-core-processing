"""Standalone worker: run with the explicitly qualified Python, never upgrade it."""

from __future__ import annotations

import contextlib
import importlib.metadata
import json
import sys
import time
import traceback
from pathlib import Path


def send(value):
    print(json.dumps(value, allow_nan=False), flush=True)


def serve():
    started = time.perf_counter()
    try:
        versions = {p: importlib.metadata.version(p) for p in ("basicpy", "numpy", "torch")}
        if versions["basicpy"] != "2.0.0" or versions["numpy"] != "1.26.4":
            raise RuntimeError(
                "Set illumination.basicpy_python to a BaSiCPy 2.0.0 / NumPy 1.26.4 interpreter"
            )
        with contextlib.redirect_stdout(sys.stderr):
            import numpy as np
            import torch
            from basicpy import BaSiC

            device, threads = sys.argv[1], int(sys.argv[2])
            torch.set_num_threads(threads)
            if device == "cuda":
                if not torch.cuda.is_available():
                    raise RuntimeError(
                        "CUDA unavailable; explicitly select illumination.device = cpu"
                    )
                torch.cuda.init()
                torch.cuda.synchronize()
        runtime = {
            "python": sys.executable,
            "versions": versions,
            "device": device,
            "threads": threads,
            "cuda_device": torch.cuda.get_device_name() if device == "cuda" else None,
        }
        send({"runtime": runtime, "startup_seconds": time.perf_counter() - started})
    except Exception as exc:
        send(
            {
                "error": f"Qualified fitter unavailable: {exc}. Set illumination.basicpy_python to a BaSiCPy 2.0.0 / NumPy 1.26.4 interpreter."
            }
        )
        return
    for line in sys.stdin:
        try:
            request = json.loads(line)
            stack = np.load(request["stack"], mmap_mode="r", allow_pickle=False)
            indices = request["indices"]
            if stack.dtype != np.uint16 or stack.ndim != 3:
                raise ValueError("BaSiCPy requires the verified native uint16 core stack")
            images = stack if indices == list(range(len(stack))) else stack[indices]
            with contextlib.redirect_stdout(sys.stderr):
                start = time.perf_counter()
                model = BaSiC(**request["settings"], device=device)
                model.fit(images, skip_shape_warning=True)
                if device == "cuda":
                    torch.cuda.synchronize()
                elapsed = time.perf_counter() - start
            for name in ("flatfield", "darkfield"):
                field = np.asarray(getattr(model, name), dtype="=f4")
                np.save(Path(request["output"]) / (name + ".npy"), field, allow_pickle=False)
            receipt = {
                "basicpy_version": versions["basicpy"],
                "runtime": runtime,
                "device": device,
                "converged": bool(model._converge_flag),
                "score": float(model.score),
                "reweight_score": float(model.reweight_score),
                "elapsed_fit_seconds": elapsed,
            }
            send({"receipt": receipt, "request_id": request["request_id"]})
            del model, images, stack
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            send({"error": str(exc), "request_id": request.get("request_id")})


if __name__ == "__main__":
    # The sibling adapter basicpy.py must not shadow the installed distribution.
    sys.path.pop(0)
    serve()
