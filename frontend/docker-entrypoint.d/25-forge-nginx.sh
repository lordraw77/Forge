#!/bin/sh
# Renders /tmp/forge-nginx/default.conf (included by conf.d/default.conf) from the template, with FORGE_TRUSTED_PROXIES
# ("10.0.0.0/8, 192.168.1.10") turned into nginx realip and geo entries, and FORGE_DNS_RESOLVER / FORGE_API_UPSTREAM.
set -eu
tpl=/etc/nginx/forge/default.conf.template
out=/tmp/forge-nginx/default.conf  # /etc is read-only at run time
mkdir -p /tmp/forge-nginx
geo=""
real=""
for raw in $(printf '%s' "${FORGE_TRUSTED_PROXIES:-}" | tr ',' ' '); do
  case "$raw" in
    *[!0-9a-fA-F:./]*) echo "forge: ignoring invalid trusted proxy '$raw'" >&2; continue ;;
  esac
  geo="${geo}    ${raw} 1;
"
  real="${real}    set_real_ip_from ${raw};
"
done
resolver="${FORGE_DNS_RESOLVER:-127.0.0.11}"
if [ "$resolver" = auto ]; then
  resolver="$(awk '$1 == "nameserver" { print $2; exit }' /etc/resolv.conf)"
  case "$resolver" in *:*) resolver="[$resolver]" ;; esac
fi
upstream="${FORGE_API_UPSTREAM:-api:8000}"
case "$resolver$upstream" in
  *[!0-9a-zA-Z:.\[\]_-]*) echo "forge: invalid FORGE_DNS_RESOLVER or FORGE_API_UPSTREAM" >&2; exit 1 ;;
esac
awk -v geo="$geo" -v real="$real" -v resolver="$resolver" -v upstream="$upstream" '
  /^\$\{FORGE_GEO_ENTRIES\}$/ { printf "%s", geo; next }
  /^\$\{FORGE_REAL_IP_DIRECTIVES\}$/ { printf "%s", real; next }
  { gsub(/\$\{FORGE_DNS_RESOLVER\}/, resolver); gsub(/\$\{FORGE_API_UPSTREAM\}/, upstream); print }
' "$tpl" > "$out"
