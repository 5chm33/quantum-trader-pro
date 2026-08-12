.PHONY: help install format format-check lint typecheck test security docs shell research-evidence build preflight demo quality clean

help:
	@printf '%s\n' \
	  'install       Install the project with development tools' \
	  'format        Format source, tests, scripts, and bootstrap' \
	  'format-check  Verify formatting without changing files' \
	  'lint          Run Ruff lint checks' \
	  'typecheck     Run strict mypy checks' \
	  'test          Run tests with branch coverage' \
	  'security      Run Bandit against production source' \
	  'docs          Validate repository-relative documentation links' \
	  'shell         Validate portable shell launchers' \
	  'research-evidence  Verify retained trial ledgers and locked holdout' \
	  'build         Build source and wheel distributions' \
	  'preflight     Print the simulation-only safety boundary' \
	  'demo          Run and verify the offline bundled demo' \
	  'quality       Run the complete local quality gate' \
	  'clean         Remove generated development artifacts'

install:
	python -m pip install -e '.[dev]'

format:
	ruff format src tests scripts launch_demo.py

format-check:
	ruff format --check src tests scripts launch_demo.py

lint:
	ruff check src tests scripts launch_demo.py

typecheck:
	mypy src

test:
	PYTHONPATH=src pytest --cov=quantum_trader --cov-report=term-missing

security:
	bandit -q -r src

docs:
	python scripts/check-doc-links.py

shell:
	bash -n launch_demo.sh scripts/run-cloud-simulation.sh

research-evidence:
	python scripts/verify-evaluation-evidence.py

build:
	python -m build

preflight:
	PYTHONPATH=src python -m quantum_trader.cli preflight

demo:
	rm -rf .quality-demo
	python launch_demo.py --output .quality-demo >/dev/null
	python scripts/verify-demo-output.py .quality-demo
	@rm -rf .quality-demo

quality: format-check lint typecheck test security docs shell research-evidence build demo

clean:
	rm -rf .coverage .pytest_cache .mypy_cache .ruff_cache build dist htmlcov *.egg-info src/*.egg-info .quality-demo
