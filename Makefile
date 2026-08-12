.PHONY: help install format format-check lint typecheck test security build preflight quality clean

help:
	@printf '%s\n' \
	  'install       Install the project with development tools' \
	  'format        Format source and tests' \
	  'format-check  Verify formatting without changing files' \
	  'lint          Run Ruff lint checks' \
	  'typecheck     Run strict mypy checks' \
	  'test          Run tests with coverage' \
	  'security      Run Bandit against production source' \
	  'build         Build source and wheel distributions' \
	  'preflight     Print the simulation-only safety boundary' \
	  'quality       Run the complete local quality gate' \
	  'clean         Remove generated development artifacts'

install:
	python -m pip install -e '.[dev]'

format:
	ruff format src tests

format-check:
	ruff format --check src tests

lint:
	ruff check src tests

typecheck:
	mypy src

test:
	pytest --cov=quantum_trader --cov-report=term-missing

security:
	bandit -q -r src

build:
	python -m build

preflight:
	quantum-trader preflight

quality: format-check lint typecheck test security build

clean:
	rm -rf .coverage .pytest_cache .mypy_cache .ruff_cache build dist htmlcov *.egg-info src/*.egg-info
