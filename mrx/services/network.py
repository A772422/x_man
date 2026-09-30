"""Network Radar: discovers devices on the *local* networks this computer is attached to.
Passive/light methods only (neighbour table, ICMP echo on your own subnet, SSDP, mDNS). It refuses to probe
public address space and never port-scans."""
from __future__ import annotations

import asyncio
import csv
import ipaddress
import json
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import psutil

from ..tools.registry import Outcome, Tool, ToolError

# A deliberately small list of prefixes that are unambiguous. The full IEEE registry can be loaded with
# `update_oui_database`; anything not found is reported as "Unknown", never guessed.
BUILTIN_OUI = {
    "B827EB": "Raspberry Pi Foundation", "DCA632": "Raspberry Pi Trading", "E45F01": "Raspberry Pi Trading",
    "D83ADD": "Raspberry Pi Trading", "000C29": "VMware", "005056": "VMware", "080027": "Oracle VirtualBox",
    "00155D": "Microsoft Hyper-V", "525400": "QEMU/KVM virtual NIC",
}
MAC_RE = r"(?:[0-9a-fA-F]{2}[:\-]){5}[0-9a-fA-F]{2}"
IP_RE = r"\b(?:\d{1,3}\.){3}\d{1,3}\b"


def norm_mac(mac: str) -> str:
    return mac.lower().replace("-", ":")


def is_randomized_mac(mac: str) -> bool:
    """Locally administered bit set → private/randomised address (typical of modern phones)."""
    return bool(int(mac[:2], 16) & 0x02)


class OUI:
    def __init__(self, path: Path):
        self.path = path
        self.table = dict(BUILTIN_OUI)
        if path.exists():
            try:
                with path.open(newline="", encoding="utf-8") as f:
                    for row in csv.reader(f):
                        if len(row) >= 3 and re.fullmatch(r"[0-9A-Fa-f]{6}", row[1]):
                            self.table[row[1].upper()] = row[2]
            except OSError:
                pass

    def vendor(self, mac: str) -> str | None:
        return self.table.get(mac.replace(":", "").upper()[:6])


def parse_neighbours(text: str) -> list[tuple[str, str]]:
    """Parse `ip neigh`, /proc/net/arp, Windows `arp -a`, macOS `arp -an` output → [(ip, mac)]."""
    out = {}
    for line in text.splitlines():
        ip, mac = re.search(IP_RE, line), re.search(MAC_RE, line)
        if not (ip and mac):
            continue
        m = norm_mac(mac.group(0))
        if m in ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff") or "FAILED" in line or "INCOMPLETE" in line:
            continue
        try:
            a = ipaddress.ip_address(ip.group(0))
        except ValueError:
            continue
        if a.is_multicast or ip.group(0).endswith(".255"):
            continue
        out[ip.group(0)] = m
    return list(out.items())


def read_neighbours() -> list[tuple[str, str]]:
    texts = []
    try:
        if sys.platform.startswith("win") or sys.platform == "darwin":
            texts.append(subprocess.run(["arp", "-a" if sys.platform.startswith("win") else "-an"],
                                        capture_output=True, text=True, timeout=8).stdout)
        else:
            if shutil.which("ip"):
                texts.append(subprocess.run(["ip", "neigh"], capture_output=True, text=True, timeout=8).stdout)
            try:
                texts.append(Path("/proc/net/arp").read_text())
            except OSError:
                pass
    except (OSError, subprocess.SubprocessError):
        pass
    merged: dict[str, str] = {}
    for t in texts:
        merged.update(dict(parse_neighbours(t)))
    return list(merged.items())


def local_networks() -> list[dict]:
    nets = []
    stats = psutil.net_if_stats()
    for iface, addrs in psutil.net_if_addrs().items():
        if iface in stats and not stats[iface].isup:
            continue
        for a in addrs:
            if a.family != socket.AF_INET or not a.netmask:
                continue
            ip = ipaddress.ip_address(a.address)
            if ip.is_loopback or ip.is_link_local or not ip.is_private:
                continue
            net = ipaddress.ip_network(f"{a.address}/{a.netmask}", strict=False)
            mac = next((x.address for x in addrs if x.family == psutil.AF_LINK), None)
            nets.append({"iface": iface, "ip": a.address, "network": str(net), "mac": norm_mac(mac) if mac and re.fullmatch(MAC_RE, mac) else None})
    return nets


