#!/usr/bin/env bash
# Lab OpenBao (deploy/compose/vault.yml): initialise and unseal it, enable Transit, create the key
# `forge`, a policy and a token for Forge. Safe to run again: it only unseals once initialised.
#
#   scripts/vault-setup.sh            first time: writes deploy/vault/init.json (KEEP IT SAFE, never commit)
#   scripts/vault-setup.sh unseal     after a restart
#
# Then in the console (Super Admin → Secrets vault): address http://openbao:8200, the token printed
# at the end, key `forge`, mount `transit`. In production run these steps on your own server.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
init_file="$root/deploy/vault/init.json"
compose=(docker compose -f deploy/compose/docker-compose.yml -f deploy/compose/vault.yml
         --project-directory "$root" --env-file "$root/.env" --env-file "$root/deploy/compose/infra.env")
bao() { "${compose[@]}" exec -T -e BAO_ADDR=http://127.0.0.1:8200 ${BAO_TOKEN:+-e BAO_TOKEN="$BAO_TOKEN"} openbao bao "$@"; }

for _ in $(seq 1 30); do
  rc=0
  bao status >/dev/null 2>&1 || rc=$?
  [[ $rc -eq 0 || $rc -eq 2 ]] && break  # exit 2 = sealed, which is fine here
  sleep 1
done

if [[ ! -s "$init_file" ]]; then
  umask 077
  bao operator init -key-shares=3 -key-threshold=2 -format=json > "$init_file"
  echo "initialised; unseal keys and root token are in $init_file"
fi
for i in 0 1; do
  key="$(python3 -c "import json,sys; print(json.load(open('$init_file'))['unseal_keys_b64'][$i])")"
  bao operator unseal "$key" >/dev/null
done
echo "unsealed"
[[ "${1:-}" == "unseal" ]] && exit 0

root_token="$(python3 -c "import json; print(json.load(open('$init_file'))['root_token'])")"
export BAO_TOKEN="$root_token"
bao secrets list -format=json | grep -q '"transit/"' || bao secrets enable transit
bao read transit/keys/forge >/dev/null 2>&1 || bao write -f transit/keys/forge type=aes256-gcm96
"${compose[@]}" exec -T -e BAO_ADDR=http://127.0.0.1:8200 -e BAO_TOKEN="$root_token" openbao \
  bao policy write forge - < "$root/deploy/vault/forge-policy.hcl"
token="$(bao token create -policy=forge -period=768h -format=json | python3 -c "import json,sys; print(json.load(sys.stdin)['auth']['client_token'])")"
echo
echo "Forge token (renewable, 32 days; renew or use AppRole in production): $token"
echo "Address for the console: http://openbao:8200   mount: transit   key: forge"
