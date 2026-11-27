# Solace setup and operations guide

**Release covered: 2026.11.24.2 Â· SQLite schema 9**

Solace is a watch-party app for synchronized playback, room chat, private messages, and shared games. The web app runs on Flask and SQLite. The separate Android and iPhone app uses Expo and React Native.

### Media options

Solace can run locally or on any compatible HTTPS host. Set `EXPO_PUBLIC_API_URL` at native build time or enter your host on the phone's Room server screen. Existing installed clients retain their saved server address.

- **Video URL** accepts public HTTPS media, including extensionless and signed links, redirects, and sharing pages that expose a direct media source. Playback runs on each participant's device. If an outbound hosting proxy prevents inspection, **Try direct playback** tells the room to try that URL in the player without downloading it through Solace. The device still needs access and a compatible format. DRM streams, login-only pages, and script-only download pages require a provider-supported route or a direct media link. In the current source, `SOLACE_MEDIA_PROXY_HOSTS=*` allows any public HTTPS media origin through a validated IP tunnel. Unset uses both IP tunneling for arbitrary public hosts and hostname-based inspection for built-in trusted providers. A proxy can still reject a tunnel under its own policy.
- **Local file** uploads a browser-playable MP4 or WebM from the device, then adds it to the room queue or starts it where playback controls permit. In the native app, open **Video URL or local file** from the video picker. Room members stream from Solace using byte ranges for seeking. Upload bandwidth and storage depend on where Solace is hosted.
- Local video files are private to room members, including people who join public rooms. They are stored outside the public assets under `instance/room-videos/`. Access ends when the room closes. Request-driven cleanup or the maintenance command removes leftover files and unqueued uploads older than 24 hours. These temporary video bytes are separate from the SQLite backup. Set `SOLACE_ROOM_VIDEO_MAX_MIB` and `SOLACE_ROOM_VIDEO_STORAGE_MIB` for your host, and match any reverse proxy request-size and upload timeout limits.

The existing PythonAnywhere site is on 2026.11.23.6. The 2026.11.23.7 release adds these media changes.

## Contents

