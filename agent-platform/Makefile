.PHONY: up down logs test test-local

up:            ## поднять всё: http://localhost:8000
	docker compose up --build -d
	@echo "Интерфейс: http://localhost:8000"

down:
	docker compose down

logs:
	docker compose logs -f platform

test:          ## проверки границ + примеры из agents/*/tests
	docker compose --profile test run --rm --build tests

test-local:    ## то же без Docker; нужен Postgres в TEST_DATABASE_URL
	cd platform && python -m pytest -v
