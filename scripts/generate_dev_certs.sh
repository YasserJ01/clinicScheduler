#!/bin/bash
# Generate self-signed development certificates for local TLS testing.
# Usage: ./scripts/generate_dev_certs.sh

set -euo pipefail

CERT_DIR="$(cd "$(dirname "$0")/.." && pwd)/nginx/ssl"
mkdir -p "$CERT_DIR"

echo "Generating self-signed TLS certificates in $CERT_DIR..."

openssl req -x509 -newkey rsa:4096 \
    -keyout "$CERT_DIR/server.key" \
    -out "$CERT_DIR/server.crt" \
    -days 365 \
    -nodes \
    -subj "/C=US/ST=State/L=City/O=ClinicScheduler/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

echo "Certificates generated successfully."
echo "  Certificate: $CERT_DIR/server.crt"
echo "  Private key: $CERT_DIR/server.key"
echo ""
echo "To use these in development, copy nginx/nginx.conf.tls to nginx/nginx.conf"
echo "and run: docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d"
