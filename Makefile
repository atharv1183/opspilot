COMPOSE ?= docker compose
RUN     ?= $(COMPOSE) exec api

.PHONY: help up down logs migrate seed eval eval-smoke test lint chat clean

help:  ## Show targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  %-12s %s\n",$$1,$$2}'

up:  ## Build and start postgres+pgvector, SOAP service and API (first start loads data)
	$(COMPOSE) up -d --build
	@echo "Waiting for the API to become healthy (first start migrates + embeds data)..."
	@$(COMPOSE) up -d --wait api
	@echo "API: http://localhost:$${API_HOST_PORT:-8000}/docs   SOAP WSDL: http://localhost:$${SOAP_HOST_PORT:-8001}/?wsdl"

down:  ## Stop everything (keeps the database volume)
	$(COMPOSE) down

logs:  ## Tail service logs
	$(COMPOSE) logs -f --tail=100

migrate:  ## Apply Alembic schema migrations
	$(RUN) alembic upgrade head

seed:  ## Re-run legacy data migration + demo seed + SOP embeddings (wipes ERP data)
	$(RUN) python -m opspilot.bootstrap --force

eval:  ## Run the 50-query evaluation and write eval/results.md
	$(RUN) python eval/run_eval.py

eval-smoke:  ## Run 5 eval queries
	$(RUN) python eval/run_eval.py --limit 5 --no-write

test:  ## Run the test suite (uses the opspilot_test database)
	$(RUN) pytest

lint:  ## Ruff lint + format check
	$(RUN) ruff check . && $(RUN) ruff format --check .

chat:  ## Interactive terminal chat with the agent
	$(RUN) python -m opspilot.chat

clean:  ## Stop and delete the database volume
	$(COMPOSE) down -v