def default_gateway() -> str | None:
    try:
        if sys.platform.startswith("linux"):
            for line in Path("/proc/net/route").read_text().splitlines()[1:]:
                f = line.split()
                if f[1] == "00000000":
                    return socket.inet_ntoa(int(f[2], 16).to_bytes(4, "little"))
        elif sys.platform.startswith("win"):
            out = subprocess.run(["route", "print", "-4", "0.0.0.0"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r"0\.0\.0\.0\s+0\.0\.0\.0\s+(" + IP_RE + ")", out)
            return m.group(1) if m else None
        else:
            out = subprocess.run(["netstat", "-rn"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r"default\s+(" + IP_RE + ")", out)
            return m.group(1) if m else None
    except Exception:
        return None
    return None


async def _ping(ip: str, sem: asyncio.Semaphore) -> bool:
    cmd = ["ping", "-n", "1", "-w", "600", ip] if sys.platform.startswith("win") else \
          ["ping", "-c", "1", "-W", "1", ip] if not sys.platform == "darwin" else ["ping", "-c", "1", "-W", "800", ip]
    async with sem:
        try:
            p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            return (await asyncio.wait_for(p.wait(), 3)) == 0
        except (OSError, asyncio.TimeoutError):
            return False


async def ping_sweep(network: str, max_hosts: int = 1024) -> list[str]:
    net = ipaddress.ip_network(network, strict=False)
    if not net.is_private:
        raise ToolError("refusing to sweep a non-private network")
    if not shutil.which("ping"):
        raise ToolError("'ping' is not available on this system")
    hosts = list(net.hosts())
    if len(hosts) > max_hosts:
        raise ToolError(f"{net} has {len(hosts)} hosts; limit is {max_hosts}")
    sem = asyncio.Semaphore(64)
    res = await asyncio.gather(*[_ping(str(h), sem) for h in hosts])
    return [str(h) for h, ok in zip(hosts, res) if ok]


SSDP_MSG = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\nST: ssdp:all\r\n\r\n").encode()


def parse_ssdp(data: bytes) -> dict:
    hdr = {}
    for line in data.decode("utf-8", "replace").splitlines()[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            hdr[k.strip().lower()] = v.strip()
    return hdr


def _ssdp_sync(timeout: float) -> dict[str, dict]:
    found: dict[str, dict] = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.settimeout(0.5)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    try:
        s.sendto(SSDP_MSG, ("239.255.255.250", 1900))
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, addr = s.recvfrom(65507)
            except socket.timeout:
                continue
            h = parse_ssdp(data)
            d = found.setdefault(addr[0], {"server": "", "location": "", "st": set()})
            d["server"] = d["server"] or h.get("server", "")
            d["location"] = d["location"] or h.get("location", "")
            d["st"].add(h.get("st", ""))
    except OSError:
        pass
    finally:
        s.close()
    for d in found.values():
        d["st"] = sorted(d["st"])
    return found


async def ssdp_discover(timeout: float = 3.0) -> dict[str, dict]:
    found = await asyncio.to_thread(_ssdp_sync, timeout)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=2.0) as c:
            for ip, d in found.items():
                loc = d.get("location")
                if not loc or urlparse(loc).hostname != ip:  # only trust a description served by the responder itself
                    continue
                try:
                    x = (await c.get(loc)).text
                    for tag in ("friendlyName", "manufacturer", "modelName", "deviceType"):
                        m = re.search(rf"<{tag}>([^<]+)</{tag}>", x)
                        if m:
                            d[tag] = m.group(1)
                except Exception:
                    pass
    except ImportError:
        pass
    return found


MDNS_TYPES = ["_http._tcp.local.", "_airplay._tcp.local.", "_googlecast._tcp.local.", "_ipp._tcp.local.",
              "_printer._tcp.local.", "_workstation._tcp.local.", "_smb._tcp.local.", "_hap._tcp.local.",
              "_spotify-connect._tcp.local.", "_companion-link._tcp.local.", "_device-info._tcp.local."]


def _mdns_sync(timeout: float) -> dict[str, dict]:
    from zeroconf import ServiceBrowser, Zeroconf  # optional dependency
    found: dict[str, dict] = {}

    class L:
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, 1500)
            if info:
                for ip in info.parsed_addresses():
                    d = found.setdefault(ip, {"names": set(), "services": set()})
                    d["names"].add(name.split(".")[0])
                    d["services"].add(type_.split(".")[0])
                    if info.server:
                        d["host"] = info.server.rstrip(".")

        def update_service(self, *a): ...
        def remove_service(self, *a): ...

    zc = Zeroconf()
    try:
        browsers = [ServiceBrowser(zc, t, L()) for t in MDNS_TYPES]
        time.sleep(timeout)
        for b in browsers:
            b.cancel()
    finally:
        zc.close()
    for d in found.values():
        d["names"], d["services"] = sorted(d["names"]), sorted(d["services"])
    return found


async def mdns_discover(timeout: float = 3.0) -> dict[str, dict]:
    try:
        import zeroconf  # noqa: F401
    except ImportError:
        raise ToolError("mDNS needs the 'zeroconf' package")
    return await asyncio.to_thread(_mdns_sync, timeout)


def reverse_dns(ip: str) -> str | None:
    try:
        socket.setdefaulttimeout(1.0)
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return None
    finally:
        socket.setdefaulttimeout(None)


def classify(vendor: str | None, hostname: str | None, ssdp: dict | None, mdns: dict | None,
             is_gateway: bool, randomized: bool, is_self: bool) -> str:
    if is_gateway:
        return "Router"
    if is_self:
        return "PC/Laptop (this computer)"
    blob = " ".join(filter(None, [vendor, hostname, (ssdp or {}).get("server"), (ssdp or {}).get("friendlyName"),
                                   (ssdp or {}).get("deviceType"), (ssdp or {}).get("modelName"),
                                   " ".join((mdns or {}).get("names", [])), " ".join((mdns or {}).get("services", []))])).lower()
    rules = [("Printer", ("printer", "_ipp", "laserjet", "deskjet", "officejet", "epson", "brother")),
             ("Camera", ("camera", "ipcam", "hikvision", "dahua", "nest cam", "wyze")),
             ("TV", ("tv", "roku", "chromecast", "_googlecast", "bravia", "webos", "samsung-tv", "mediarenderer", "airplay")),
             ("Tablet", ("ipad", "tablet")),
             ("Phone", ("iphone", "android", "pixel", "galaxy", "oneplus", "xiaomi", "redmi")),
             ("Laptop", ("macbook", "laptop", "thinkpad")),
             ("PC", ("desktop", "windows", "_workstation", "_smb")),
             ("IoT device", ("raspberry", "esp", "tuya", "espressif", "_hap", "shelly", "sonoff", "iot", "plug", "bulb", "hue", "alexa", "echo"))]
    for label, keys in rules:
        if any(k in blob for k in keys):
            return label
    if randomized:
        return "Phone/Tablet (randomised MAC)"
    return "Unknown"


class NetworkRadar:
    def __init__(self, rt):
        self.rt = rt
        self.oui = OUI(rt.settings.home / "oui.csv")
        self.scanning = False
        self.last: dict = {"devices": [], "completed_at": None, "methods": [], "networks": []}

    async def update_oui(self) -> int:
        import httpx
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
            r = await c.get("https://standards-oui.ieee.org/oui/oui.csv", headers={"User-Agent": "M.R.X./0.1"})
            r.raise_for_status()
        (self.rt.settings.home / "oui.csv").write_text(r.text, encoding="utf-8")
        self.oui = OUI(self.rt.settings.home / "oui.csv")
        return len(self.oui.table)

    async def scan(self, methods: list[str] | None = None) -> dict:
        if self.scanning:
            raise ToolError("a scan is already running")
        self.scanning = True
        bus = self.rt.bus
        methods = methods or self.rt.settings.get("network.methods", ["arp", "ping", "ssdp", "mdns"])
        try:
            nets = local_networks()
            if not nets:
                raise ToolError("no private local network interface is up; connect to a LAN/Wi-Fi network")
            await bus.emit("network.scan_started", {"networks": nets, "methods": methods})
            gateway = default_gateway()
            own = {n["ip"]: n for n in nets}
            devices: dict[str, dict] = {}
            used, skipped = [], {}
            names = self.rt.settings.get("network.device_names", {}) or {}

            async def upsert(ip: str, mac: str | None, method: str, extra: dict | None = None):
                key = mac or f"ip:{ip}"
                new = key not in devices
                d = devices.setdefault(key, {"mac": mac, "ip": ip, "methods": [], "ssdp": None, "mdns": None})
                d["ip"] = ip
                if method not in d["methods"]:
                    d["methods"].append(method)
                if extra:
                    d.update({k: v for k, v in extra.items() if v})
                if new:
                    await bus.emit("network.device_found", {"ip": ip, "mac": mac, "method": method})
                    await bus.emit("network.progress", {"message": f"{len(devices)} devices found…"})

            for ip, n in own.items():
                await upsert(ip, n["mac"], "self", {"is_self": True})

            if "ping" in methods:
                await bus.emit("network.progress", {"message": "Probing subnet with ICMP echo…"})
                for n in nets:
                    try:
                        alive = await ping_sweep(n["network"])
                        used.append("ping")
                        for ip in alive:
                            if ip not in own:
                                await upsert(ip, None, "ping")
                    except ToolError as e:
                        skipped["ping"] = str(e)
            if "arp" in methods:
                await bus.emit("network.progress", {"message": "Reading neighbour (ARP) table…"})
                neigh = await asyncio.to_thread(read_neighbours)
                used.append("arp")
                in_nets = [ipaddress.ip_network(n["network"]) for n in nets]
                for ip, mac in neigh:
                    if any(ipaddress.ip_address(ip) in nn for nn in in_nets) and ip not in own:
                        prior = devices.pop(f"ip:{ip}", None)  # same device already seen by ping → enrich, don't re-announce
                        if prior:
                            prior["mac"] = mac
                            prior["methods"].append("arp")
                            devices[mac] = prior
                        else:
                            await upsert(ip, mac, "arp")
            if "ssdp" in methods:
                await bus.emit("network.progress", {"message": "Listening for SSDP/UPnP announcements…"})
                s = await ssdp_discover()
                used.append("ssdp")
                for ip, info in s.items():
                    key = next((k for k, d in devices.items() if d["ip"] == ip), None)
                    if key:
                        devices[key]["ssdp"] = info
                        if "ssdp" not in devices[key]["methods"]:
                            devices[key]["methods"].append("ssdp")
            if "mdns" in methods:
                await bus.emit("network.progress", {"message": "Listening for mDNS/Bonjour services…"})
                try:
                    m = await mdns_discover()
                    used.append("mdns")
                    for ip, info in m.items():
                        key = next((k for k, d in devices.items() if d["ip"] == ip), None)
                        if key:
                            devices[key]["mdns"] = info
                            devices[key]["methods"].append("mdns")
                except ToolError as e:
                    skipped["mdns"] = str(e)

            await bus.emit("network.progress", {"message": "Identifying devices…"})
            now = time.time()
            out = []
            for key, d in devices.items():
                d["hostname"] = (d.get("mdns") or {}).get("host") or await asyncio.to_thread(reverse_dns, d["ip"]) \
                    or (d.get("ssdp") or {}).get("friendlyName")
                mac = d["mac"]
                d["vendor"] = self.oui.vendor(mac) if mac else None
                rnd = bool(mac and is_randomized_mac(mac))
                if rnd:
                    d["vendor"] = d["vendor"] or None
                d["randomized_mac"] = rnd
                d["device_type"] = classify(d["vendor"], d["hostname"], d.get("ssdp"), d.get("mdns"),
                                            d["ip"] == gateway, rnd, bool(d.get("is_self")))
                d["name"] = names.get(mac or d["ip"]) or d["hostname"] or (d.get("ssdp") or {}).get("friendlyName") or d["ip"]
                d["connection"] = "Unknown"  # wired vs Wi-Fi is not observable from the host
                d["status"] = "online"
                d["last_seen"] = now
                d["is_gateway"] = d["ip"] == gateway
                out.append(d)
                self._persist(d)
            # devices remembered from earlier scans but not seen now
            seen_macs = {d["mac"] for d in out if d["mac"]}
            for r in self.rt.db.query("SELECT * FROM devices"):
                if r["mac"] not in seen_macs and not r["mac"].startswith("ip:"):
                    self.rt.db.execute("UPDATE devices SET status='offline' WHERE mac=?", (r["mac"],))
            self.last = {"devices": out, "completed_at": now, "methods": used, "skipped_methods": skipped,
                         "networks": nets, "gateway": gateway}
            await bus.emit("network.scan_completed", {"count": len(out), "methods": used, "skipped": skipped})
            return self.last
        finally:
            self.scanning = False

    def _persist(self, d: dict) -> None:
        key = d["mac"] or f"ip:{d['ip']}"
        old = self.rt.db.one("SELECT first_seen FROM devices WHERE mac=?", (key,))
        self.rt.db.execute(
            "INSERT OR REPLACE INTO devices(mac,ip,hostname,vendor,device_type,connection,status,methods,first_seen,last_seen,extra) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (key, d["ip"], d.get("hostname"), d.get("vendor"), d["device_type"], d["connection"], d["status"],
             ",".join(d["methods"]), old["first_seen"] if old else d["last_seen"], d["last_seen"],
             json.dumps({"randomized_mac": d.get("randomized_mac"), "ssdp": d.get("ssdp"), "mdns": d.get("mdns")}, default=str)))

    def known_devices(self) -> list[dict]:
        return self.rt.db.query("SELECT * FROM devices ORDER BY last_seen DESC")


