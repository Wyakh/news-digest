"""
News Digest tray launcher.

Binds the local server before showing tray controls. NewsDigest.exe, the
Start Menu/login shortcuts, and news.bat all launch this entry point.
Shared optional config/launch.json chooses a fixed loopback port (default 5000).

Launch from the project root:
    .\\venv\\Scripts\\pythonw.exe tray.py
Debug with a visible terminal:
    .\\venv\\Scripts\\python.exe tray.py
"""

import ctypes
import hashlib
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
import traceback

from launch_config import load_launch_settings

# Make web/ importable so we can grab the Flask app object.
# This mirrors how `python web\app.py` would set up sys.path,
# which is necessary because app.py uses bare imports like `import runner`.
PROJECT_ROOT = Path(__file__).resolve().parent
WEB_DIR = PROJECT_ROOT / "web"
sys.path.insert(0, str(WEB_DIR))

def _load_tray_dependencies():
    # Keep optional imports inside the guarded startup path for pythonw users.
    global pystray, Image, ImageDraw, ImageFont, Notification, audio
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    from winotify import Notification, audio


def report_startup_error(exc):
    """Log a launch failure and show it even when pythonw has no console."""
    log_path = PROJECT_ROOT / "data" / "startup.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log:
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=log)
    except Exception:
        log_path = None
    address = f"{HOST}:{PORT}" if HOST is not None and PORT is not None else "the configured local address"
    message = (f"News Digest could not start on {address}.\n\n{exc}\n\n"
               "Another Windows account/session may already use this port. "
               "Check config/launch.json; no other process was stopped.")
    if log_path:
        message += f"\n\nDetails: {log_path}"
    try:
        ctypes.windll.user32.MessageBoxW(None, message, "News Digest startup error", 0x10)
    except Exception:
        try:
            print(message, file=sys.stderr)
        except Exception:
            pass


def _acquire_instance():
    """Hold a session-local, project-and-port-specific Windows instance marker."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    name = "Local\\NewsDigest-" + hashlib.sha256(
        f"{PROJECT_ROOT}:{PORT}".casefold().encode("utf-8")
    ).hexdigest()
    handle = kernel.CreateMutexW(None, False, name)
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    if error == 183:  # ERROR_ALREADY_EXISTS: this session/project is starting or running.
        kernel.CloseHandle(handle)
        return None
    return handle


def _release_instance(handle):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle(handle)

# ── Configuration ──────────────────────────────────────────────────────────
HOST = PORT = BASE_URL = None  # Resolved together inside guarded main().
ICON_PATH = PROJECT_ROOT / "assets" / "tray_icon.ico"
APP_ID = "News Digest"
POLL_INTERVAL_SEC = 0.75


# ── Single-instance check ──────────────────────────────────────────────────
def another_instance_running():
    """Return True if something is already listening on (HOST, PORT)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        try:
            s.connect((HOST, PORT))
            return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            return False


# ── Notifications ──────────────────────────────────────────────────────────
def notify(title, body):
    """Fire a simple Windows toast with the default notification sound."""
    toast = Notification(app_id=APP_ID, title=title, msg=body)
    toast.set_audio(audio.Default, loop=False)
    toast.show()


def notify_with_open(title, body, label, url):
    """Toast with an action button that opens a URL when clicked."""
    toast = Notification(app_id=APP_ID, title=title, msg=body)
    toast.set_audio(audio.Default, loop=False)
    toast.add_actions(label=label, launch=url)
    toast.show()


# ── Icon (placeholder until user supplies one) ─────────────────────────────
def load_or_create_icon():
    """Load assets/tray_icon.ico if present, else generate a placeholder."""
    if ICON_PATH.exists():
        return Image.open(ICON_PATH)
    img = Image.new("RGB", (64, 64), color=(30, 60, 120))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 44)
    except OSError:
        font = ImageFont.load_default()
    draw.text((18, 6), "N", fill="white", font=font)
    return img


# ── Helpers ────────────────────────────────────────────────────────────────
def open_url(url):
    webbrowser.open(url)


def _latest_run_id():
    import runner
    return runner._find_latest_digest_run_id()


