# Minimal Makefile for a Python + FastAPI project

VENV ?= .venv
PYTHON ?= python3
APP_MODULE ?= app.main:app
HOST ?= 127.0.0.1
PORT ?= 8000

PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python
UVICORN := $(VENV)/bin/uvicorn

.PHONY: setup dev run cli test lint format

# Create virtual environment and install dependencies (placeholder-friendly).
setup:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	@if [ -f requirements.txt ]; then \
		$(PIP) install -r requirements.txt; \
	elif [ -f pyproject.toml ]; then \
		$(PIP) install -e .; \
	else \
		echo "No requirements.txt or pyproject.toml found; skipping dependency install."; \
	fi

# Run FastAPI in development mode with auto-reload.
dev:
	@if [ -x "$(UVICORN)" ]; then \
		$(UVICORN) $(APP_MODULE) --host $(HOST) --port $(PORT) --reload; \
	else \
		echo "uvicorn is not installed. Run 'make setup' and add dependencies."; \
		exit 1; \
	fi

# Run FastAPI normally (no reload).
run:
	@if [ -x "$(UVICORN)" ]; then \
		$(UVICORN) $(APP_MODULE) --host $(HOST) --port $(PORT); \
	else \
		echo "uvicorn is not installed. Run 'make setup' and add dependencies."; \
		exit 1; \
	fi

# Run the project CLI. Usage: make cli ARGS="health"
cli:
	@if [ -x "$(PY)" ]; then \
		$(PY) -m app.cli $(ARGS); \
	else \
		echo "Virtual environment not found. Run 'make setup' first."; \
		exit 1; \
	fi

# Run test suite with pytest.
test:
	@if [ -x "$(PY)" ]; then \
		$(PY) -m pytest; \
	else \
		echo "Virtual environment not found. Run 'make setup' first."; \
		exit 1; \
	fi

# Run lint checks with ruff (fallback to flake8).
lint:
	@if [ -x "$(PY)" ]; then \
		if $(PY) -m ruff check . >/dev/null 2>&1; then \
			$(PY) -m ruff check .; \
		else \
			$(PY) -m flake8 .; \
		fi; \
	else \
		echo "Virtual environment not found. Run 'make setup' first."; \
		exit 1; \
	fi

# Format code with ruff format (fallback to black).
format:
	@if [ -x "$(PY)" ]; then \
		if $(PY) -m ruff format . >/dev/null 2>&1; then \
			$(PY) -m ruff format .; \
		else \
			$(PY) -m black .; \
		fi; \
	else \
		echo "Virtual environment not found. Run 'make setup' first."; \
		exit 1; \
	fi