- [Run the web app locally](#run-the-web-app-locally)
- [Install or update on PythonAnywhere](#install-or-update-on-pythonanywhere)
- [Configure production and other hosts](#configure-production-and-other-hosts)
- [Host Solace on your own server](#host-solace-on-your-own-server)
- [Move from PythonAnywhere](#move-from-pythonanywhere)
- [Set up administration and integrations](#set-up-administration-and-integrations)
- [Build Android and iPhone apps](#build-android-and-iphone-apps)
- [Understand playback, rooms, and privacy](#understand-playback-rooms-and-privacy)
- [Back up, maintain, and recover Solace](#back-up-maintain-and-recover-solace)
- [Verify the installation and build a release](#verify-the-installation-and-build-a-release)

## Run the web app locally

Install **Python 3.13 or newer**. Open PowerShell in the project root and run:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe run.py
```

On macOS or Linux:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python run.py
```

Open <http://127.0.0.1:8000>. Create a private room and join its invitation from a private window or a different browser profile to test two participants. Tabs sharing the same browser session share an identity. Stop the server with Ctrl+C.

The local server creates or uses the project's `instance/` directory. If your checkout contains saved data, set `SOLACE_INSTANCE_PATH` to a new absolute directory before starting to isolate your test. A YouTube Data API key enables keyword search. Supported YouTube links can be added without a search key.

## Install or update on PythonAnywhere

Use **`Solace-pythonanywhere.zip`** from the verified release. It contains the Flask application, static assets, templates, locked dependencies and server tools. Use `Solace-release-source.zip` for development or native builds. The repository root is the source of truth. `release/` and the ZIPs are generated artifacts.

### Existing installation

1. In PythonAnywhere **Web**, confirm the source directory, WSGI file, and configured virtual environment. The current Solace deployment uses `/home/aditya211/cloudsw`. Find the live instance path from the existing WSGI configuration. When there is no override, it is `/home/aditya211/cloudsw/instance`.
2. Wait until active rooms are empty. Upload the server ZIP to your private home directory. Back up the WSGI configuration and the running code, and make a **verified SQLite backup**. Preserve the entire instance directory, the signing secret, staff credentials, provider settings, and any private files. The live environment and instance stay in place during this update.
3. In a Bash console, activate the **same virtual environment shown on the Web tab**. Adjust the paths below for your installation. Run each block in order and stop at any error.

```bash
cd /home/aditya211
umask 077
app_dir=/home/aditya211/cloudsw
instance_dir="$app_dir/instance"  # Use the real SOLACE_INSTANCE_PATH if configured.
stage_dir=$(mktemp -d "$PWD/solace-stage-XXXXXXXX")
backup_dir="$PWD/solace-backups/solace-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup_dir"
python -m zipfile -e Solace-pythonanywhere.zip "$stage_dir"
cat "$stage_dir/release.json"
python "$stage_dir/tools/storage.py" backup --instance "$instance_dir" --output "$backup_dir/database"
python "$stage_dir/tools/storage.py" verify --backup "$backup_dir/database"
tar -czf "$backup_dir/app-and-wsgi.tar.gz" --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' -C / home/aditya211/cloudsw var/www/aditya211_pythonanywhere_com_wsgi.py
```

If the instance directory is outside the application directory, also back up its private non-database files separately. Keep the backups private. SQLite verification covers committed data in the WAL. The file archive preserves code, WSGI configuration, and private files.

4. Check that the staging manifest shows the intended version. Install the pinned dependencies in the web environment, confirm Pillow, then copy the complete release:

```bash
python --version
python -m pip install -r "$stage_dir/requirements.lock"
python -c "from PIL import __version__; print(__version__); assert __version__ == '13.0.0'"
cp -a "$stage_dir"/. "$app_dir"/
```

5. Preserve the existing WSGI environment and import. If PythonAnywhere has a separate `/static/` mapping, it should point to the live app's `static` directory. Click **Web â†’ Reload**. Missing tables and columns are added by the current initialization code. Use the [verification steps](#verify-the-installation-and-build-a-release) before calling the update complete.

If upgrading from a version that included the retired personal video feature, the overlay can leave behind old public assets. After verifying the backup, remove only these obsolete application files from the deployed app directory:

```bash
cd "$app_dir"
rm -f -- static/love.html static/love.js static/love.css static/love-film.js static/media/for-jaanu.mp4 templates/love-public.html
```

Remove the obsolete `SOLACE_LOVE_VIDEO` setting if present. After reload, `/love`, `/love/film` and `/static/love.html` should return 404. Preserve all user media and instance data.

### Fresh installation

Create a manually configured Python 3.13+ web app. In the PythonAnywhere Bash console, create the matching environment and unpack the server ZIP into a new application directory. This example assumes the chosen web app uses Python 3.13:

```bash
cd /home/aditya211
umask 077
python3.13 -m venv "$HOME/.virtualenvs/solace-web"
source "$HOME/.virtualenvs/solace-web/bin/activate"
mkdir "$HOME/cloudsw"
python -m zipfile -e Solace-pythonanywhere.zip "$HOME/cloudsw"
python -m pip install -r "$HOME/cloudsw/requirements.lock"
```

Configure the production values below before the WSGI import. Use this import pattern with your actual source path:

```python
import sys
sys.path.insert(0, '/home/aditya211/cloudsw')
from app import app as application
```

Set the WSGI source directory and virtual environment on the Web tab, ensure HTTPS is enabled, and reload. PythonAnywhere imports `app:app` as WSGI. A console process started with `run.py` or `serve.py` is independent of the web app.

## Configure production and other hosts

[.env.example](.env.example) lists the available environment keys. Export values in the host or WSGI environment **before importing the app**. Solace does not automatically load the example file.

| Setting | Purpose |
| --- | --- |
| `SOLACE_ENV=production` | Enables production checks and secure cookies. Disable debug mode. |
| `SOLACE_PUBLIC_URL` and `SOLACE_CONTACT_EMAIL` | Set the approved public HTTPS origin and monitored operator contact. Site setup can publish replacements. An origin has no path or query. |
| `SECRET_KEY` | Use a stable, private signing secret of at least 32 characters. Preserve the current secret or existing instance secret file during upgrades. |
| `SOLACE_INSTANCE_PATH` | Optional absolute path to persistent, writable, private storage. Keep the existing path on upgrades. |
| `COOKIE_SECURE=1` | Keep this enabled for the supplied HTTPS Waitress entrypoint. |
| `TRUST_PROXY`, `SOLACE_TRUSTED_PROXY` | Set `TRUST_PROXY=1` behind a reverse proxy that overwrites forwarded headers. The immediate proxy defaults to `127.0.0.1`. Set `SOLACE_TRUSTED_PROXY` to its literal IP address when it connects from a different address. |
| `SOLACE_TRUST_X_REAL_IP` | Use only if the hosting proxy guarantees the `X-Real-IP` header is set by trusted infrastructure. |
| `SOLACE_REQUIRE_EMAIL` | Defaults to `1` in production readiness. `0` is for an intentional limited rollout without working verification and password-reset email. |
| `SOLACE_SITE_ORIGINS` | Approved extra HTTPS origins for site changes. Set up DNS, TLS and hosting routes before publishing a domain change. |

For another Python host, run `python serve.py` behind your HTTPS reverse proxy. The supplied entrypoint defaults to port 8080 and accepts `HOST` and `PORT`. The Dockerfile provides the same entrypoint. Mount a durable, private volume at `/app/instance`, writable by UID 10001. Each deployment needs one authoritative SQLite instance on durable local storage. Multiple servers with separate SQLite files will diverge.

## Host Solace on your own server


### Automatic setup (recommended)

On an Ubuntu or Debian machine with systemd, extract the **Solace-release-source.zip** in your SSH user's home directory (or update the existing deployed checkout). Point your desired DNS name to the server, or use its public IPv4. Allow inbound TCP 80 and 443 in your cloud firewall. Run:

```bash
sudo bash setup.sh
```

For a first install, the script asks for the public hostname/IP and contact email if they are not already configured. To supply them directly:

```bash
sudo bash setup.sh --host watch.example.com --email support@example.com
```

It installs Nginx, Python 3.13+ and locked dependencies, creates the systemd service and private environment, configures HTTP validation, obtains a trusted certificate, enables certificate renewal and HTTPS, and verifies the readiness endpoint through Nginx. On older Debian/Ubuntu Python versions, it installs Python 3.13 with uv into the app directory. Public IPv4 certificates need Certbot 5.4+ and snapd; a domain may use the distribution's Certbot. Existing certificates are reused. [Python management](https://docs.astral.sh/uv/guides/install-python/) and [IP certificates](https://letsencrypt.org/2026/03/11/shorter-certs-certbot).

For upgrades, extract the new code over the same app directory without deleting the instance or environment file, then rerun the same command. The installer adopts the standard existing service and persistent instance path, backs up SQLite and private deployment configuration under /var/backups/solace, and keeps existing credentials, profiles, chat and uploads in place. Keep separate off-host copies of media, secrets and the previous code before upgrades. If a manually customized service uses another environment file, launcher or directory, the installer stops with a specific message; use the manual instructions below. It does not change your cloud firewall, DNS, or create paid resources.

On completion, open the printed HTTPS URL. Optional email, YouTube search, push, and Google configuration is available in the admin Integrations section. A fresh setup without SMTP permits a limited rollout until mail is configured. A missing API key in the running service can be supplied via /etc/solace.env followed by sudo systemctl restart solace. Named staff enrollment remains the separate setup_staff.py command described below.

Useful checks:

```bash
sudo systemctl status solace nginx --no-pager
sudo journalctl -u solace -n 30 --no-pager
sudo nginx -t
```

For rollback, use the private backup path printed during setup to recover the previous environment, Nginx config, and systemd unit; restore the previous code and use the documented database recovery procedure if needed. Backups preserve the database before migrations and are not deleted by subsequent setups.

### Manual setup (advanced)

These examples assume a Linux server with Python 3.13+, systemd, Nginx, and a domain you control. Replace `watch.example.com` with your HTTPS hostname. Point its DNS to the server and provision a TLS certificate before exposing the app. Publish ports 80 and 443 through your firewall. Keep the Python listener on loopback. If you are moving existing data, keep the new hostname private during rehearsal and follow the migration section before opening it to users.

On EC2, run `cat /etc/os-release` over SSH to identify the distribution. If Nginx is missing, install it with `sudo apt update && sudo apt install nginx` on Ubuntu or Debian, or `sudo dnf install nginx` on Amazon Linux 2023. Verify that `python3.13 --version` succeeds before setting up Solace. Uploading the ZIP does not create or start a service.

Upload the **`Solace-release-source.zip`** to your own account over SSH. It includes `serve.py`, the app, and the maintenance tools. On the server, from the directory with that ZIP:

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin solace
sudo install -d -m 0755 /opt/solace/app
sudo install -d -m 0700 -o solace -g solace /var/lib/solace
sudo python3.13 -m zipfile -e Solace-release-source.zip /opt/solace/app
sudo python3.13 -m venv /opt/solace/venv
sudo /opt/solace/venv/bin/python -m pip install -r /opt/solace/app/requirements.lock
sudo install -m 0600 /dev/null /etc/solace.env
sudoedit /etc/solace.env
```

Run the provisioning commands once for a fresh target. Put the following values in the private environment file and replace the sample domain and contact. Use the original signing secret for a migration. On a fresh install, the app can generate the secret under the persistent instance path.

```dotenv
SOLACE_ENV=production
SOLACE_INSTANCE_PATH=/var/lib/solace/instance
SOLACE_PUBLIC_URL=https://watch.example.com
SOLACE_CONTACT_EMAIL=support@example.com
COOKIE_SECURE=1
TRUST_PROXY=1
HOST=127.0.0.1
PORT=8080
SOLACE_ROOM_VIDEO_MAX_MIB=128
SOLACE_ROOM_VIDEO_STORAGE_MIB=1024
```

Add SMTP configuration for email verification and recovery. For a deliberate limited launch without mail, use `SOLACE_REQUIRE_EMAIL=0`. Keep optional credentials in the private environment or the admin integrations panel. For a fresh install, create the instance directory with `sudo install -d -m 0700 -o solace -g solace /var/lib/solace/instance`. For a move, the import step creates it.

Save this as `/etc/systemd/system/solace.service`.

```ini
[Unit]
Description=Solace watch-party server
After=network-online.target

[Service]
User=solace
Group=solace
WorkingDirectory=/opt/solace/app
EnvironmentFile=/etc/solace.env
ExecStart=/opt/solace/venv/bin/python -B /opt/solace/app/serve.py
Restart=on-failure
UMask=0077

[Install]
WantedBy=multi-user.target
```

Inside your HTTPS Nginx server block, use this location. Nginx forwards the original host and scheme and overwrites client-supplied forwarded headers. Adjust the upload ceiling if you change Solaceâ€™s configured maximum.

```nginx
client_max_body_size 128m;
client_body_timeout 120s;

location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_send_timeout 120s;
    proxy_read_timeout 120s;
    proxy_buffering off;
}
```

For a fresh installation, start the service with `sudo systemctl daemon-reload` and `sudo systemctl enable --now solace`. For a transfer, do that after installing the data. Check Nginx with `sudo nginx -t`, start it with `sudo systemctl enable --now nginx`, and reload it if it was already running. Check the public HTTPS `/api/health` and `/api/ready`. The service log is available through `sudo journalctl -u solace`. [Nginx reverse proxy configuration](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)

If both service units are missing, create `solace.service` using the unit above and install Nginx for your distribution. Once the units exist, use these commands on the server. Replace the hostname with the value in `SOLACE_PUBLIC_URL`.

```bash
sudo systemctl status solace nginx --no-pager -l
sudo journalctl -u solace -n 40 --no-pager
curl -i -H 'Host: watch.example.com' http://127.0.0.1:8080/api/ready
```

A connection refusal from the last command means Waitress is absent from that port. A 503 response shows which readiness check failed. If readiness passes locally but the public site times out, verify that the EC2 instance has a public address, that the security group is attached to it, and that the subnet route points to an internet gateway. Use the configured HTTPS hostname for the public site. A raw-IP request can be rejected when Solace is configured for a different hostname. [AWS public subnet requirements](https://docs.aws.amazon.com/vpc/latest/userguide/working-with-igw.html)

### EC2 public IP and the Nginx welcome page

The welcome page means Nginx handled the request using its default site. Nginx still needs a site matching the address you opened, and that site needs a running Solace service as its upstream. Keep the Git checkout, database and signing secret outside any Nginx document root. Deploy the verified source ZIP if the GitHub checkout is older or contains merge markers. [Nginx request routing](https://nginx.org/en/docs/http/request_processing.html)

If `journalctl -u solace` shows no entries, use `sudo systemctl cat solace` to see whether a unit is installed at all. When installing the unit for a checkout at `/home/ubuntu/test11` with an existing `.venv`, adapt these four lines in the service example above. Keep the existing instance path in `/etc/solace.env` if this server already contains data you intend to use.

```ini
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/test11
ExecStart=/home/ubuntu/test11/.venv/bin/python -B /home/ubuntu/test11/serve.py
```

Run `sudo systemctl daemon-reload` and `sudo systemctl enable --now solace` after saving a new unit. Check `sudo systemctl status solace --no-pager -l` if start fails.

1. Follow the service setup above. In `/etc/solace.env` set `SOLACE_PUBLIC_URL=https://203.0.113.10`, replacing the example with your EC2 **Public IPv4 address**. Keep `HOST=127.0.0.1`, `PORT=8080` and `TRUST_PROXY=1`. If the service was already running, restart it to load the environment file. Check it before changing Nginx:

   ```bash
   curl -i -H 'Host: 203.0.113.10' http://127.0.0.1:8080/api/ready
   ```

2. For an IP-only deployment, install Certbot 5.4 or newer using the instructions for your Linux distribution. Keep public port 80 available for HTTP validation. Create a dedicated challenge directory with `sudo install -d -m 0755 /var/www/solace-acme`. Add an Nginx site with an exact `server_name` matching your IP. On installations that load `/etc/nginx/conf.d/*.conf`, use `/etc/nginx/conf.d/solace.conf`. Start with this port 80 block only:

   ```nginx
   server {
       listen 80;
       server_name 203.0.113.10;

       location ^~ /.well-known/acme-challenge/ {
           root /var/www/solace-acme;
           try_files $uri =404;
       }

       location / {
           return 503;
       }
   }
   ```

3. Run `sudo nginx -t && sudo systemctl reload nginx`. With the example IP replaced, request the certificate:

   ```bash
   sudo certbot certonly --webroot -w /var/www/solace-acme \
       --preferred-profile shortlived --ip-address 203.0.113.10 \
       --deploy-hook "systemctl reload nginx"
   ```

   Use the certificate and key paths reported by Certbot. IP certificates last six days. Enable automatic renewal for the Certbot installation and check it with `sudo certbot renew --dry-run`. [Letâ€™s Encrypt IP certificate instructions](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)

4. Change the HTTP site's `location /` to `return 308 https://203.0.113.10$request_uri;` and keep the challenge route. Add the 443 block using the certificate paths from step 3:

   ```nginx
   server {
       listen 443 ssl;
       server_name 203.0.113.10;
       ssl_certificate /etc/letsencrypt/live/203.0.113.10/fullchain.pem;
       ssl_certificate_key /etc/letsencrypt/live/203.0.113.10/privkey.pem;
       client_max_body_size 128m;
       client_body_timeout 120s;

       location / {
           proxy_pass http://127.0.0.1:8080;
           proxy_set_header Host $host;
           proxy_set_header X-Forwarded-Host $host;
           proxy_set_header X-Forwarded-Proto $scheme;
           proxy_set_header X-Forwarded-For $remote_addr;
           proxy_send_timeout 120s;
           proxy_read_timeout 120s;
           proxy_buffering off;
       }
   }
   ```

   Run `sudo nginx -t && sudo systemctl reload nginx`, then open `https://203.0.113.10/api/health` and the homepage with your real IP. The exact IP match lets the packaged default Nginx site stay in place. If EC2 assigns you a different public IP after a stop/start, update the origin, Nginx server name and certificate together. [EC2 public IPv4 assignment](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-instance-addressing.html)

## Move from PythonAnywhere

1. **Plan access before changing the address.** If you already own the hostname people use, keep it and switch its DNS to the new server at cutover. If people use the PythonAnywhere hostname, registered users can sign in on the new domain with their existing accounts. Guests should create an account from their existing guest browser on the old site before the final copy. That upgrades their existing identity. Guest records can be copied byte for byte, but browsers send a host-only guest cookie only to the host that issued it. If a guest cannot upgrade and keeping guest access is required, delay cutover until you have an identity-transfer plan. Browser-only preferences and earlier expired data are outside the server snapshot. [Browser cookie scope](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Cookies)
2. **Rehearse privately.** Use the real instance path from the existing WSGI configuration. Make a verified copy, import it into an isolated destination, and verify accounts, room and private chats, Open World messages, attachments, saved preferences, staff setup, and published site settings. Prevent rehearsal users, notifications and workers from writing to production services. Delete or secure the rehearsal instance after verification.
3. **Take the final copy with writes paused.** Schedule a quiet window, place the old site in maintenance, and stop scheduled tasks or dispatchers that can write. Solace deletes a room when its last member leaves and expires abandoned rooms after the reconnect window. Avoid asking everyone to leave as a migration step if you need to retain their room records in the snapshot. Keep the source data and its WSGI configuration. Create the snapshot in a private directory on the old host:

```bash
set -e
umask 077
live_app=/home/aditya211/cloudsw
instance_dir="$live_app/instance"  # Replace with the live WSGI override if set.
snapshot=$(mktemp -d "$HOME/solace-move-XXXXXXXX")
python "$live_app/tools/storage.py" backup --instance "$instance_dir" --output "$snapshot/database"
python "$live_app/tools/storage.py" verify --backup "$snapshot/database"
tar -C "$instance_dir" --exclude='./afterglow.db' --exclude='./afterglow.db-wal' --exclude='./afterglow.db-shm' \
    --exclude='./solace-admin.db' --exclude='./solace-admin.db-wal' --exclude='./solace-admin.db-shm' \
    -czf "$snapshot/instance-files.tar.gz" .
(cd "$snapshot" && sha256sum instance-files.tar.gz > instance-files.sha256)
printf '%s\n' "$snapshot"
```

4. **Transfer and verify privately.** Copy the snapshot over SSH/SFTP, or use the authenticated hosting file manager and an encrypted local staging location. It contains private data and must stay outside the appâ€™s public files. Recreate any host environment values, including the original `SECRET_KEY` if set, staff security configuration, and SMTP, push, and YouTube credentials. Copy referenced private credential files and adjust their paths for the new server. Preserve existing database and media paths through configuration. Start with a **new** instance directory:

```bash
set -e
snapshot=/absolute/path/to/transferred/solace-move-snapshot
python=/opt/solace/venv/bin/python
instance=/var/lib/solace/instance
"$python" /opt/solace/app/tools/storage.py verify --backup "$snapshot/database"
(cd "$snapshot" && sha256sum -c instance-files.sha256)
test ! -e "$instance" && sudo mkdir -m 0700 "$instance" || exit 1
sudo tar -xzf "$snapshot/instance-files.tar.gz" --no-same-owner --no-same-permissions -C "$instance"
for filename in afterglow.db solace-admin.db; do
    if [ -f "$snapshot/database/$filename" ]; then
        sudo install -m 0600 "$snapshot/database/$filename" "$instance/$filename"
        sudo cmp "$snapshot/database/$filename" "$instance/$filename" || exit 1
    fi
done
sudo chown -R solace:solace "$instance"
```

Verified database copies preserve all stored rows. The separate disaster-recovery `storage.py restore` command deliberately resets sessions and invite grants, which is useful for rollback after an incident. During migration use the verified copy above if you intend to preserve those records.

5. **Verify before switching traffic.** Start the new service privately and check versions, readiness, account sign-in, chats and attachments, guest upgrades, settings and staff MFA. If the domain changes, add the new HTTPS origin to `SOLACE_SITE_ORIGINS`, include the old origin there during transition, and publish the new origin through **Site setup** on the destination. This matters because a saved site revision can override `SOLACE_PUBLIC_URL`. Verify link generation, mail, crawler files and push subscriptions on the new URL. Web push device permission is also tied to the browser origin and needs to be enabled again on a new domain.
6. **Switch traffic once.** Point the domain or share the new address only after acceptance. Keep PythonAnywhere offline for writes and retain the verified snapshot. If rollback is needed after the destination has accepted writes, first stop destination writes and preserve its new data. Simply pointing users at an older snapshot would omit post-cutover activity.

## Set up administration and integrations

### Staff access and site setup

Open `/ops/solace` on your Solace domain. Existing named staff should use their staff username, password and authenticator code. For the first named administrator, use the actual production environment from a private server console:

```bash
python setup_staff.py your-staff-username --role administrator
```

Enter a unique staff password when prompted and enroll the displayed TOTP secret in your authenticator. Keep the enrollment secret private. This is a one-time step that disables legacy shared-password access. Use the existing account for ordinary updates. Manage additional staff and permissions under **Staff & security**. **Users & guests** can find a profile by friend code without requiring dashes or matching letter case. **Storage** manages global and individual quotas. **Community safety** manages reports and platform restrictions.

In **Site setup**, preview branding, contact and indexing changes, enter a reason, and publish the revision. Use **Integrations â†’ Check configuration** to check `robots.txt` and `sitemap.xml` against the generated responses. Publish indexing only for the intended HTTPS origin. Private pages remain protected by authorization and excluded from the public sitemap. Submit the public sitemap in Search Console after verifying ownership.

### Provider setup

Use **Integrations â†’ Provider setup** with an account that can manage site settings. An empty credential field keeps the saved value. **Restore host settings** removes the selected provider override. The dashboard saves overrides in the private `instance/provider-settings.json` file and masks credentials. Back up this file separately from SQLite. You can also configure provider values in the hosting environment.

| Provider | Setup and check |
| --- | --- |
| YouTube search | Enable YouTube Data API v3 and set `YOUTUBE_API_KEY` or configure it in Integrations. Restrict the key to the API and check a keyword search. |
| Account email | Set `SOLACE_SMTP_HOST`, `SOLACE_SMTP_PORT`, `SOLACE_SMTP_SSL`, `SOLACE_SMTP_USERNAME`, `SOLACE_SMTP_PASSWORD`, and `SOLACE_MAIL_FROM` as required by the provider. Use a verified sender. The editor accepts ports 465 and 587. For a custom host, set `SOLACE_SMTP_ALLOWED_HOSTS`. Confirm host egress and test verification and password reset with a test account. |
| Web Push | Set `SOLACE_PUSH_PUBLIC_KEY`, `SOLACE_PUSH_PRIVATE_KEY` and `SOLACE_PUSH_CONTACT`. Install optional `pywebpush` in the dispatcher environment. Use a supervisor to run `python -m flask --app app dispatch-push` every few seconds with access to the same instance and configuration. Verify host egress, worker operation and permission on a test device. The free PythonAnywhere deployment needs a hosting arrangement that supports the required supervised worker before enabling timely delivery. |
| Google reports | Enable Search Console and Analytics Data APIs as needed, install optional `google-auth` and `requests` in the web environment, and grant the service account read access. Use private JSON credentials in Integrations or set `GOOGLE_APPLICATION_CREDENTIALS` to a protected file. Set `SOLACE_SEARCH_PROPERTY` to the exact verified property and `SOLACE_GA4_PROPERTY` to the numeric property ID. GA4 reporting reads existing analytics. Install any tracking separately. |
| Backup discovery | Set `SOLACE_BACKUP_DIRECTORY` to the private parent of completed backup directories. Solace looks for `*/backup.json` and `*/database/backup.json` there. A visible timestamp identifies a manifest. Use the storage tool to verify backup integrity. |

Open **Overview** for UTC daily counts, recent recognized profiles, and aggregate room, direct and Open World message events. Recently active means a supported action in the preceding two minutes. Referrer-based room funnel counts are estimates. Aggregate activity tracking excludes message bodies. **Diagnostics** holds separate access-controlled operational data. Provider badges show configuration. Confirm actual email, notifications, and Google reports before relying on them.

## Build Android and iPhone apps

The website already works in mobile browsers. Installed native updates require separate builds. For a development preview on a trusted Wi-Fi network, install Node.js and use `start-mobile.bat` from the project root with Expo Go on both phones. The development room server uses port 8001. A phone must use the computer's Wi-Fi address, since its own localhost points back to the phone.

For installed builds, configure the public HTTPS backend in the phone's Server settings or set `EXPO_PUBLIC_API_URL` in the build environment. Run from `mobile/`:

```text
npm ci
npm run check
npm test
npx eas-cli login
npx eas-cli build --platform android --profile preview
npx eas-cli build --platform ios --profile preview
```

Android preview builds produce an APK. Use `--profile production` for an Android store bundle or iOS distribution, with the required platform signing and developer accounts. iPhone internal builds require registered devices. The updated iOS icon is included in the native source and takes effect after rebuilding. Native video runs in a YouTube WebView and remains subject to YouTube restrictions.

Web Push is separate. On supported Android browsers, enable device notifications in Solace Settings and grant permission. On iPhone or iPad, add Solace to the Home Screen, open it there, and enable notifications from Settings. The profile preference defaults to on for new profiles. Provider acceptance, display receipts and opens are separate signals. The installed React Native app has no native OS push integration.

## Understand playback, rooms, and privacy

- **Playback.** Browser clients use the YouTube embed or supported direct media. The server synchronizes room state and the queue while native or browser players decode media locally. Autoplay, codec support, ads, and source availability can affect synchronization. On PythonAnywhere, the direct-link inspector uses the hosting proxy if configured. That host's allowlist still applies. Signed URLs may expire and should stay out of logs. Netflix opens separately from shared playback.
- **Room and settings.** Rooms start private. Room settings own room visibility, capacity, invitations and moderation. User Settings own themes, haptics, ambience, privacy and alerts. Active-room navigation keeps the player mounted. Leaving the last seat closes the room. A disconnected room has a limited reconnect window. Revoking a private link does not change a separately shared room code.
- **Ambience and icons.** Ambient mode and haptics default on while existing choices remain saved. Ambience stays inside the video box. It uses the current YouTube thumbnail, readable direct-video frames, or a theme edge. A browser can restrict access to cross-origin video frames.
- **Identity and history.** Guests retain their profile and history in the same browser and can upgrade to a new account in that browser. Clearing browser data can remove guest access. Watch history credits verified visible playback samples, excludes long gaps, and groups samples by playback session. The account activity graph uses the user's selected timezone.
- **Messaging and media.** Accepted friends can send private messages; inbox hiding and message removal are separate actions. Blocks limit reading and sending. Room, Open World and direct message attachments follow their own access rules. Chat uploads support JPEG, PNG, still WebP and GIF up to 8 MiB and 16 megapixels, with separate GIF animation limits. Profile pictures use a smaller still-image limit. Storage quotas cover eligible saved data. Private messaging is server-authorized and stored in SQLite.
- **Open World.** Community participation and public discovery are explicit choices. Open World history is kept for up to 30 days or the newest 10,000 messages, subject to cleanup. Its online number counts eligible visible participants once across their active tabs. Private direct messaging still requires an accepted connection.
- **Safety and retention.** Staff roles govern reports, evidence, restrictions, appeals and emergency switches. Evidence access is case-specific and audited. The documented retention is 30 days for evidence and 180 days for case details. Keep backups and moderation evidence private, and rotate off-host backups according to your policy.
- **Games.** Love Hoops, Little Ludo, and Little Hearts & Stars run inside rooms. Accepted friends can also use the standalone game invitation flow. Game options and chat gestures are separate from playback controls.

## Back up, maintain, and recover Solace

Run the backup and verify commands from the PythonAnywhere update steps, substituting the active instance path when running on another host. Save the matching code, private configuration, credentials, and non-database instance files separately. Schedule maintenance hourly when your hosting plan allows it:

```bash
python tools/maintenance.py --instance /absolute/path/to/instance
```

Monitor readiness, HTTP 5xx and 429 trends, latency, disk space, SQLite errors, playback failures, and provider delivery. Keep media tokens, emails, chat content, and query strings out of routine logs. Test restoring to a separate directory before relying on a backup.

For rollback, pause traffic and preserve a fresh verified copy of the current database. Use the matching app version and config. If a database restore is needed, restore into a **new** private instance directory:

```bash
python tools/storage.py verify --backup /absolute/path/to/backup/database
python tools/storage.py restore --backup /absolute/path/to/backup/database --target /absolute/path/to/new-instance
```

Restore the matching private files and signing secret separately, set `SOLACE_INSTANCE_PATH` to the verified new directory, then reload and check readiness. The restore tool revokes saved sessions and invite links. An older snapshot omits updates committed after it. Keep the original instance for recovery until the rollback is verified.

## Verify the installation and build a release

On the deployed app, check `/api/health` for the release version and `/api/ready` for readiness. A 503 means a readiness requirement failed. Check configuration, database, and disk headroom before opening the deployment to users. These commands check the status without creating test data:

```text
curl --fail https://aditya211.pythonanywhere.com/api/health
curl --fail https://aditya211.pythonanywhere.com/api/ready
```

When authorized to run a live smoke test, create a private disposable test room in the browser. Check that an existing profile and chats remain accessible to their owner, send a synthetic test image or GIF, and try Watch â†’ Chat â†’ Queue â†’ Room Settings with two browser profiles. Leave the test room from both profiles. Verify the signed-in admin Integrations and Overview screens, then check end-to-end delivery for each configured provider.

From the source root, use a disposable data directory for tests:

```text
python -B -m unittest discover -s tests -p "test_*.py"
python -B tests/run_local_smoke.py
```

For browser checks, run `python -m pip install -r requirements-audit.txt` and `python -m playwright install chromium webkit` in the local environment. Start `python -B tests/run_local_smoke.py --serve --port 8017` in one terminal and run `python -B tests/check_player_visibility.py` in another. Use the audit tools and synthetic media only in local or isolated test environments. Native type checks and tests run in `mobile/`. CI also runs a Docker build, JavaScript syntax checks and native exports.

To package a future release, run `python tools/build_release.py --version YOUR_RELEASE_VERSION` from the source root with a new release version. It updates asset versions, builds the generated release directory and both ZIPs, and verifies archive contents. Keep existing deployment artifacts when you need an exact rollback copy.

### Recent verification and first checks when something fails

- Release 2026.11.23.6 was deployed and reloaded. Backup comparisons reported no missing or changed existing accounts, chats, preferences, room settings, profiles or media. Pixel 7 and iPhone 13 browser emulation passed Room Settings, icon and ambience checks. The activity database recorded the synthetic message. Seven local admin tests passed. Signed-in production admin screens, real provider delivery and physical phone checks remained open at that verification.
- The historical local streaming runs used synthetic media and up to four concurrent clients. A 121-second decoded-media run measured 0.209 s p95 playback drift across 55 samples. It measured local behavior only. Live capacity and long physical-device sessions need their own evidence.
- **Missing table or startup error:** use the current complete release, confirm that WSGI imports the correct directory, and check the newest error log for the real instance path. Preserve SQLite and run the normal migration path.
- **Image uploads blocked:** verify Pillow with the Python interpreter in the web app's virtual environment, install the lockfile there, and reload.
- **Admin sign-in fails:** use the current named staff username and its matching authenticator entry, with device time set automatically. Keep credentials private and check the newest server error if the issue persists.
- **Video fails to load:** try another embeddable YouTube video or a known playable HTTPS media source. Check the room's playback status, source link expiry and host egress.
- **Empty sitemap or stale assets:** check the published HTTPS origin and indexing revision, then verify the release shown in health and confirm that any host-level static mapping uses the live code path.
