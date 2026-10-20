#!/usr/bin/env bash
set -euo pipefail

# Backward-compatible name; HTTPS now protects the whole CRM, including scanner pages.
root="$(cd "$(dirname "$0")/.." && pwd)"
exec "$root/scripts/setup-https.sh" "$@"
