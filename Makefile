.PHONY: help dev docker-up docker-down logs clean setup-attrs test-replay

help:
	@echo ""
	@echo "Flourish & Blotts OMS — Make targets"
	@echo "--------------------------------------"
	@echo "  make dev          Start Temporal dev server (run this first)"
	@echo "  make setup-attrs  Register custom Temporal search attributes"
	@echo "  make docker-up    Build & start all Docker services"
	@echo "  make docker-down  Stop all Docker services"
	@echo "  make logs         Tail all container logs"
	@echo "  make codespace    Start all services locally (no Docker)"
	@echo "  make test-replay  Run replay regression harness against captured histories"
	@echo ""

dev:
	@echo "Starting Temporal dev server on :7233 (UI on :8233)..."
	temporal server start-dev --ui-port 8233

setup-attrs:
	@echo "Registering custom search attributes..."
	temporal operator search-attribute create \
		--namespace default \
		--name OrderId --type Keyword \
		--name CustomerName --type Keyword \
		--name BookTitle --type Keyword \
		--name OrderStatus --type Keyword \
		--name FailureType --type Keyword \
		--name RepairOutcome --type Keyword \
		--name RequiresHITL --type Bool \
		--name RepairAttempts --type Int \
		|| echo "(attributes may already exist)"

test-replay:
	python -m tests.test_replay

docker-up:
	@[ -f .env ] || (echo "Copy .env.example to .env and fill in credentials first"; exit 1)
	docker compose up --build -d
	@echo ""
	@echo "Services started:"
	@echo "  UI:   http://localhost:3000"
	@echo "  API:  http://localhost:8000"
	@echo "  (Temporal must be running separately: make dev)"

docker-down:
	docker compose down

logs:
	docker compose logs -f

codespace:
	bash scripts/start-codespace.sh

clean:
	docker compose down --rmi local --volumes
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf ui/dist ui/node_modules logs/
