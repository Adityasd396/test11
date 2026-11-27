#!/usr/bin/env bash
# One-command Ubuntu/Debian deployment; run from the extracted source directory.
set -Eeuo pipefail
umask 077

fail() { printf 'Solace setup: %s\n' "$*" >&2; exit 1; }
say() { printf '\n== %s ==\n' "$*"; }
usage() {
    cat <<'HELP'
Usage: sudo bash setup.sh [--host DOMAIN_OR_PUBLIC_IPV4] [--email CONTACT_EMAIL]
Run again from the same app directory to update. Existing instance and secrets stay in place.
Ubuntu/Debian with systemd; outbound HTTPS and public inbound TCP 80/443 required.
First install prompts for missing host/contact. Existing /etc/solace.env is reused.
Optional: --print-nginx HOST PORT UPLOAD_MIB http|https (render only, no changes).
HELP
}

valid_host() {
    [[ $1 =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ && $1 == *.* && $1 != *..* && ${#1} -le 253 ]]
}

render_nginx() {
    local name=$1 port=$2 mib=$3 mode=$4
    valid_host "$name" || fail 'Invalid host.'
    [[ $port =~ ^[1-9][0-9]{0,4}$ && $port -ge 1 && $port -le 65535 ]] || fail 'Invalid port.'
    [[ $mib =~ ^[1-9][0-9]{0,6}$ && $mib -ge 9 ]] || fail 'Invalid upload limit.'
    [[ $mode == http || $mode == https ]] || fail 'Invalid Nginx mode.'
    cat <<EOF
# Managed by Solace setup.sh
server {
    listen 80;
    server_name $name;
    access_log off;
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/solace-acme;
        try_files \$uri =404;
    }
    location / {
EOF
    if [[ $mode == https ]]; then
        printf '        return 308 https://%s$request_uri;\n' "$name"
    else
        printf '        return 503;\n'
    fi
    printf '    }\n}\n'
    [[ $mode == https ]] || return 0
    cat <<EOF

server {
    listen 443 ssl;
    server_name $name;
    ssl_certificate /etc/letsencrypt/live/$name/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$name/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    access_log off;
    client_max_body_size ${mib}m;
    client_body_timeout 120s;
    location / {
        proxy_pass http://127.0.0.1:$port;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header Connection "";
        proxy_send_timeout 120s;
        proxy_read_timeout 120s;
        proxy_buffering off;
    }
}
EOF
}

if [[ ${1:-} == --print-nginx ]]; then
    [[ $# == 5 ]] || fail 'Supply host, port, upload MiB, and http or https.'
    render_nginx "$2" "$3" "$4" "$5"
    exit
fi

host= email=
while (( $# )); do
    case "$1" in
        --help|-h) usage; exit ;;
        --host) [[ $# -ge 2 ]] || fail 'Missing host.'; host=$2; shift 2 ;;
        --email) [[ $# -ge 2 ]] || fail 'Missing email.'; email=$2; shift 2 ;;
        *) fail "Unknown option: $1" ;;
    esac
done
[[ $EUID == 0 ]] || fail 'Run sudo bash setup.sh.'
[[ -d /run/systemd/system && -x /usr/bin/apt-get ]] || fail 'This installer requires Ubuntu/Debian with systemd.'
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
app_dir=$(pwd -P)
[[ -f serve.py && -f requirements.lock && -f app.py ]] || fail 'Extract the full Solace source ZIP first.'
[[ $app_dir =~ ^/[a-zA-Z0-9_./-]+$ ]] || fail 'Use an app directory without spaces or special characters.'
exec 9>/run/lock/solace-setup.lock
flock -n 9 || fail 'Another setup is running.'
trap 'printf "\nSetup stopped at line %s. Check the error above; saved data was not removed.\n" "$LINENO" >&2' ERR

export DEBIAN_FRONTEND=noninteractive
say 'Install server packages'
apt-get update
apt-get install -y nginx curl ca-certificates openssl python3 python3-venv
env_file=/etc/solace.env
unit_file=/etc/systemd/system/solace.service
nginx_file=/etc/nginx/conf.d/solace.conf
service_user=${SUDO_USER:-}
existing=$(systemctl show solace.service -p LoadState --value || true)
[[ $existing == loaded || $existing == not-found ]] || fail 'Cannot determine existing Solace service state.'
if [[ $existing == loaded ]]; then
    old_dir=$(systemctl show solace.service -p WorkingDirectory --value)
    [[ $old_dir == "$app_dir" ]] || fail "Run this installer in the existing service directory: $old_dir"
    service_user=$(systemctl show solace.service -p User --value)
    old_env=$(systemctl show solace.service -p EnvironmentFiles --value)
    [[ $old_env == "$env_file (ignore_errors=no)" || $old_env == "$env_file (ignore_errors=yes)" ]] ||
        fail 'The existing service uses a custom environment file. Retain it and adapt the unit before using this installer.'
    [[ -f $env_file ]] || fail 'Existing service environment file missing; restore it before upgrading.'
fi
[[ -n $service_user && $service_user != root ]] || fail 'Run from a non-root SSH account using sudo, or use the existing non-root Solace service.'
id "$service_user" >/dev/null
service_group=$(id -gn "$service_user")
as_user() { runuser -u "$service_user" -- "$@"; }
as_user test -r "$app_dir/serve.py" || fail 'The service user cannot read the app directory.'

# Do not source credentials as shell code. Read only the simple deployment fields.
read_env() {
    python3 - "$1" "$2" <<'PY'
import pathlib, sys
path, name = pathlib.Path(sys.argv[1]), sys.argv[2]
value = ""
if path.is_file():
    for line in path.read_text().splitlines():
        key, sep, item = line.strip().partition("=")
        if sep and key.strip() == name:
            value = item.strip()
            if value.startswith(("\'", '"')) and value[-1:] == value[0]:
                value = value[1:-1]
print(value)
PY
}

[[ ! -L $env_file && ! -L $nginx_file ]] || fail "Standard configuration paths must be regular files."
if [[ ! -f $env_file ]]; then
    if [[ -f "$app_dir/.env" ]]; then
        install -m 0600 "$app_dir/.env" "$env_file"
    else
        install -m 0600 /dev/null "$env_file"
    fi
fi
chown root:root "$env_file"
chmod 0600 "$env_file"
saved_origin=$(read_env "$env_file" SOLACE_PUBLIC_URL)
saved_host=${saved_origin#https://}
saved_host=${saved_host%/}
host=${host:-$saved_host}
email=${email:-$(read_env "$env_file" SOLACE_CONTACT_EMAIL)}
if [[ -z $host ]]; then
    read -r -p 'Public IPv4 or domain: ' host
fi
if [[ -z $email ]]; then
    read -r -p 'Public contact email: ' email
fi
host=${host,,}
valid_host "$host" || fail 'Enter a domain or a public IPv4 without https:// or a trailing slash.'
[[ $email =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || fail 'Enter a valid contact email.'
ip_type=$(python3 - "$host" <<'PY'
import ipaddress, re, sys
host = sys.argv[1]
try:
    addr = ipaddress.ip_address(host)
except ValueError:
    if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in host.split(".")) or re.fullmatch(r"[0-9.]+", host):
        raise SystemExit("Invalid domain or IPv4")
    print("domain")
else:
    if addr.version != 4 or not addr.is_global:
        raise SystemExit("Use a public IPv4 address.")
    print("ip")
PY
)
if [[ $existing == loaded && -n $saved_host && $host != "$saved_host" ]]; then
    fail 'Existing public URL differs. Use the current host; a domain migration must update site settings and cookie scope separately.'
fi
instance=$(read_env "$env_file" SOLACE_INSTANCE_PATH)
instance=${instance:-"$app_dir/instance"}
[[ $instance =~ ^/[a-zA-Z0-9_./-]+$ && $instance != / ]] || fail 'Instance path must be an absolute private directory.'
if [[ ! -d $instance ]]; then
    install -d -m 0700 -o "$service_user" -g "$service_group" "$instance"
fi
as_user test -w "$instance" || fail 'Existing instance directory is not writable by the Solace service user.'
port=$(read_env "$env_file" PORT)
port=${port:-8080}
[[ $port =~ ^[1-9][0-9]{0,4}$ && $port -le 65535 && $port -ne 80 && $port -ne 443 ]] || fail 'Use an available private application port.'
upload=$(read_env "$env_file" SOLACE_ROOM_VIDEO_MAX_MIB)
upload=${upload:-128}
[[ $upload =~ ^(0|[1-9][0-9]{0,6})$ ]] || fail 'Invalid SOLACE_ROOM_VIDEO_MAX_MIB.'
(( upload >= 9 )) || upload=9

say 'Install Python and pinned app dependencies'
venv="$app_dir/.venv"
# The previous manual guide can use a separate venv.
if [[ $existing == loaded ]]; then
    old_exec=$(systemctl show solace.service -p ExecStart --value)
    if [[ $old_exec =~ path=([^[:space:]]+/bin/python[0-9.]*) ]]; then
        candidate=${BASH_REMATCH[1]}
        [[ -x $candidate ]] || fail "Existing service Python executable is missing: $candidate"
        venv=$(dirname "$(dirname "$candidate")")
    else
        fail "Existing service uses a custom launcher; retain it and update manually."
    fi
fi
if [[ ! -x $venv/bin/python ]]; then
    if command -v python3.13 >/dev/null && python3.13 -c "import ensurepip" >/dev/null 2>&1; then
        as_user python3.13 -m venv "$venv"
    elif python3 -c 'import sys; sys.exit(sys.version_info < (3, 13))'; then
        as_user python3 -m venv "$venv"
    else
        # On older Ubuntu/Debian, install a managed CPython in the app owner's home.
        bootstrap="$app_dir/.setup-bootstrap"
        as_user python3 -m venv "$bootstrap"
        as_user "$bootstrap/bin/python" -m pip install uv
        as_user env UV_PYTHON_INSTALL_DIR="$app_dir/.python" "$bootstrap/bin/uv" python install 3.13
        python_path=$(as_user env UV_PYTHON_INSTALL_DIR="$app_dir/.python" "$bootstrap/bin/uv" python find --managed-python 3.13)
        as_user "$python_path" -m venv "$venv"
    fi
fi
as_user "$venv/bin/python" -c 'import sys; sys.exit(sys.version_info < (3,13))' ||
    fail 'Existing virtual environment requires Python 3.13 or newer.'
as_user "$venv/bin/python" -m pip install -r "$app_dir/requirements.lock"
as_user "$venv/bin/python" -m pip check

say 'Back up existing configuration and database'
install -d -m 0700 /var/backups/solace
backup=$(mktemp -d /var/backups/solace/setup.XXXXXXXX)
chmod 0700 "$backup"
cp -a "$env_file" "$backup/solace.env"
[[ ! -f $unit_file ]] || cp -a "$unit_file" "$backup/solace.service"
[[ ! -f $nginx_file ]] || cp -a "$nginx_file" "$backup/solace.conf"
if [[ -f "$instance/afterglow.db" ]]; then
    python3 "$app_dir/tools/storage.py" backup --instance "$instance" --output "$backup/database"
    python3 "$app_dir/tools/storage.py" verify --backup "$backup/database"
fi
printf 'Private configuration/database backup: %s\n' "$backup"

# Existing keys, SMTP and provider configuration remain intact.
python3 - "$env_file" "$host" "$email" "$instance" "$port" "$app_dir/.env" <<'PY'
import os, pathlib, sys, tempfile
path = pathlib.Path(sys.argv[1])
host, email, instance, port, uploaded_env = sys.argv[2:]
updates = {
    "SOLACE_ENV": "production", "SOLACE_PUBLIC_URL": "https://" + host,
    "SOLACE_CONTACT_EMAIL": email, "SOLACE_INSTANCE_PATH": instance,
    "HOST": "127.0.0.1", "PORT": port, "TRUST_PROXY": "1",
    "SOLACE_TRUSTED_PROXY": "127.0.0.1", "SOLACE_TRUST_X_REAL_IP": "0",
    "COOKIE_SECURE": "1",
}
lines = path.read_text().splitlines()
keys = {line.strip().partition("=")[0].strip() for line in lines if "=" in line and not line.lstrip().startswith("#")}
if "YOUTUBE_API_KEY" not in keys and pathlib.Path(uploaded_env).is_file():
    # Import only a valid, explicitly uploaded key when the service has no key setting.
    import re
    for line in pathlib.Path(uploaded_env).read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() == "YOUTUBE_API_KEY":
            candidate = value.strip().strip("\'\"")
            if re.fullmatch(r"[A-Za-z0-9_-]{20,100}", candidate):
                updates["YOUTUBE_API_KEY"] = candidate
if "SOLACE_REQUIRE_EMAIL" not in keys:
    has_mail = any(line.strip().partition("=")[0].strip() == "SOLACE_SMTP_HOST"
                   and line.partition("=")[2].strip().strip("'\"") for line in lines)
    if not has_mail:
        updates["SOLACE_REQUIRE_EMAIL"] = "0"
        print("Email provider not configured; verification and recovery require SMTP setup in Integrations.")
kept = [line for line in lines if line.strip().partition("=")[0].strip() not in updates]
with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as output:
    for line in kept:
        output.write(line + "\n")
    for key, value in updates.items():
        if "\n" in value or "\r" in value:
            raise SystemExit("Invalid environment value")
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
        output.write(f'{key}="{escaped}"\n')
    temporary = output.name
os.replace(temporary, path)
PY

say 'Configure systemd and verify Solace'
if [[ $existing != loaded ]]; then
    cat > "$unit_file" <<EOF
[Unit]
Description=Solace watch-party server
Wants=network-online.target
After=network-online.target
[Service]
Type=simple
User=$service_user
Group=$service_group
WorkingDirectory=$app_dir
EnvironmentFile=$env_file
ExecStart=$venv/bin/python -B $app_dir/serve.py
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
[Install]
WantedBy=multi-user.target
EOF
fi
systemctl daemon-reload
systemctl enable solace.service
systemctl restart solace.service
ready=false
for attempt in {1..30}; do
    if curl --noproxy '*' -fsS --max-time 3 -H "Host: $host" "http://127.0.0.1:$port/api/ready" >/dev/null 2>&1; then
        ready=true
        break
    fi
    sleep 1
done
if [[ $ready != true ]]; then
    journalctl -u solace.service -n 20 --no-pager
    fail "Solace did not become ready; configuration backup is $backup."
fi

say 'Configure Nginx and certificate'
mkdir -p /var/www/solace-acme/.well-known/acme-challenge
chmod 0755 /var/www/solace-acme /var/www/solace-acme/.well-known /var/www/solace-acme/.well-known/acme-challenge
apply_nginx() {
    local mode=$1 previous
    previous=$(mktemp "$backup/nginx.XXXXXXXX")
    if [[ -f $nginx_file ]]; then cp "$nginx_file" "$previous"; fi
    render_nginx "$host" "$port" "$upload" "$mode" > "$nginx_file"
    chmod 0644 "$nginx_file"
    if ! nginx -t || ! systemctl reload nginx; then
        if [[ -s $previous ]]; then cp "$previous" "$nginx_file"; else rm -f "$nginx_file"; fi
        nginx -t && systemctl reload nginx || true
        fail 'Nginx could not load the new configuration. The old configuration was restored.'
    fi
}
systemctl enable --now nginx.service
cert="/etc/letsencrypt/live/$host/fullchain.pem"
key="/etc/letsencrypt/live/$host/privkey.pem"
if [[ -r $cert && -r $key ]]; then
    apply_nginx https
else
    apply_nginx http
fi
probe="solace-setup-$(openssl rand -hex 16)"
probe_path="/var/www/solace-acme/.well-known/acme-challenge/$probe"
printf '%s' "$probe" > "$probe_path"
chmod 0644 "$probe_path"
probe_response=$(curl --noproxy '*' -fsS --max-time 5 -H "Host: $host" "http://127.0.0.1/.well-known/acme-challenge/$probe" || true)
rm -f "$probe_path"
[[ $probe_response == "$probe" ]] || fail 'Nginx is not serving the ACME route for this host; check duplicate server_name configuration.'
if [[ $ip_type == ip ]]; then
    if [[ ! -x /snap/bin/certbot ]]; then
        apt-get install -y snapd
        systemctl enable --now snapd.socket
        timeout 120 snap wait system seed.loaded
        snap install --classic certbot
    fi
    certbot=/snap/bin/certbot
    certbot_version=$("$certbot" --version)
    python3 - "$certbot_version" <<'PY'
import re, sys
match = re.search(r"(\d+)\.(\d+)", sys.argv[1])
if not match or tuple(map(int, match.groups())) < (5, 4):
    raise SystemExit("IP certificates require Certbot 5.4 or newer. Run sudo snap refresh certbot and retry.")
PY
    cert_args=(--preferred-profile shortlived --ip-address "$host")
else
    if [[ -x /snap/bin/certbot ]]; then certbot=/snap/bin/certbot
    else apt-get install -y certbot; certbot=/usr/bin/certbot
    fi
    cert_args=(-d "$host")
fi
# Use an existing valid certificate without requesting a duplicate.
if [[ ! -r $cert || ! -r $key ]] || ! openssl x509 -checkend 86400 -noout -in "$cert" >/dev/null; then
    "$certbot" certonly --non-interactive --agree-tos --email "$email" --webroot \
        -w /var/www/solace-acme --cert-name "$host" --keep-until-expiring \
        --deploy-hook 'systemctl reload nginx' "${cert_args[@]}"
fi
[[ -r $cert && -r $key ]] || fail 'No certificate files found. Check DNS/firewall and retry.'
install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
printf '#!/bin/sh\nset -e\nnginx -t\nsystemctl reload nginx\n' > /etc/letsencrypt/renewal-hooks/deploy/solace-nginx
chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/solace-nginx
if [[ $certbot == /usr/bin/certbot ]]; then systemctl enable --now certbot.timer; fi
apply_nginx https

say 'Final HTTPS verification'
curl --noproxy '*' -fsS --max-time 10 --connect-to "$host:443:127.0.0.1:443" "https://$host/api/ready"
printf '\nSolace is ready at https://%s/\nPrivate instance: %s\nBackup: %s\n' "$host" "$instance" "$backup"
printf 'Optional providers: Admin > Integrations. Verify renewal: sudo %s renew --dry-run\n' "$certbot"