async def scan_network(rt, methods: list | None = None):
    res = await rt.radar.scan(methods)
    devs = [{"name": d["name"], "ip": d["ip"], "mac": d["mac"], "vendor": d["vendor"], "type": d["device_type"],
             "methods": d["methods"]} for d in res["devices"]]
    return Outcome({"count": len(devs), "devices": devs, "methods_used": res["methods"],
                    "skipped_methods": res["skipped_methods"], "gateway": res["gateway"]},
                   len(devs) > 0, f"{len(devs)} device(s) found via {', '.join(res['methods']) or 'no method'}")


async def identify_device(rt, ip: str):
    for r in rt.radar.last["devices"]:
        if r["ip"] == ip:
            return {"device": {k: r.get(k) for k in ("name", "ip", "mac", "vendor", "device_type", "hostname", "methods")}}
    row = next((d for d in rt.radar.known_devices() if d["ip"] == ip), None)
    if row:
        return {"device": row, "note": "from the device history; not seen in the latest scan"}
    raise ToolError(f"{ip} has not been discovered; run scan_network first")


async def update_oui_database(rt):
    n = await rt.radar.update_oui()
    return Outcome({"entries": n}, n > len(BUILTIN_OUI), f"{n} vendor prefixes loaded from the IEEE registry")


def tools() -> list[Tool]:
    return [
        Tool("scan_network", "Discover devices on the local network (ARP, ICMP, SSDP, mDNS).",
             {"methods": {"type": "array", "items": {"type": "string", "enum": ["arp", "ping", "ssdp", "mdns"]}}}, [],
             scan_network, plugin="network", scope="network", timeout_s=180, parallel_safe=False),
        Tool("identify_device", "Show details for a discovered device by IP.", {"ip": {"type": "string"}}, ["ip"], identify_device, plugin="network", scope="network"),
        Tool("update_oui_database", "Download the IEEE MAC vendor registry for better manufacturer names.", {}, [], update_oui_database, plugin="network", scope="network"),
    ]
