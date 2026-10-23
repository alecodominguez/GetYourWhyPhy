# GetYourWhyPhy — Improvements Roadmap & Evaluation

This covers the six requested improvements: what's implemented in this
delivery, what's a stub/design-only, what's genuinely infeasible as
originally scoped (and why), and what's still needed to ship each one for
real. Code referenced below is in the accompanying repository copy.

---

## 1. Automated Mapping (BSSID)

**Correction to the original framing:** speedtest-cli can't provide a
BSSID — it only talks to speedtest.net servers, it has no visibility into
your Wi-Fi radio. BSSID has to come from the same OS-level interface query
`WhyPhy.py` already uses for SSID (`netsh` / `system_profiler` / `nmcli`),
extended to also grab the BSSID field they all expose.

**Implemented:**
- `get_signal_info()` in `WhyPhy.py` now returns `(ssid, signal, bssid)`.
- `bssid_resolver.py`: local cache + server round-trip to resolve a BSSID
  to a building; falls back to the existing manual prompt when unresolved.
- `server.py`: `BSSIDMapping` table, `/resolve-bssid/{bssid}` and
  `/vote-bssid` endpoints, with a confirmation-threshold voting scheme
  (3 matching votes before a BSSID is trusted) so one mistyped or bad-faith
  submission can't mislabel an access point for everyone.
- `WhyPhy.py`'s `main()` tries auto-resolution first, still lets the user
  override/confirm, and always submits a vote after a confirmed run.

**Still needed before this is production-solid:**
- A seed dataset — the registry starts empty, so mapping only becomes
  useful after enough manual runs accumulate per building. Consider a
  one-time manual walkthrough (three trusted contributors per building)
  to bootstrap coverage instead of waiting on organic traffic.
- Access points that roam/load-balance clients across multiple BSSIDs for
  the same SSID (common on enterprise Wi-Fi with band-steering) will show
  up as several distinct BSSIDs per room — the threshold model tolerates
  this per-BSSID but won't merge them into "this room has 4 known APs."
  A `covers_building` many-to-one view is a reasonable v2.

---

## 2. Passive Testing (background monitoring)

**Implemented:** `passive_monitor.py` — a loop that pings the default
gateway every 30s, logs "drop events" (3+ consecutive failures) and
reports them via a new `/log-drop` endpoint / `DropEvent` table, and runs
one real WhyPhy benchmark every 30 minutes so passive data stays
comparable to manually-submitted data.

**Deliberately scoped down from the original "IoT Ghost" idea**, and this
is the one place I'd push back on the request as originally framed:
running a full `speedtest-cli` throughput test on a tight loop in the
background would (a) consume real bandwidth continuously, (b) hammer the
public speedtest.net infrastructure other people rely on, and (c) is the
kind of thing that runs invisibly and would reasonably concern anyone who
noticed a process doing that without a clear on/off switch. The
implementation here is intentionally: opt-in only, visibly running
(prints status, stoppable with Ctrl+C or a `whyphy-monitor stop` you'd
wire up), and limited to *network* liveness — never inspects traffic,
DNS, or other on-device activity. That boundary should stay fixed even as
this feature matures; it's the difference between a Wi-Fi mapping tool and
something that looks like spyware to the person running it.

**Still needed:**
- A proper OS service wrapper so it survives logout/reboot: `launchd`
  plist on macOS, Task Scheduler on Windows, a systemd `--user` unit on
  Linux. Not included here since it's mostly OS-specific boilerplate, but
  straightforward to add per platform.
- Local queuing for drop events reported while the WhyPhy server itself is
  unreachable (currently just drops the report — fine for a v1, not for a
  tool meant to catch outages).
- A visible tray/menu-bar indicator would meaningfully improve the
  transparency story over "a terminal window you have to remember is
  open," if this heads toward a GUI.

---

## 3. Device Profiling

**Implemented:** `device_profile.py` fingerprints OS, CPU core count, RAM,
and Wi-Fi standard (802.11n/ac/ax) by parsing the same OS commands already
in use. `WhyPhy.py` computes a second score, `score_adjusted`, that
re-scores the download component against that device's Wi-Fi generation's
realistic ceiling (e.g., an 802.11n adapter hitting 140 Mbps is basically
maxed out; an 802.11ax adapter hitting 140 Mbps in the same room might
point at building-side congestion instead).

**Deliberate design choice worth flagging:** the absolute `score` (today's
leaderboard metric) and the new `score_adjusted` are both kept, never
merged. If a slow phone's adjusted score got silently averaged into a
building's leaderboard number, "Main Library: 92/100" could mean
completely different things depending on who tested last — the leaderboard
needs to stay measuring the room, and the adjusted score needs to stay
answering "is my hardware the bottleneck," and conflating them breaks both.

**Still needed:**
- No adapter theoretical-ceiling table is authoritative across every
  chipset; the constants in `WIFI_STANDARD_CEILINGS` are reasonable
  single-stream estimates, not measured. If this matters for real
  decisions, a small calibration pass (known-good hardware in a known-good
  room) would tighten those numbers.
- Currently nothing in `index.html` surfaces `score_adjusted` — it's
  captured and stored but not yet rendered anywhere.

---

## 4. Executable → WebAssembly

This is the one item where the premise needs a real technical correction
before proposing an approach, because "compile it to WASM to avoid
privacy/security concerns" doesn't hold up:

