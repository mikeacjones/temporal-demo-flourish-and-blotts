#!/bin/bash
set -e

echo "=== Flourish & Blotts OMS — Codespace Setup ==="

# Install Temporal CLI
echo "Installing Temporal CLI..."
curl -sSf https://temporal.download/cli.sh | sh
echo 'export PATH="$HOME/.temporalio/bin:$PATH"' >> ~/.bashrc
export PATH="$HOME/.temporalio/bin:$PATH"

# Install Python dependencies
echo "Installing Python dependencies..."
pip install --no-cache-dir -r requirements.txt

# Install Node dependencies for UI
echo "Installing UI dependencies..."
cd ui && npm install && cd ..

# Copy env file if not present
if [ ! -f .env ]; then
  cp .env.example .env
  echo ""
  echo "⚠️  .env created from .env.example — fill in your API keys before starting services."
fi

echo "=== Setup complete! ==="
