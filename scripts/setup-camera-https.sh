#!/usr/bin/env bash
set -euo pipefail

host="${1:-}"
if [[ -z "$host" || ! "$host" =~ ^[a-zA-Z0-9.-]+$ ]]; then
  echo "Uso: scripts/setup-camera-https.sh IP_PRIVADA_DEL_SERVIDOR" >&2
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
mkcert -install
mkcert -cert-file "$cert_dir/server/comgraf.pem" -key-file "$cert_dir/server/comgraf-key.pem" "$host"
install -m 644 "$(mkcert -CAROOT)/rootCA.pem" "$cert_dir/rootCA.pem"
chmod 600 "$cert_dir/server/comgraf-key.pem"
printf 'Certificado creado para %s. La CA pública para Android está en %s/rootCA.pem\n' "$host" "$cert_dir"
printf 'La clave privada de la CA permanece en el directorio local de mkcert; no la copies al teléfono.\n'
