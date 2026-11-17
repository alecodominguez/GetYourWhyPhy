# Updating the live server

## Push-to-deploy (normal path)
Pushing to `main` deploys automatically via `.github/workflows/deploy.yml`:
pull -> pip install -> restart -> service check -> public site check.
Watch it under the repo's **Actions** tab; a failed deploy turns red, shows the
last 30 service log lines, and GitHub emails you. Pushes that only touch
`paper/`, `docs/`, `*.md` or `.idea/` don't deploy. To redeploy without a new
commit: Actions -> "Deploy to whyphy.app" -> **Run workflow**.

The manual steps below are the fallback if Actions is down or a deploy fails.

### Push-to-deploy setup (one time)
On the VPS (as `aleco`):
```bash
# 1. whyphy needs a login shell for SSH (service accounts often have nologin)
getent passwd whyphy                       # last field is the shell
sudo usermod -s /bin/bash whyphy           # only if it showed nologin/false

# 2. Deploy-only SSH key, authorized for whyphy
sudo -u whyphy ssh-keygen -t ed25519 -N "" -C github-deploy -f /tmp/deploy_key
WH=$(getent passwd whyphy | cut -d: -f6)
sudo -u whyphy mkdir -p "$WH/.ssh" && sudo -u whyphy chmod 700 "$WH/.ssh"
sudo -u whyphy sh -c "cat /tmp/deploy_key.pub >> $WH/.ssh/authorized_keys && chmod 600 $WH/.ssh/authorized_keys"
sudo cat /tmp/deploy_key                   # copy ALL of this into DEPLOY_SSH_KEY
sudo rm /tmp/deploy_key /tmp/deploy_key.pub

# 3. Let whyphy restart the service and read its logs - nothing else
echo 'whyphy ALL=(root) NOPASSWD: /usr/bin/systemctl restart whyphy, /bin/systemctl restart whyphy, /usr/bin/journalctl -u whyphy -n 30 --no-pager' \
  | sudo tee /etc/sudoers.d/whyphy-deploy
sudo chmod 440 /etc/sudoers.d/whyphy-deploy
sudo visudo -cf /etc/sudoers.d/whyphy-deploy   # must say "parsed OK"
```
On GitHub: repo **Settings -> Secrets and variables -> Actions -> New repository secret**:
`DEPLOY_HOST` = `whyphy.app`, `DEPLOY_USER` = `whyphy`, `DEPLOY_SSH_KEY` = the private key above.

## Manual fallback

After pushing to `main`:

```bash
ssh aleco@whyphy.app
cd /opt/GetYourWhyPhy
sudo -u whyphy git pull origin main
sudo -u whyphy .venv/bin/pip install -r requirements.txt
# apply any pending DB schema changes here (see below)
sudo systemctl restart whyphy
sudo systemctl status whyphy
curl -s https://whyphy.app | head -c 200
```

## Database schema changes
`git pull` never touches the database, and `Base.metadata.create_all()` only
creates missing *tables*, not missing *columns*. Any change that adds a
column needs a manual `ALTER TABLE` before the restart.

The database file is owned by the `whyphy` user, so **run anything that
writes to it as `whyphy`** (`sudo -u whyphy ...`). Running it as `aleco`
fails with `attempt to write a readonly database`.

```bash
sudo -u whyphy python3 -c "
import sqlite3
conn = sqlite3.connect('/opt/GetYourWhyPhy/data/campus_wifi.db')
conn.execute(\"ALTER TABLE wifi_logs ADD COLUMN source VARCHAR DEFAULT 'native'\")
conn.commit()
print('done')
"
```
- `done` means it applied.
- `duplicate column name: source` means it was already applied (not a problem).

Check the current columns (read-only, fine as `aleco`):

```bash
python3 -c "import sqlite3; c=sqlite3.connect('/opt/GetYourWhyPhy/data/campus_wifi.db'); print([r[1] for r in c.execute('PRAGMA table_info(wifi_logs)')])"
```

## Gotchas learned the hard way
- `attempt to write a readonly database` = you ran a write as `aleco`. Re-run
  it with `sudo -u whyphy`. The `apport_python_hook` / `FileNotFoundError`
  text printed after it is Ubuntu's crash reporter choking on `python3 -c`
  one-liners; ignore it, the line above it is the real error.
- If you replace `data/campus_wifi.db` while the service is running, restart
  the service afterward - the running process keeps the old file open.
- `scp` runs on your Mac, not inside an SSH session.
- Keep `WHYPHY_ADMIN_TOKEN` only in `/etc/systemd/system/whyphy.service` on
  the server, never in the committed `deploy/whyphy.service`.
- Set `DEPLOY_USER` to `whyphy` (not `aleco`) if you use `deploy/deploy.yml`.