# Direct read of runner._state under runner._lock.
# Both are technically private (underscore-prefixed) but are the canonical
# implementation; we're in the same process. Using a snapshot avoids holding
# the lock across any slow operation (toast.show, file I/O, etc).
def _runner_state_snapshot():
    import runner
    with runner._lock:
        return dict(runner._state)


# ── Flask thread ───────────────────────────────────────────────────────────
def create_server():
    from app import app  # web/app.py; imports happen before successful tray startup.
    from werkzeug.serving import make_server
    return make_server(HOST, PORT, app, threaded=True)


def start_flask(server, started):
    global _server_failure
    service_actions = server.service_actions

    def ready():
        started.set()  # Signals an actual serving-loop iteration, not just thread creation.
        service_actions()

    server.service_actions = ready
    try:
        server.serve_forever(poll_interval=0.1)
    except (Exception, SystemExit) as exc:
        _server_failure = exc
        started.set()
        if _active_icon is not None:
            _active_icon.stop()


# ── Tray menu actions ──────────────────────────────────────────────────────
def on_main_page(icon, item):
    open_url(BASE_URL + "/")


def on_newest_digest(icon, item):
    run_id = _latest_run_id()
    if run_id:
        open_url(f"{BASE_URL}/digest/{run_id}")


def on_notes_page(icon, item):
    open_url(BASE_URL + "/notes")


def on_run(icon, item):
    """Run scraper + AI digest. Aborts with a warning toast if the
    12-hour rule would be triggered — overrides must go through the
    webpage Run page so the user sees the full three-option modal."""
    import runner

    pre = runner.precheck("both")
    if pre.get("needs_confirm"):
        parts = []
        if pre.get("scraper_within_12h"):
            parts.append(f"scrape was {pre['scraper_age_hours']}h ago")
        if pre.get("ai_within_12h"):
            parts.append(f"AI digest was {pre['ai_age_hours']}h ago")
        body = (", ".join(parts) +
                ". Use the Run page in the browser to override.")
        notify("Run blocked (12-hour rule)", body)
        return

    ok, message = runner.start("both", "auto")
    if ok:
        notify(APP_ID, "Scraper & AI digest started.")
    else:
        notify("Could not start run", message)


def on_quit(icon, item):
    if _runner_state_snapshot()["active"]:
        notify(APP_ID, "A run is active. Wait for it to finish before quitting.")
        return
    icon.stop()


# ── Dynamic menu labels / enabled flags ────────────────────────────────────
def newest_digest_label(item):
    return "Newest digest" if _latest_run_id() else "Newest digest (none yet)"


def newest_digest_enabled(item):
    return _latest_run_id() is not None


def run_label(item):
    s = _runner_state_snapshot()
    if not s["active"]:
        return "Run scraper & AI digest"
    if s["phase"] == "scraper":
        return "Run scraper & AI digest (running scraper…)"
    if s["phase"] == "ai":
        return "Run scraper & AI digest (running AI…)"
    return "Run scraper & AI digest (running…)"


def run_enabled(item):
    return not _runner_state_snapshot()["active"]


# ── Completion notification ────────────────────────────────────────────────
def fire_completion_toast(prev_state):
    """Fired by the poller when active just flipped from True to False."""
    s = _runner_state_snapshot()
    exit_code = s.get("exit_code")
    error_msg = s.get("error_msg")
    job = s.get("job")

    if exit_code == 0:
        if job in ("ai", "both"):
            run_id = _latest_run_id()
            if run_id:
                notify_with_open(
                    "Run finished",
                    f"AI digest {run_id} ready.",
                    label="Open digest",
                    url=f"{BASE_URL}/digest/{run_id}",
                )
                return
        # Scraper-only success or AI success without a digest on disk.
        notify("Run finished", "Job completed successfully.")
    else:
        body = error_msg or f"Exit code {exit_code}."
        notify("Run failed", body)


