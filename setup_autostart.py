"""Install/remove per-user News Digest Start Menu and login shortcuts.

Build the honest project-owned launcher first:
    powershell -ExecutionPolicy Bypass -File launcher/build-launcher.ps1
Then use the project venv interpreter with this script: install, remove, status.
Nothing is launched by installation; both entry points use the same launcher and
optional config/launch.json. Python interpreter metadata is never modified.
"""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
PYTHONW = PROJECT_ROOT / "venv" / "Scripts" / "pythonw.exe"
TRAY_SCRIPT = PROJECT_ROOT / "tray.py"
LAUNCHER = PROJECT_ROOT / "NewsDigest.exe"
ICON_PATH = PROJECT_ROOT / "assets" / "tray_icon.ico"
PROGRAMS_DIR = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
STARTUP_DIR = PROGRAMS_DIR / "Startup"
SHORTCUT_PATH = STARTUP_DIR / "News Digest.lnk"
START_MENU_PATH = PROGRAMS_DIR / "News Digest.lnk"


def _owned_shortcut(shell, path):
    shortcut = shell.CreateShortcut(str(path))
    if path.exists() and Path(shortcut.TargetPath) not in (LAUNCHER, PYTHONW):
        raise RuntimeError(f"Refusing to replace or remove an unrelated shortcut: {path}")
    return shortcut


def install():
    for path in (PYTHONW, TRAY_SCRIPT, LAUNCHER):
        if not path.exists():
            raise RuntimeError(f"Missing {path.name}. Create the venv and build launcher/build-launcher.ps1 first.")
    from win32com.client import Dispatch
    shell = Dispatch("WScript.Shell")
    paths = (SHORTCUT_PATH, START_MENU_PATH)
    # Check all conflicts before changing either entry.
    shortcuts = [_owned_shortcut(shell, path) for path in paths]
    for path, shortcut in zip(paths, shortcuts):
        path.parent.mkdir(parents=True, exist_ok=True)
        shortcut.TargetPath = str(LAUNCHER)
        shortcut.Arguments = ""
        shortcut.WorkingDirectory = str(PROJECT_ROOT)
        shortcut.WindowStyle = 7
        shortcut.IconLocation = str(ICON_PATH)
        shortcut.Description = "News Digest — local interface and tray controls"
        shortcut.Save()
        print(f"Installed: {path}")
    print("Open News Digest from Start now; login Startup applies at next sign-in.")


def remove():
    from win32com.client import Dispatch
    shell = Dispatch("WScript.Shell")
    paths = (SHORTCUT_PATH, START_MENU_PATH)
    for path in paths:
        if path.exists():
            _owned_shortcut(shell, path)
    for path in paths:
        if path.exists():
            path.unlink()
            print(f"Removed: {path}")


def status():
    for path in (SHORTCUT_PATH, START_MENU_PATH):
        print(f"{'Installed' if path.exists() else 'Not installed'}: {path}")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("install", "remove", "status"):
        print("Usage: setup_autostart.py [install|remove|status]")
        return 1
    {"install": install, "remove": remove, "status": status}[sys.argv[1]]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
