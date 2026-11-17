"""
File: tray_icon.py
Purpose: Optional system tray / menu-bar indicator for passive_monitor.py.

A background process that measures your network with no visible sign it's
running erodes trust, even when all it does is ping a gateway. A tray icon
that is always visible, always one click from "Stop Monitoring" and one click
from the dashboard is the other half of the consent/transparency notes in
passive_monitor.py.

Optional: if `pystray` and `Pillow` aren't installed, passive_monitor.py
falls back to console output. Install with:
    pip install -r requirements-monitor.txt

Linux tray support depends on the desktop environment having a system tray at
all, so pystray's Linux backend can be flaky. macOS and Windows are the
intended targets.
"""
import threading

try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False


def _make_icon_image(color):
    """A plain filled circle - the color (status) is what needs to be legible."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 6
    draw.ellipse((margin, margin, size - margin, size - margin), fill=color)
    return img


COLOR_OK = (40, 167, 69, 255)        # green
COLOR_DROP = (220, 53, 69, 255)      # red
COLOR_STARTING = (255, 193, 7, 255)  # amber


class WhyPhyTray:
    """
    Thin wrapper around pystray.Icon.

        tray = WhyPhyTray(on_stop=..., on_open_dashboard=..., dashboard_url=...)
        tray.run(setup=start_background_thread)

    run() blocks the calling thread (required on macOS, where the tray icon
    must own the main thread), so the monitoring loop must run in a separate
    thread started from the `setup` callback.
    """

    def __init__(self, on_stop, on_open_dashboard, dashboard_url):
        if not TRAY_AVAILABLE:
            raise RuntimeError("pystray/Pillow not installed - tray unavailable")
        self._on_stop = on_stop
        self._status_text = "Starting..."
        self._icon = pystray.Icon(
            "whyphy-monitor",
            _make_icon_image(COLOR_STARTING),
            "WhyPhy Monitor - starting...",
            menu=pystray.Menu(
                pystray.MenuItem(lambda item: self._status_text, None, enabled=False),
                pystray.MenuItem("Open Dashboard", lambda: on_open_dashboard(dashboard_url)),
                pystray.MenuItem("Stop Monitoring", self._handle_stop),
            ),
        )

    def _handle_stop(self):
        self._on_stop()
        self._icon.stop()

    def set_status(self, text, dropped=False):
        """Called from the monitoring thread to update what the tray shows."""
        self._status_text = text
        self._icon.title = f"WhyPhy Monitor - {text}"
        self._icon.icon = _make_icon_image(COLOR_DROP if dropped else COLOR_OK)

    def run(self, setup):
        def _setup(icon):
            icon.visible = True
            setup()
        self._icon.run(setup=_setup)


def run_in_background_thread(target, *args, **kwargs):
    t = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t
