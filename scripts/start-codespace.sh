#!/bin/bash
# Start all services for GitHub Codespace / local development (no Docker)

export PATH="$HOME/.temporalio/bin:$PATH"
WORKSPACE="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$WORKSPACE/logs"
mkdir -p "$LOG_DIR"

# Check if .env exists
if [ ! -f "$WORKSPACE/.env" ]; then
  echo "⚠️  .env not found. Copy .env.example and fill in your credentials."
  exit 1
fi

# Source env
set -a
source "$WORKSPACE/.env"
set +a

# Kill any previously running services
echo "Stopping any previous services..."
pkill -f "temporal server" 2>/dev/null || true
pkill -f "worker.main" 2>/dev/null || true
pkill -f "api.main" 2>/dev/null || true
pkill -f "slack_bot.app" 2>/dev/null || true
pkill -f "vite" 2>/dev/null || true
sleep 1

# 1. Start Temporal dev server
echo "Starting Temporal dev server..."
temporal server start-dev \
  --ui-port 8233 \
  --db-filename "$WORKSPACE/logs/temporal.db" \
  > "$LOG_DIR/temporal.log" 2>&1 &
echo "  PID $! — logs: logs/temporal.log"

# Wait for Temporal to be ready
echo "Waiting for Temporal to start..."
for i in $(seq 1 15); do
  if temporal workflow list --namespace default >/dev/null 2>&1; then
    echo "  Temporal is ready!"
    break
  fi
  sleep 1
done

# 2. Register custom search attributes
echo "Registering custom search attributes..."
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
  2>/dev/null && echo "  Search attributes registered" || echo "  (already registered)"

# 2.5 Start MailHog (fake SMTP) if available locally. In Docker Compose this is a
#     separate service; for Codespaces without Docker we try a standalone binary.
if command -v MailHog >/dev/null 2>&1; then
  echo "Starting MailHog..."
  MailHog > "$LOG_DIR/mailhog.log" 2>&1 &
  echo "  PID $! — logs: logs/mailhog.log (UI on :8025, SMTP on :1025)"
else
  echo "  MailHog binary not found on PATH. Install with: go install github.com/mailhog/MailHog@latest"
  echo "  (Or run via Docker Compose: docker compose up mailhog)"
fi

# 3. Start Temporal worker
echo "Starting Temporal worker..."
cd "$WORKSPACE"
PYTHONPATH="$WORKSPACE" python -m worker.main \
  > "$LOG_DIR/worker.log" 2>&1 &
echo "  PID $! — logs: logs/worker.log"

# 4. Start API server
echo "Starting API server..."
PYTHONPATH="$WORKSPACE" uvicorn api.main:app \
  --host 0.0.0.0 \
  --port "${API_PORT:-8000}" \
  > "$LOG_DIR/api.log" 2>&1 &
echo "  PID $! — logs: logs/api.log"

# 5. Start Slack bot (optional)
if [ -n "$SLACK_BOT_TOKEN" ] && [ -n "$SLACK_APP_TOKEN" ] && [ "$SLACK_BOT_TOKEN" != "xoxb-..." ]; then
  echo "Starting Slack bot..."
  PYTHONPATH="$WORKSPACE" python -m slack_bot.app \
    > "$LOG_DIR/slack-bot.log" 2>&1 &
  echo "  PID $! — logs: logs/slack-bot.log"
else
  echo "  Skipping Slack bot (SLACK_BOT_TOKEN/SLACK_APP_TOKEN not configured)"
fi

# 6. Start UI dev server
echo "Starting UI dev server..."
cd "$WORKSPACE/ui" && npm run dev -- --host \
  > "$LOG_DIR/ui.log" 2>&1 &
echo "  PID $! — logs: logs/ui.log"

echo ""
echo "=== All services started! ==="
echo ""
echo "  Storefront UI:    http://localhost:3000"
echo "  API:              http://localhost:8000"
echo "  Temporal Web UI:  http://localhost:8233"
echo "  MailHog inbox:    http://localhost:8025   (customer HITL emails land here)"
echo ""
echo "  View logs in: $LOG_DIR/"