# ── State poller (background thread) ───────────────────────────────────────
def state_poller(icon, stop_event=None):
    """Watches runner._state for active→idle transitions and fires toasts.
    Also calls icon.update_menu() on transitions so the (running) label
    refreshes if the menu happens to be open during the change."""
    last_signature = None  # (active, phase)
    while stop_event is None or not stop_event.is_set():
        s = _runner_state_snapshot()
        signature = (s["active"], s["phase"])

        if last_signature is not None and signature != last_signature:
            prev_active = last_signature[0]
            curr_active = signature[0]
            if prev_active and not curr_active:
                try:
                    fire_completion_toast(last_signature)
                except Exception as exc:
                    # A Windows notification failure must not stop monitoring.
                    try:
                        print(f"News Digest notification failed: {exc}", file=sys.stderr)
                    except Exception:
                        # pythonw may not have a writable stderr stream.
                        pass
            try:
                icon.update_menu()
            except Exception:
                # Icon not ready yet, or other transient failure. Harmless.
                pass

        last_signature = signature
        if stop_event is None:
            time.sleep(POLL_INTERVAL_SEC)
        else:
            stop_event.wait(POLL_INTERVAL_SEC)


# ── Main ───────────────────────────────────────────────────────────────────
_server_failure = None
_active_icon = None


def main():
    global HOST, PORT, BASE_URL, _active_icon, _server_failure
    HOST, PORT, BASE_URL = load_launch_settings(PROJECT_ROOT)
    _server_failure = None
    stop_event = threading.Event()
    handle = _acquire_instance()
    if handle is None:
        _load_tray_dependencies()
        notify(APP_ID, "Already starting or running in this Windows session. Check hidden tray icons.")
        return 0

    server = None
    flask_thread = None
    flask_started = False
    server_ready = False
    icon = None
    try:
        if another_instance_running():
            raise RuntimeError(f"Port {PORT} is occupied by another instance or Windows session.")
        _load_tray_dependencies()
        server = create_server()  # Bind synchronously: failure must not leave a successful tray.
        started = threading.Event()
        flask_thread = threading.Thread(target=start_flask, args=(server, started), daemon=True)
        flask_thread.start()
        flask_started = True
        server_ready = started.wait(5) and _server_failure is None
        if not server_ready or not flask_thread.is_alive():
            raise RuntimeError("The local server did not become ready.") from _server_failure

        menu = pystray.Menu(
            pystray.MenuItem("Main page", on_main_page, default=True),
            pystray.MenuItem(newest_digest_label, on_newest_digest, enabled=newest_digest_enabled),
            pystray.MenuItem("Notes page", on_notes_page),
            pystray.MenuItem(run_label, on_run, enabled=run_enabled),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", on_quit),
        )
        icon = pystray.Icon("news_digest", load_or_create_icon(), APP_ID, menu=menu)
        _active_icon = icon
        poller = threading.Thread(target=state_poller, args=(icon, stop_event), daemon=True)
        poller.start()
        if _server_failure is not None or not flask_thread.is_alive():
            raise RuntimeError("The local server stopped before the tray became ready.") from _server_failure

        def show_icon(active_icon):
            global _server_failure
            try:
                if _server_failure is not None or not flask_thread.is_alive():
                    raise RuntimeError("The local server stopped during tray startup.")
                active_icon.visible = True
            except (Exception, SystemExit) as exc:
                _server_failure = exc
                active_icon.stop()

        icon.run(setup=show_icon)
        if _server_failure is not None:
            raise RuntimeError("The tray or local server stopped unexpectedly.") from _server_failure
        return 0
    finally:
        stop_event.set()
        _active_icon = None
        try:
            if icon is not None:
                icon.stop()
        finally:
            try:
                if server is not None:
                    try:
                        if server_ready and flask_started and flask_thread.is_alive():
                            server.shutdown()
                    finally:
                        server.server_close()
                if flask_started:
                    flask_thread.join(timeout=2)
            finally:
                _release_instance(handle)


def run():
    original_stdout, original_stderr = sys.stdout, sys.stderr
    stream = None
    try:
        if sys.stdout is None or sys.stderr is None:
            log_path = PROJECT_ROOT / "data" / "startup.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            stream = log_path.open("a", encoding="utf-8", buffering=1)
            if sys.stdout is None:
                sys.stdout = stream
            if sys.stderr is None:
                sys.stderr = stream
        return main()
    except (Exception, SystemExit) as exc:
        report_startup_error(exc)
        return 1
    finally:
        if stream is not None:
            sys.stdout, sys.stderr = original_stdout, original_stderr
            stream.close()


if __name__ == "__main__":
    sys.exit(run())
