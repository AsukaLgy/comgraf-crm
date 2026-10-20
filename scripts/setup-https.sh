#!/usr/bin/env bash
set -euo pipefail

host="${1:-}"
if [[ -z "$host" || ! "$host" =~ ^[a-zA-Z0-9.-]+$ ]]; then
  echo "Uso: scripts/setup-https.sh IP_PRIVADA_O_HOST_DEL_SERVIDOR" >&2
  exit 2
fi
if ! command -v mkcert >/dev/null 2>&1; then
  echo "Instala mkcert desde su guía oficial y vuelve a ejecutar este script." >&2
  exit 2
fi

root="$(cd "$(dirname "$0")/.." && pwd)"
cert_dir="$root/.runtime/camera-https"
mkdir -p "$cert_dir/server"
chmod 700 "$cert_dir" "$cert_dir/server"
mkcert -cert-file "$cert_dir/server/comgraf.pem" \
  -key-file "$cert_dir/server/comgraf-key.pem" \
  "$host" localhost 127.0.0.1
caroot="$(mkcert -CAROOT)"
install -m 644 "$caroot/rootCA.pem" "$cert_dir/rootCA.pem"
install -m 644 "$caroot/rootCA.pem" "$cert_dir/rootCA.crt"
chmod 600 "$cert_dir/server/comgraf-key.pem"
printf 'Certificado HTTPS creado para %s, localhost y 127.0.0.1.\n' "$host"
printf 'Certificado raíz público para dispositivos: %s/rootCA.crt\n' "$cert_dir"
printf 'No copies ni compartas rootCA-key.pem; permanece en %s.\n' "$caroot"
