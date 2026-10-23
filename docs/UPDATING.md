# Updating the live server

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
