"""Computer control: applications, processes, windows, keyboard, mouse, screen, clipboard, telemetry."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil

from .registry import Outcome, Tool, ToolError

WIN = sys.platform.startswith("win")
MAC = sys.platform == "darwin"

# name the user says -> (launch candidates per platform, process-name hints used for verification)
APPS: dict[str, dict] = {
    "chrome": {"win": ["chrome"], "mac": ["Google Chrome"], "linux": ["google-chrome", "chromium", "chromium-browser"], "proc": ["chrome", "google chrome", "chromium"]},
    "firefox": {"win": ["firefox"], "mac": ["Firefox"], "linux": ["firefox"], "proc": ["firefox"]},
    "edge": {"win": ["msedge"], "mac": ["Microsoft Edge"], "linux": ["microsoft-edge"], "proc": ["msedge", "microsoft edge"]},
    "notepad": {"win": ["notepad"], "mac": ["TextEdit"], "linux": ["gedit", "kate", "mousepad"], "proc": ["notepad", "textedit", "gedit", "kate", "mousepad"]},
    "vscode": {"win": ["code"], "mac": ["Visual Studio Code"], "linux": ["code"], "proc": ["code"]},
    "explorer": {"win": ["explorer"], "mac": ["Finder"], "linux": ["nautilus", "thunar", "dolphin", "xdg-open"], "proc": ["explorer", "finder", "nautilus", "thunar", "dolphin"]},
    "calculator": {"win": ["calc"], "mac": ["Calculator"], "linux": ["gnome-calculator", "kcalc"], "proc": ["calculator", "calculatorapp", "calc", "gnome-calculator", "kcalc"]},
    "terminal": {"win": ["wt", "cmd"], "mac": ["Terminal"], "linux": ["gnome-terminal", "konsole", "xterm"], "proc": ["windowsterminal", "cmd", "terminal", "gnome-terminal", "konsole", "xterm"]},
    "spotify": {"win": ["spotify"], "mac": ["Spotify"], "linux": ["spotify"], "proc": ["spotify"]},
    "word": {"win": ["winword"], "mac": ["Microsoft Word"], "linux": ["libreoffice --writer"], "proc": ["winword", "microsoft word", "soffice"]},
    "excel": {"win": ["excel"], "mac": ["Microsoft Excel"], "linux": ["libreoffice --calc"], "proc": ["excel", "microsoft excel", "soffice"]},
    "powerpoint": {"win": ["powerpnt"], "mac": ["Microsoft PowerPoint"], "linux": ["libreoffice --impress"], "proc": ["powerpnt", "microsoft powerpoint", "soffice"]},
    "paint": {"win": ["mspaint"], "mac": ["Preview"], "linux": ["gimp"], "proc": ["mspaint", "preview", "gimp"]},
    "task manager": {"win": ["taskmgr"], "mac": ["Activity Monitor"], "linux": ["gnome-system-monitor"], "proc": ["taskmgr", "activity monitor", "gnome-system-monitor"]},
}
ALIASES = {"google chrome": "chrome", "vs code": "vscode", "visual studio code": "vscode", "code": "vscode",
           "file explorer": "explorer", "files": "explorer", "file manager": "explorer", "calc": "calculator",
           "microsoft edge": "edge", "cmd": "terminal", "command prompt": "terminal", "powershell": "terminal"}


def _plat() -> str:
    return "win" if WIN else "mac" if MAC else "linux"


def _key(name: str) -> str:
    n = name.strip().lower()
    return ALIASES.get(n, n)


def _proc_hints(name: str) -> list[str]:
    k = _key(name)
    if k in APPS:
        return APPS[k]["proc"]
    stem = Path(name).stem.lower()
    return [stem]


def find_processes(name: str) -> list[psutil.Process]:
    hints = _proc_hints(name)
    out = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            pn = (p.info["name"] or "").lower()
            pn_stem = pn[:-4] if pn.endswith(".exe") else pn
            if any(h == pn_stem or h == pn for h in hints):
                out.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def is_running(rt, name: str):
    procs = find_processes(name)
    return {"name": name, "running": bool(procs), "pids": [p.pid for p in procs][:20]}


SHELLS = {"cmd", "powershell", "pwsh", "bash", "sh", "zsh", "wscript", "cscript", "mshta", "regsvr32", "rundll32", "python",
          "python3", "node", "perl", "ruby", "wsl", "certutil", "bitsadmin", "curl", "wget", "nc", "ncat", "ssh", "osascript"}
_BAD = set('&|<>^%"`$;\r\n')


def _launch_risk(args: dict, rt) -> int:
    """Known apps launched bare → automatic. Anything else, or any arguments → confirm. Shells/interpreters → always."""
    name = str(args.get("name", "")).strip()
    stem = Path(name.replace("\\", "/")).stem.lower()
    if stem in SHELLS or _key(name) in SHELLS:
        return 3
    if args.get("args") or _key(name) not in APPS:
        return 2
    return 1


def open_application(rt, name: str, args: list | None = None, wait_s: float = 8.0):
    args = [str(a) for a in (args or [])]
    if any(c in _BAD for c in name) or any(any(c in _BAD for c in a) for a in args):
        raise ToolError("application name/arguments contain shell metacharacters, which are not allowed")
    key = _key(name)
    cands = list(APPS.get(key, {}).get(_plat(), [])) or [name]
    before = {p.pid for p in find_processes(name)}
    launched = None
    err = ""
    for c in cands:
        try:
            if WIN:
                # 'start' resolves App Paths registrations (chrome, winword, code ...) that PATH lookup misses.
                subprocess.Popen(["cmd", "/c", "start", "", c, *args], shell=False)
            elif MAC:
                subprocess.Popen(["open", "-a", c, *(["--args", *args] if args else [])])
            else:
                parts = c.split()
                exe = shutil.which(parts[0])
                if not exe:
                    err = f"'{parts[0]}' not found on PATH"
                    continue
                subprocess.Popen([exe, *parts[1:], *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            launched = c
            break
        except (OSError, ValueError) as e:
            err = str(e)
    if not launched:
        raise ToolError(f"could not launch '{name}': {err or 'no launch candidate worked'}")
    end = time.time() + wait_s
    now: list = []
    while time.time() < end:
        now = find_processes(name)
        if now and ({p.pid for p in now} - before or before):
            break
        time.sleep(0.25)
    ok = bool(now)
    return Outcome({"name": name, "launched_as": launched, "pids": [p.pid for p in now][:10],
                    "already_running": bool(before)}, ok,
                   f"process detected: {[p.name() for p in now][:3]}" if ok else
                   f"launch command ran but no matching process appeared within {wait_s:.0f}s")


def close_application(rt, name: str, force: bool = False):
    procs = find_processes(name)
    if not procs:
        return Outcome({"name": name, "closed": 0}, True, "no matching process was running")
    for p in procs:
        try:
            p.kill() if force else p.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    gone, alive = psutil.wait_procs(procs, timeout=5)
    if alive and not force:
        for p in alive:
            try:
                p.kill()
            except Exception:
                pass
        gone2, alive = psutil.wait_procs(alive, timeout=3)
        gone += gone2
    remaining = find_processes(name)
    return Outcome({"name": name, "closed": len(gone)}, not remaining,
                   "no matching process remains" if not remaining else f"{len(remaining)} process(es) still running")


def restart_application(rt, name: str):
    close_application(rt, name)
    return open_application(rt, name)


def list_processes(rt, filter: str = "", limit: int = 30, sort_by: str = "memory"):
    rows = []
    for p in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent", "username"]):
        try:
            n = p.info["name"] or ""
            if filter and filter.lower() not in n.lower():
                continue
            rows.append({"pid": p.info["pid"], "name": n, "cpu": p.info["cpu_percent"],
                         "memory_mb": round((p.info["memory_info"].rss if p.info["memory_info"] else 0) / 1048576, 1)})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    rows.sort(key=lambda r: r["cpu" if sort_by == "cpu" else "memory_mb"], reverse=True)
    return {"count": len(rows), "processes": rows[:limit]}


def kill_process(rt, pid: int, force: bool = False):
    if pid == os.getpid():
        raise ToolError("refusing to kill M.R.X. itself")
    try:
        p = psutil.Process(pid)
        name = p.name()
        p.kill() if force else p.terminate()
        p.wait(5)
    except psutil.NoSuchProcess:
        return Outcome({"pid": pid}, True, "process is not running")
    except psutil.AccessDenied:
        raise ToolError(f"access denied terminating pid {pid}")
    except psutil.TimeoutExpired:
        pass
    ok = not psutil.pid_exists(pid)
    return Outcome({"pid": pid, "name": name}, ok, f"pid {pid} exists: {not ok}")


# --- telemetry -----------------------------------------------------------------------------------
_last_net = {"t": 0.0, "up": 0, "down": 0}


def _gpu() -> dict | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3).stdout
        name, util, mu, mt, temp = [x.strip() for x in out.strip().splitlines()[0].split(",")]
        return {"name": name, "util_percent": float(util), "mem_used_mb": float(mu), "mem_total_mb": float(mt),
                "temp_c": float(temp)}
    except Exception:
        return None


def system_stats(rt=None, top: int = 5):
    vm, now = psutil.virtual_memory(), time.time()
    net = psutil.net_io_counters()
    up = down = 0.0
    if _last_net["t"]:
        dt = max(now - _last_net["t"], 0.001)
        up, down = (net.bytes_sent - _last_net["up"]) / dt, (net.bytes_recv - _last_net["down"]) / dt
    _last_net.update(t=now, up=net.bytes_sent, down=net.bytes_recv)
    try:
        disk = psutil.disk_usage(str(Path.home().anchor or "/"))
    except Exception:
        disk = None
    temps = None
    try:
        t = psutil.sensors_temperatures()  # not available on Windows/macOS
        temps = {k: round(v[0].current, 1) for k, v in t.items() if v} or None
    except Exception:
        pass
    bat = None
    try:
        b = psutil.sensors_battery()
        if b:
            bat = {"percent": round(b.percent, 1), "plugged": b.power_plugged}
    except Exception:
        pass
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            procs.append({"pid": p.info["pid"], "name": p.info["name"],
                          "memory_mb": round(p.info["memory_info"].rss / 1048576, 1)})
        except Exception:
            continue
    procs.sort(key=lambda r: r["memory_mb"], reverse=True)
    return {"ts": now, "cpu_percent": psutil.cpu_percent(interval=None), "cpu_cores": psutil.cpu_count(),
            "ram": {"percent": vm.percent, "used_gb": round(vm.used / 1e9, 2), "total_gb": round(vm.total / 1e9, 2)},
            "gpu": _gpu(),  # None when no NVIDIA GPU / nvidia-smi: shown as "unavailable", never faked
            "disk": {"percent": disk.percent, "used_gb": round(disk.used / 1e9, 1), "total_gb": round(disk.total / 1e9, 1)} if disk else None,
            "net": {"up_bps": round(up), "down_bps": round(down)}, "temperatures_c": temps, "battery": bat,
            "uptime_s": int(now - psutil.boot_time()), "process_count": len(procs), "top_processes": procs[:top]}


# --- desktop input / screen ------------------------------------------------------------------------
def _gui():
    try:
        import pyautogui  # type: ignore
        pyautogui.FAILSAFE = True  # slam the mouse into a corner to abort
        pyautogui.size()
        return pyautogui
    except Exception as e:
        raise ToolError(f"desktop input is unavailable here ({type(e).__name__}: {e}). It needs 'pyautogui' and a "
                        f"graphical desktop session.")


def _gui_available(rt):
    try:
        _gui()
        return True, ""
    except ToolError as e:
        return False, str(e)


def keyboard_type(rt, text: str, interval: float = 0.01):
    g = _gui()
    g.write(text, interval=interval)
    return Outcome({"typed_chars": len(text)}, None, "keystrokes sent; target field content is not readable back")


def press_key(rt, key: str, presses: int = 1):
    g = _gui()
    g.press(key, presses=presses)
    return Outcome({"key": key, "presses": presses}, None, "key event sent")


def hotkey(rt, keys: list):
    g = _gui()
    g.hotkey(*[str(k) for k in keys])
    return Outcome({"keys": keys}, None, "hotkey sent")


def mouse_position(rt):
    g = _gui()
    x, y = g.position()
    w, h = g.size()
    return {"x": x, "y": y, "screen": {"width": w, "height": h}}


def mouse_move(rt, x: int, y: int, duration: float = 0.2):
    g = _gui()
    g.moveTo(x, y, duration=duration)
    px, py = g.position()
    return Outcome({"x": px, "y": py}, abs(px - x) <= 2 and abs(py - y) <= 2, f"pointer now at ({px},{py})")


def mouse_click(rt, x: int | None = None, y: int | None = None, button: str = "left", clicks: int = 1):
    g = _gui()
    g.click(x=x, y=y, button=button, clicks=clicks)
    return Outcome({"x": x, "y": y, "button": button, "clicks": clicks}, None, "click event sent")


def mouse_drag(rt, x: int, y: int, duration: float = 0.4, button: str = "left"):
    g = _gui()
    g.dragTo(x, y, duration=duration, button=button)
    return Outcome({"x": x, "y": y}, None, "drag event sent")


def mouse_scroll(rt, amount: int):
    g = _gui()
    g.scroll(amount)
    return Outcome({"amount": amount}, None, "scroll event sent")


def screenshot(rt, monitor: int = 0, region: list | None = None):
    out_dir = rt.settings.home / "screenshots"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"screen-{time.strftime('%Y%m%d-%H%M%S')}.png"
    try:
        import mss  # type: ignore
        import mss.tools
        with mss.mss() as sct:
            mons = sct.monitors  # [0]=all, 1..n physical
            if monitor >= len(mons):
                raise ToolError(f"monitor {monitor} does not exist ({len(mons) - 1} physical monitors)")
            area = {"left": region[0], "top": region[1], "width": region[2], "height": region[3]} if region else mons[monitor]
            img = sct.grab(area)
            mss.tools.to_png(img.rgb, img.size, output=str(path))
            n = len(mons) - 1
    except ImportError:
        g = _gui()
        g.screenshot(str(path), region=tuple(region) if region else None)
        n = None
    ok = path.is_file() and path.stat().st_size > 0
    return Outcome({"path": str(path), "monitors": n}, ok, f"image written ({path.stat().st_size if ok else 0} bytes)")


def screen_ocr(rt, path: str = "", monitor: int = 0):
    try:
        import pytesseract  # type: ignore
        from PIL import Image
    except ImportError:
        raise ToolError("OCR needs 'pytesseract' + 'pillow' and the Tesseract engine installed")
    if not path:
        path = screenshot(rt, monitor=monitor).result["path"]
    try:
        text = pytesseract.image_to_string(Image.open(path))
    except Exception as e:
        raise ToolError(f"OCR failed: {e}")
    return {"path": path, "text": text}


def clipboard_get(rt):
    try:
        import pyperclip  # type: ignore
        return {"text": pyperclip.paste()}
    except Exception:
        pass
    if WIN:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"], capture_output=True, text=True, timeout=5)
        return {"text": r.stdout.rstrip("\r\n")}
    raise ToolError("clipboard unavailable (install 'pyperclip'; on Linux also xclip/xsel)")


def clipboard_set(rt, text: str):
    try:
        import pyperclip  # type: ignore
        pyperclip.copy(text)
        ok = pyperclip.paste() == text
        return Outcome({"chars": len(text)}, ok, "read back from clipboard: %s" % ok)
    except Exception:
        pass
    if WIN:
        subprocess.run(["clip"], input=text.encode("utf-16"), timeout=5)
        return Outcome({"chars": len(text)}, clipboard_get(rt)["text"] == text, "read back from clipboard")
    raise ToolError("clipboard unavailable (install 'pyperclip'; on Linux also xclip/xsel)")


def _windows_lib():
    try:
        import pygetwindow as gw  # type: ignore
        return gw
    except Exception:
        return None


def list_windows(rt):
    gw = _windows_lib()
    if gw:
        try:
            return {"windows": [{"title": w.title, "minimized": getattr(w, "isMinimized", None),
                                 "maximized": getattr(w, "isMaximized", None)} for w in gw.getAllWindows() if w.title]}
        except Exception as e:
            raise ToolError(f"window enumeration failed: {e}")
    if shutil.which("wmctrl"):
        out = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=5).stdout
        return {"windows": [{"title": l.split(None, 3)[-1]} for l in out.splitlines() if l.strip()]}
    raise ToolError("window management needs 'pygetwindow' (Windows) or 'wmctrl' (Linux X11)")


def window_action(rt, title: str, action: str):
    gw = _windows_lib()
    if not gw:
        if shutil.which("wmctrl") and action == "focus":
            r = subprocess.run(["wmctrl", "-a", title], capture_output=True, timeout=5)
            return Outcome({"title": title}, None if r.returncode == 0 else False, "wmctrl focus requested")
        raise ToolError("window management needs 'pygetwindow' (Windows) or 'wmctrl' (Linux X11)")
    matches = [w for w in gw.getWindowsWithTitle(title) if w.title]
    if not matches:
        raise ToolError(f"no window with title containing '{title}'")
    w = matches[0]
    {"minimize": w.minimize, "maximize": w.maximize, "restore": w.restore, "focus": w.activate,
     "close": w.close}[action]()
    time.sleep(0.3)
    if action == "minimize":
        ok = bool(w.isMinimized)
    elif action == "maximize":
        ok = bool(w.isMaximized)
    elif action == "focus":
        act = gw.getActiveWindow()
        ok = bool(act and title.lower() in (act.title or "").lower())
    elif action == "restore":
        ok = not w.isMinimized
    else:
        ok = not [x for x in gw.getWindowsWithTitle(w.title) if x._hWnd == w._hWnd]
    return Outcome({"title": w.title, "action": action}, ok, f"window state check after '{action}': {ok}")


def power_action(rt, action: str):
    cmds = {"win": {"shutdown": ["shutdown", "/s", "/t", "5"], "restart": ["shutdown", "/r", "/t", "5"], "lock": ["rundll32.exe", "user32.dll,LockWorkStation"], "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]},
            "mac": {"shutdown": ["osascript", "-e", 'tell app "System Events" to shut down'], "restart": ["osascript", "-e", 'tell app "System Events" to restart'], "lock": ["pmset", "displaysleepnow"], "sleep": ["pmset", "sleepnow"]},
            "linux": {"shutdown": ["systemctl", "poweroff"], "restart": ["systemctl", "reboot"], "lock": ["loginctl", "lock-session"], "sleep": ["systemctl", "suspend"]}}[_plat()]
    subprocess.Popen(cmds[action])
    return Outcome({"action": action}, None, "command issued; the OS reports no result before it acts")


def open_url(rt, url: str):
    """Open a website in the user's default browser (their normal, logged-in browser — not the automation browser)."""
    import webbrowser
    from .browser import normalize_url
    u = normalize_url(url)
    if not u.startswith(("http://", "https://")):
        raise ToolError("open_url only opens http(s) websites")
    ok = webbrowser.open(u)
    if not ok:
        raise ToolError("the operating system could not open the default browser")
    return Outcome({"url": u}, None, "URL handed to the default browser; the page itself is not observable")


