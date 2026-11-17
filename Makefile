# Targets used locally and in CI. `make help` lists them.
UV ?= uv
RUN = $(UV) run --frozen
FROST_URL ?= http://localhost:8080/FROST-Server/v1.1
MICROPYTHON ?= micropython
# Single-threaded numerics so that results are byte-identical between runs.
export OMP_NUM_THREADS = 1
export OPENBLAS_NUM_THREADS = 1
export VECLIB_MAXIMUM_THREADS = 1
export MKL_NUM_THREADS = 1

.PHONY: help setup test lint typecheck micropython smoke reproduce report frost-up frost-down \
        frost-test demo dashboard site ci clean

help:
	@grep -E '^[a-z-]+:' Makefile | cut -d: -f1 | tr '\n' ' '; echo

setup:
	$(UV) sync --frozen

test:
	$(RUN) pytest -q -m "not frost"

lint:
	$(RUN) ruff check src tests scripts
	$(RUN) ruff format --check src tests scripts

typecheck:
	$(RUN) mypy

micropython:
	$(MICROPYTHON) tests/micropython/run.py

smoke:
	$(RUN) flowmine experiments run --config configs/smoke.yaml --out results/smoke
	$(RUN) flowmine experiments report --config configs/smoke.yaml --results results/smoke --no-readme

reproduce:
	$(RUN) flowmine experiments run --config configs/default.yaml --out results
	$(RUN) flowmine experiments report --config configs/default.yaml --results results

report:
	$(RUN) flowmine experiments report --config configs/default.yaml --results results

frost-up:
	docker compose up -d frost
	$(RUN) flowmine sta wait --url $(FROST_URL) --timeout 180

frost-down:
	docker compose down -v

frost-test:
	FROST_URL=$(FROST_URL) $(RUN) pytest -q -m frost tests/contract

demo: frost-up
	$(RUN) flowmine demo --url $(FROST_URL) --config configs/default.yaml

dashboard:
	$(RUN) flowmine dashboard --url $(FROST_URL)

site:
	$(RUN) python scripts/build_site.py
	mkdir -p site/img && cp docs/img/dashboard.png site/img/

ci: lint typecheck test smoke

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache results/smoke
