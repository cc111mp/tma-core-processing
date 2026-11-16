"""One qualified BaSiCPy worker per run; arrays cross the boundary via local NPY."""

from __future__ import annotations

import json
import selectors
import subprocess
import sys
import uuid
from pathlib import Path

import numpy as np

from ..schema import NeedsInput


class ResidentFitter:
    def __init__(self, settings, threads, log_path):
        python = settings["basicpy_python"] or sys.executable
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log = log_path.open("w")
        self.process = None
        try:
            self.process = subprocess.Popen(
                [
                    python,
                    "-u",
                    str(Path(__file__).with_name("basicpy_worker.py")),
                    settings["device"],
                    str(threads),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self.log,
                text=True,
            )
            hello = self.receive(60)
            if "error" in hello:
                raise NeedsInput(hello["error"])
            self.identity = hello["runtime"]
            self.startup_seconds = hello["startup_seconds"]
        except Exception:
            self.close()
            raise

    def receive(self, timeout):
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout):
                raise RuntimeError("BaSiCPy worker timed out")
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("BaSiCPy worker stopped without a response; inspect worker log")
        return json.loads(line)

    def fit(self, stack_path, indices, settings, output):
        output.mkdir()
        request_id = uuid.uuid4().hex
        self.process.stdin.write(
            json.dumps(
                {
                    "stack": str(stack_path),
                    "request_id": request_id,
                    "indices": indices,
                    "settings": settings,
                    "output": str(output),
                }
            )
            + "\n"
        )
        self.process.stdin.flush()
        try:
            answer = self.receive(1800)
            if answer.get("request_id") != request_id:
                raise RuntimeError("BaSiCPy response does not match the current core request")
        except Exception:
            self.close()
            raise
        if "error" in answer:
            raise RuntimeError(answer["error"])
        fields = [
            np.load(output / (name + ".npy"), allow_pickle=False)
            for name in ("flatfield", "darkfield")
        ]
        if any(field.dtype != np.dtype("=f4") for field in fields):
            raise ValueError("Worker fields must be native float32")
        return *fields, answer["receipt"]

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.stdin.close()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            self.process.stdout.close()
        self.log.close()
