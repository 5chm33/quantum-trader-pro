.PHONY: help install lock format format-check lint typecheck test security docs shell research-evidence strategy-governance workflow-security build preflight demo quality clean

help:
	@printf '%s\n' \
	  'install       Install the locked project and development tools' \
	  'lock          Verify the committed dependency lockfile is current' \
	  'format        Format source, tests, scripts, and bootstrap' \
	  'format-check  Verify formatting without changing files' \
	  'lint          Run Ruff lint checks' \
	  'typecheck     Run strict mypy checks' \
	  'test          Run tests with branch coverage' \
	  'security      Run Bandit against production source' \
	  'docs          Validate repository-relative documentation links' \
	  'shell         Validate portable shell launchers' \
	  'research-evidence  Verify retained trial ledgers and locked holdout' \
	  'strategy-governance Verify frozen A+ grade, options, and holdout policy' \
	  'workflow-security Verify immutable action references and safe authority' \
	  'build         Build source and wheel distributions' \
	  'preflight     Print the simulation-only safety boundary' \
	  'demo          Run and verify the offline bundled demo' \
	  'quality       Run the complete local quality gate' \
	  'clean         Remove generated development artifacts'

install:
	uv sync --locked --extra dev

lock:
	uv lock --check

format:
	uv run --extra dev ruff format src tests scripts launch_demo.py

format-check:
	uv run --extra dev ruff format --check src tests scripts launch_demo.py

lint:
	uv run --extra dev ruff check src tests scripts launch_demo.py

typecheck:
	uv run --extra dev mypy src tests/helpers/paper_process_worker.py

test:
	uv run --extra dev pytest --cov=quantum_trader --cov-report=term-missing

security:
	uv run --extra dev bandit -q -r src

docs:
	uv run --extra dev python scripts/check-doc-links.py

shell:
	bash -n launch_demo.sh scripts/run-cloud-simulation.sh

research-evidence:
	uv run --extra dev python scripts/verify-evaluation-evidence.py

strategy-governance:
	uv run --extra dev python scripts/verify-strategy-governance.py

workflow-security:
	uv run --extra dev python scripts/verify-workflow-security.py

build:
	uv run --extra dev python -m build

preflight:
	uv run --extra dev quantum-trader preflight

demo:
	rm -rf .quality-demo
	uv run --extra dev python launch_demo.py --output .quality-demo >/dev/null
	uv run --extra dev python scripts/verify-demo-output.py .quality-demo
	@rm -rf .quality-demo

quality: lock format-check lint typecheck test security docs shell research-evidence strategy-governance workflow-security build demo

clean:
	rm -rf .coverage .pytest_cache .mypy_cache .ruff_cache .venv build dist htmlcov *.egg-info src/*.egg-info .quality-demo
