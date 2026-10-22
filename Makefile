TMA_PYTHON ?= python3
TMA_CHECK_PATHS = src tests experiments examples tma_qc check_transfer.py check_registration.py make_montage.py

.PHONY: check lint format-check format test tutorial

check: lint format-check test

lint:
	$(TMA_PYTHON) -m ruff check $(TMA_CHECK_PATHS)

format-check:
	$(TMA_PYTHON) -m ruff format --check $(TMA_CHECK_PATHS)

format:
	$(TMA_PYTHON) -m ruff check --fix $(TMA_CHECK_PATHS)
	$(TMA_PYTHON) -m ruff format $(TMA_CHECK_PATHS)

test:
	$(TMA_PYTHON) -m pytest -q

tutorial:
	$(TMA_PYTHON) docs/tutorial/build_pdf.py
