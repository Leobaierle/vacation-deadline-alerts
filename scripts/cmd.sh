#!/usr/bin/env bash
# Envia comandos para scripts/sessao.mjs e espera terminarem.
# Uso: scripts/cmd.sh "click 433 89" "wait 2000" "snap menu-calculos"
set -euo pipefail
CTL="$(cd "$(dirname "$0")/.." && pwd)/ctl"
antes=$(grep -c . "$CTL/cmds.txt" || true)
alvo=$((antes + $#))
printf '%s\n' "$@" >> "$CTL/cmds.txt"
for _ in $(seq 1 "${TIMEOUT:-180}"); do
  [ "$(cat "$CTL/feitos.txt" 2>/dev/null || echo 0)" -ge "$alvo" ] && exit 0
  sleep 1
done
echo "timeout esperando a sessão executar os comandos" >&2
exit 1
