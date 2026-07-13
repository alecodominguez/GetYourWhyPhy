# Deployment: Remote Access & Domain Migration

## Why move off ngrok

The current `https://opacity-cadillac-emporium.ngrok-free.dev` URL is a free
ngrok tunnel pointed at whatever machine happens to be running `server.py`
at the moment - almost certainly a laptop. That means:
- The site goes down whenever that laptop sleeps, closes, or loses Wi-Fi.
- The URL can rotate on the free tier, breaking every already-downloaded
  copy of `WhyPhy.py` that has it hardcoded.
- There's no real remote control - "running the server" means someone's
  physical laptop is open and connected right now.

Moving to a small always-on VPS + your own domain solves the uptime problem
and is also a prerequisite for real remote access: you can't remotely
start/stop/modify a process running on a laptop that's asleep in someone's
backpack.

## 1. Get a VPS

Any $5-6/mo box works fine for this traffic level (DigitalOcean Droplet,
Linode, AWS Lightsail, Hetzner). Ubuntu 22.04/24.04 LTS is assumed below.

## 2. Point the domain at it

In your domain registrar's DNS settings for `whyphy.app`, add:
```
A     @      <VPS_IP>
A     www    <VPS_IP>
```
DNS propagation can take up to ~24h but is usually faster.

## 3. Install and run the app

```bash
sudo adduser --disabled-password whyphy
sudo mkdir -p /opt/GetYourWhyPhy
sudo chown whyphy:whyphy /opt/GetYourWhyPhy
sudo -u whyphy git clone https://github.com/alecodominguez/GetYourWhyPhy /opt/GetYourWhyPhy
cd /opt/GetYourWhyPhy
sudo -u whyphy python3 -m venv .venv
sudo -u whyphy .venv/bin/pip install -r requirements.txt uvicorn
```

Edit `deploy/whyphy.service`, set a real `WHYPHY_ADMIN_TOKEN`, then:
```bash
sudo cp deploy/whyphy.service /etc/systemd/system/whyphy.service
sudo systemctl daemon-reload
sudo systemctl enable --now whyphy
```

## 4. TLS + reverse proxy

```bash
sudo apt install -y caddy   # see caddyserver.com for the repo setup
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```
Caddy automatically requests and renews a Let's Encrypt certificate for
`whyphy.app` the first time it starts - no manual cert management.

## 5. Remote access, three layers (use whichever fits the moment)

**Layer 1 - SSH (primary, always available).** This is genuinely the right
tool for "run, stop, or modify the server from another state" - it's what
SSH is for, works from any terminal (including Termius on a phone), and
doesn't require exposing anything extra to the internet:
```bash
ssh whyphy@whyphy.app     # "whyphy" here is the Linux user from step 3
sudo systemctl restart whyphy
sudo systemctl stop whyphy
journalctl -u whyphy -f          # live logs
```
Recommended hardening: disable SSH password auth (key-only), and put the
box behind [Tailscale](https://tailscale.com) so SSH isn't reachable from
the open internet at all - only from your own enrolled devices.

**Layer 2 - Git push to deploy.** `deploy/deploy.yml` is a GitHub Actions
workflow: push to `main` and it SSHs in, pulls, reinstalls dependencies,
and restarts the service for you. This is "modify the server remotely"
in the most literal sense - edit code locally, `git push`, done.

**Layer 3 - `/admin/status` and `/admin/restart` HTTP endpoints.** Added in
`server.py`, gated behind a `WHYPHY_ADMIN_TOKEN` header, for quick
"is it up? / kick it" checks from your phone without opening a terminal.
Deliberately limited to status + restart, nothing else - no arbitrary
command execution, no stop-without-restart (a systemd-managed service that
can only be *restarted* remotely can't accidentally be left down by a
dropped connection). Treat these as a convenience layer, not your primary
control plane - Layer 1 does everything these do and more, more securely.

## 6. Update everything that still points at ngrok

- `WhyPhy.py`, `bssid_resolver.py`, `passive_monitor.py`: already updated
  in this delivery to `https://whyphy.app`.
- `README.md` and the GitHub repo description/website field.
- Any already-downloaded `WhyPhy_Windows.zip` / `WhyPhy_Mac.zip`
  executables in `downloads/` - these have the old URL baked in and need
  to be rebuilt and re-uploaded after the code changes above.
- The `downloads/` links on the frontend itself keep working as-is since
  they're relative paths (`/downloads/...`) served by the same app.
