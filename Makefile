PY ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

.PHONY: install run test lint clean

install:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -r requirements-dev.txt
	$(BIN)/pip install -e . --no-deps

run:
	$(BIN)/python -m reliability.demo --out docs/sample_output --data-dir data/demo

test:
	$(BIN)/pytest

lint:
	$(BIN)/ruff check .

clean:
	rm -rf data out .pytest_cache .ruff_cache src/*.egg-info