**WebAssembly is a compute sandbox, not a permissions upgrade.** Code
compiled to WASM and run in a browser tab has *less* system access than a
native executable, not more — it runs inside the exact same browser
security sandbox as ordinary JavaScript. Concretely, a browser (any
browser, this isn't a Chrome-specific restriction) will not let a WASM
module:
- Read the SSID, BSSID, or signal strength of the Wi-Fi adapter — no Web
  API exposes this, on purpose, since it would let any website silently
  fingerprint your physical location via nearby access points.
- Send raw ICMP pings — jitter and packet loss as currently measured
  (via `ping`) have no browser equivalent; you'd have to approximate them
  with repeated `fetch()`/WebSocket round-trips to your own server, which
  measures something related but not identical.
- Keep running once the tab is closed — rules out Passive Testing (#2)
  as a browser-only feature entirely.

So a WASM rewrite would *lose* three of your five WhyPhy metrics (SSID,
BSSID, real jitter/packet-loss) and the passive-monitoring feature, in
exchange for solving the "scary OS warning on launch" problem. That's a
real problem worth solving, but WASM solves a different problem than the
one causing it.

**What's actually causing "you may get a warning when trying to open it"
(from the paper's own UI copy) is that the executables are unsigned.**
Windows SmartScreen and macOS Gatekeeper warn on any binary that isn't
signed with a recognized certificate — that's true whether it's built with
PyInstaller or anything else, and it's solved by code-signing, not by a
different compilation target:
- **macOS:** an Apple Developer ID ($99/yr) lets you sign + notarize the
  build; `codesign` + `xcrun notarytool` in CI. Eliminates the Gatekeeper
  warning entirely.
- **Windows:** an EV code-signing certificate (~$200-400/yr from a CA like
  DigiCert/SSL.com) eliminates the SmartScreen warning. A standard
  (non-EV) cert helps but SmartScreen reputation still needs to build up.

**Recommended split, if a browser-based option is still worth having:**
Ship a "WhyPhy Web Lite" alongside the native tool, not instead of it —
throughput + latency only (via `fetch()` timing against your own server,
optionally with the numeric scoring/normalization logic compiled to WASM
from Rust/C for speed and code-reuse, which is a legitimate use of WASM
here), manual building entry only (no BSSID), zero install, zero OS
warnings, positioned as a 30-second "quick check" rather than a full
report. The full-fidelity tool stays a signed native download for anyone
who wants BSSID auto-mapping, real jitter/packet-loss, or passive
monitoring.

**Not implemented in this delivery** — this is a scoping/tooling decision
(get a signing certificate, set up a build pipeline) more than a code
change, so it's written up here rather than half-built.

---

## 5. Remote Access (run / stop / modify from another state)

**Implemented:**
- `deploy/whyphy.service` — a systemd unit so the app survives reboots and
  can be `start`/`stop`/`restart`ed with one command over SSH.
- `deploy/deploy.yml` — a GitHub Actions workflow: push to `main`, it SSHs
  into the box, pulls, reinstalls dependencies, restarts the service. This
  is the "modify remotely" piece — edit locally, `git push`, live.
- `server.py` — narrowly-scoped `/admin/status` and `/admin/restart` HTTP
  endpoints, token-gated via `WHYPHY_ADMIN_TOKEN` (disabled entirely if
  that env var isn't set, so there's no guessable default), for a quick
  phone check without opening a terminal.

**Why the admin endpoints are deliberately limited to status + restart,
not stop/start/shell:** an HTTP endpoint that can run arbitrary commands
on a box reachable from the internet is a real liability — one leaked
token and someone else has a shell. Restart-only, via `sudo systemctl
restart whyphy` with sudo scoped to that exact command in
`/etc/sudoers.d/`, bounds the blast radius of a leaked token to "someone
can bounce your web server," which is annoying but not dangerous. Full
guidance is in `deploy/README.md`, including putting SSH behind Tailscale
so it's not exposed to the open internet at all.

**Still needed:**
- Actually provisioning the VPS, DNS, and secrets (this delivery gives you
  the config files and steps, not a running server — those need your
  accounts/credentials, which is naturally outside what I can do here).
- Log shipping/alerting if "detect it's down" needs to be proactive rather
  than something you check manually — a simple uptime monitor (e.g.
  UptimeRobot hitting `/admin/status` or just `/`) pinging you on failure
  would close that gap cheaply.

---

## 6. Migration to whyphy.app

**Implemented:**
- `deploy/Caddyfile` — reverse proxy + automatic Let's Encrypt TLS.
- `WhyPhy.py`, `bssid_resolver.py`, `passive_monitor.py` — all updated
  from the ngrok URL to `https://whyphy.app`.
- `deploy/README.md` — full DNS + server + TLS walkthrough, and a
  checklist of everything else that still references the ngrok domain
  (README, repo metadata, and — importantly — the already-built
  `WhyPhy_Windows.zip`/`WhyPhy_Mac.zip` executables in `downloads/`, which
  have the old URL compiled in and need to be rebuilt post-migration).

**Still needed:** the actual DNS record change and first deploy, which
requires access to your domain registrar and the VPS you provision — steps
are written out in `deploy/README.md` for you to run.

---

## Suggested build order

Given the dependencies between these (remote hosting has to exist before
the domain migration means anything; BSSID mapping and device profiling
both just need the server changes deployed):

1. **Remote hosting + domain migration (5, 6)** — everything else assumes
   a stable server exists to talk to.
2. **Automated Mapping (1)** — highest leverage for reducing user friction
   per the paper's own evaluation findings (manual location entry was
   flagged as a source of error).
3. **Device Profiling (3)** — self-contained, no new infra, pairs well
   with #1's data.
4. **Passive Testing (2)** — needs the OS service wrapper work to be
   genuinely usable rather than a script you have to remember to run.
5. **WebAssembly / signed executables (4)** — this is really "fix the
   install-time warning," and code-signing gets there faster and without
   losing metrics; treat the WASM "Lite" version as optional polish after
   that's solved.
