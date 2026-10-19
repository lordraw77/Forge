#!/bin/sh
# Renders /tmp/forge-nginx/default.conf (included by conf.d/default.conf) from the template, with FORGE_TRUSTED_PROXIES
# ("10.0.0.0/8, 192.168.1.10") turned into nginx realip and geo entries.
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
awk -v geo="$geo" -v real="$real" '
  /^\$\{FORGE_GEO_ENTRIES\}$/ { printf "%s", geo; next }
  /^\$\{FORGE_REAL_IP_DIRECTIVES\}$/ { printf "%s", real; next }
  { print }
' "$tpl" > "$out"
