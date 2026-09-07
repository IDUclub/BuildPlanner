SHELL := /bin/bash
CODE := app tests

install:
	poetry install

install-dev:
	poetry install --with dev

format:
	poetry run isort $(CODE)
	poetry run black $(CODE)

lint:
	poetry run pylint app

test:
	poetry run pytest -q

run:
	poetry run uvicorn app.main:app --reload --port 8080

.PHONY: install install-dev format lint test run