# ---- volume -----------------------------------------------------------------------------------------------------
def parse_pactl_volume(text: str) -> int | None:
    m = re.search(r"(\d{1,3})%", text)
    return int(m.group(1)) if m else None


def _run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
    if r.returncode != 0:
        raise ToolError(f"{cmd[0]} failed: {(r.stderr or r.stdout).strip()[:120]}")
    return r.stdout


def _win_endpoint():
    try:
        import comtypes
        from pycaw.pycaw import AudioUtilities
    except ImportError:
        raise ToolError("volume control on Windows needs the 'pycaw' package — run update.bat to install it")
    comtypes.CoInitialize()          # tools run in worker threads; COM must be initialised per thread
    dev = AudioUtilities.GetSpeakers()
    ep = getattr(dev, "EndpointVolume", None)
    if ep is not None:
        return ep
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import IAudioEndpointVolume
    return cast(dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None), POINTER(IAudioEndpointVolume))


def _read_volume() -> tuple[int, bool | None]:
    if WIN:
        ep = _win_endpoint()
        return round(ep.GetMasterVolumeLevelScalar() * 100), bool(ep.GetMute())
    if MAC:
        out = _run(["osascript", "-e", "get volume settings"])
        m = re.search(r"output volume:(\d+)", out)
        return int(m.group(1)), "output muted:true" in out
    if shutil.which("pactl"):
        v = parse_pactl_volume(_run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"]))
        muted = "yes" in _run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"]).lower()
        if v is None:
            raise ToolError("could not read the volume from pactl")
        return v, muted
    if shutil.which("amixer"):
        out = _run(["amixer", "sget", "Master"])
        v = parse_pactl_volume(out)
        if v is None:
            raise ToolError("could not read the volume from amixer")
        return v, "[off]" in out
    raise ToolError("no supported audio control found (Windows: pycaw · macOS: osascript · Linux: pactl or amixer)")


def get_volume(rt):
    v, muted = _read_volume()
    return {"percent": v, "muted": muted}


def set_volume(rt, percent: int | None = None, delta: int | None = None, mute: bool | None = None):
    if percent is None and delta is None and mute is None:
        raise ToolError("give percent, delta or mute")
    cur, _ = _read_volume()
    target = None if (percent is None and delta is None) else max(0, min(100, percent if percent is not None else cur + delta))
    if WIN:
        ep = _win_endpoint()
        if target is not None:
            ep.SetMasterVolumeLevelScalar(target / 100.0, None)
        if mute is not None:
            ep.SetMute(1 if mute else 0, None)
    elif MAC:
        if target is not None:
            _run(["osascript", "-e", f"set volume output volume {target}"])
        if mute is not None:
            _run(["osascript", "-e", f"set volume {'with' if mute else 'without'} output muted"])
    elif shutil.which("pactl"):
        if target is not None:
            _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{target}%"])
        if mute is not None:
            _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if mute else "0"])
    elif shutil.which("amixer"):
        if target is not None:
            _run(["amixer", "sset", "Master", f"{target}%"])
        if mute is not None:
            _run(["amixer", "sset", "Master", "mute" if mute else "unmute"])
    else:
        _read_volume()  # raises the "no supported audio control" error
    time.sleep(0.15)
    now, muted = _read_volume()
    ok = (target is None or abs(now - target) <= 2) and (mute is None or muted is None or muted == mute)
    return Outcome({"percent": now, "muted": muted}, ok, f"read back: volume {now}%, muted={muted}")


