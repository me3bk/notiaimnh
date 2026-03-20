#!/bin/bash
# Aymannoti — RSSHub setup script for Ubuntu 24.04
# Run this on your home server: bash setup_rsshub.sh

set -e

echo "=== Aymannoti — RSSHub Setup ==="

# Install Docker if missing
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    sudo apt-get update
    sudo apt-get install -y docker.io docker-compose-v2
    sudo systemctl enable --now docker
    sudo usermod -aG docker "$USER"
    echo "Docker installed. Log out and back in for group changes to take effect."
fi

# Create RSSHub directory
mkdir -p ~/rsshub
cd ~/rsshub

# Write docker-compose config
cat > docker-compose.yml << 'COMPOSE'
services:
  rsshub:
    image: diygod/rsshub:chromium-bundled
    restart: always
    ports:
      - "1200:1200"
    environment:
      # Cache every feed for 2 minutes — safe with 3 load-balanced IPs
      - CACHE_TYPE=memory
      - CACHE_EXPIRE=120
      - REQUEST_TIMEOUT=30000
COMPOSE

echo "Starting RSSHub..."
docker compose up -d

echo ""
echo "RSSHub is running at http://localhost:1200"
echo "Test it:  curl http://localhost:1200/tiktok/user/@tiktok"
echo ""
echo "To stop:      cd ~/rsshub && docker compose down"
echo "To view logs: cd ~/rsshub && docker compose logs -f"