def tools() -> list[Tool]:
    S, I = {"type": "string"}, {"type": "integer"}
    G = _gui_available
    return [
        Tool("open_application", "Launch an application by name (e.g. chrome, vscode, notepad). Verified by detecting its process.",
             {"name": S, "args": {"type": "array", "items": S}, "wait_s": {"type": "number"}}, ["name"], open_application, risk_fn=_launch_risk, scope="plugin:windows"),
        Tool("open_url", "Open a website (e.g. https://www.youtube.com) in the user's default browser.", {"url": S}, ["url"], open_url, scope="plugin:windows"),
        Tool("get_volume", "Read the system output volume and mute state.", {}, [], get_volume, scope="plugin:windows"),
        Tool("set_volume", "Set the system volume (percent 0-100), change it by a delta, and/or mute/unmute. Verified by reading it back.",
             {"percent": I, "delta": I, "mute": {"type": "boolean"}}, [], set_volume, scope="plugin:windows"),
        Tool("is_application_running", "Check whether an application is currently running.", {"name": S}, ["name"], is_running, scope="plugin:windows"),
        Tool("close_application", "Close an application (terminates its processes).", {"name": S, "force": {"type": "boolean"}},
             ["name"], close_application, risk=2, scope="plugin:windows"),
        Tool("restart_application", "Close and relaunch an application.", {"name": S}, ["name"], restart_application, risk=2, scope="plugin:windows"),
        Tool("list_processes", "List running processes.", {"filter": S, "limit": I, "sort_by": {"type": "string", "enum": ["memory", "cpu"]}}, [], list_processes, scope="plugin:windows"),
        Tool("kill_process", "Terminate a process by PID.", {"pid": I, "force": {"type": "boolean"}}, ["pid"], kill_process, risk=2, scope="plugin:windows"),
        Tool("get_system_stats", "CPU, RAM, GPU, disk, network, battery, uptime snapshot.", {"top": I}, [], system_stats, scope="plugin:windows"),
        Tool("keyboard_type", "Type text with the physical keyboard into the focused window.", {"text": S, "interval": {"type": "number"}}, ["text"], keyboard_type, available=G, risk=2, scope="plugin:windows"),
        Tool("press_key", "Press a key (enter, esc, f5, tab…).", {"key": S, "presses": I}, ["key"], press_key, available=G, risk=2, scope="plugin:windows"),
        Tool("hotkey", "Press a key combination, e.g. ['ctrl','c'].", {"keys": {"type": "array", "items": S}}, ["keys"], hotkey, available=G, risk=2, scope="plugin:windows"),
        Tool("mouse_position", "Get the pointer position and screen size.", {}, [], mouse_position, available=G, scope="plugin:windows"),
        Tool("mouse_move", "Move the pointer.", {"x": I, "y": I, "duration": {"type": "number"}}, ["x", "y"], mouse_move, available=G, scope="plugin:windows"),
        Tool("mouse_click", "Click/double-click/right-click.", {"x": I, "y": I, "button": {"type": "string", "enum": ["left", "right", "middle"]}, "clicks": I}, [], mouse_click, available=G, risk=2, scope="plugin:windows"),
        Tool("mouse_drag", "Drag from the current position to x,y.", {"x": I, "y": I, "duration": {"type": "number"}, "button": S}, ["x", "y"], mouse_drag, available=G, risk=2, scope="plugin:windows"),
        Tool("mouse_scroll", "Scroll (positive up, negative down).", {"amount": I}, ["amount"], mouse_scroll, available=G, scope="plugin:windows"),
        Tool("screenshot", "Capture a monitor or region to a PNG.", {"monitor": I, "region": {"type": "array", "items": I}}, [], screenshot, scope="plugin:windows"),
        Tool("screen_ocr", "Read text from the screen or an image using OCR.", {"path": S, "monitor": I}, [], screen_ocr, scope="plugin:windows"),
        Tool("clipboard_get", "Read the clipboard text.", {}, [], clipboard_get, scope="plugin:windows"),
        Tool("clipboard_set", "Put text on the clipboard.", {"text": S}, ["text"], clipboard_set, scope="plugin:windows"),
        Tool("list_windows", "List open windows.", {}, [], list_windows, scope="plugin:windows"),
        Tool("window_action", "Focus/minimize/maximize/restore/close a window by title.", {"title": S, "action": {"type": "string", "enum": ["focus", "minimize", "maximize", "restore", "close"]}}, ["title", "action"], window_action, risk=1, scope="plugin:windows"),
        Tool("power_action", "Shut down, restart, lock or sleep the computer.", {"action": {"type": "string", "enum": ["shutdown", "restart", "lock", "sleep"]}}, ["action"], power_action,
             risk=3, scope="plugin:windows"),
    ]
