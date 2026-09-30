import ipaddress

import pytest

from mrx.services import network as net
from mrx.tools.registry import ToolError

ARP_LINUX = """192.168.1.1 dev eth0 lladdr aa:bb:cc:dd:ee:01 REACHABLE
192.168.1.20 dev eth0 lladdr 3a:11:22:33:44:55 STALE
192.168.1.30 dev eth0  FAILED
224.0.0.251 dev eth0 lladdr 01:00:5e:00:00:fb PERMANENT
"""
ARP_WIN = """Interface: 192.168.1.5 --- 0x7
  Internet Address      Physical Address      Type
  192.168.1.1           aa-bb-cc-dd-ee-01     dynamic
  192.168.1.255         ff-ff-ff-ff-ff-ff     static
  224.0.0.22            01-00-5e-00-00-16     static
"""
ARP_MAC = "? (192.168.1.7) at b8:27:eb:12:34:56 on en0 ifscope [ethernet]\n? (192.168.1.8) at (incomplete) on en0"


def test_parse_neighbour_tables():
    assert dict(net.parse_neighbours(ARP_LINUX)) == {"192.168.1.1": "aa:bb:cc:dd:ee:01", "192.168.1.20": "3a:11:22:33:44:55"}
    assert dict(net.parse_neighbours(ARP_WIN)) == {"192.168.1.1": "aa:bb:cc:dd:ee:01"}
    assert dict(net.parse_neighbours(ARP_MAC)) == {"192.168.1.7": "b8:27:eb:12:34:56"}


def test_vendor_and_randomized_mac(tmp_path):
    oui = net.OUI(tmp_path / "none.csv")
    assert oui.vendor("b8:27:eb:12:34:56") == "Raspberry Pi Foundation"
    assert oui.vendor("aa:bb:cc:dd:ee:ff") is None      # unknown stays unknown, never guessed
    assert net.is_randomized_mac("3a:11:22:33:44:55") and not net.is_randomized_mac("b8:27:eb:12:34:56")


def test_oui_file_loading(tmp_path):
    f = tmp_path / "oui.csv"
    f.write_text("Registry,Assignment,Organization Name,Organization Address\nMA-L,AABBCC,Acme Corp,Somewhere\n")
    assert net.OUI(f).vendor("aa:bb:cc:00:00:01") == "Acme Corp"


def test_classification():
    c = net.classify
    assert c(None, None, None, None, True, False, False) == "Router"
    assert c(None, "HP LaserJet", None, None, False, False, False) == "Printer"
    assert c(None, None, {"server": "Roku/9.4 UPnP"}, None, False, False, False) == "TV"
    assert c(None, None, None, {"services": ["_googlecast"], "names": []}, False, False, False) == "TV"
    assert c("Raspberry Pi Foundation", None, None, None, False, False, False) == "IoT device"
    assert c(None, None, None, None, False, True, False).startswith("Phone")
    assert c(None, None, None, None, False, False, False) == "Unknown"


def test_ssdp_parse():
    h = net.parse_ssdp(b"HTTP/1.1 200 OK\r\nSERVER: Linux UPnP/1.0 Roku\r\nLOCATION: http://192.168.1.9:8060/\r\nST: upnp:rootdevice\r\n\r\n")
    assert h["server"].startswith("Linux") and h["location"].endswith(":8060/")


async def test_refuses_public_network_sweep():
    with pytest.raises(ToolError):
        await net.ping_sweep("8.8.8.0/24")
    with pytest.raises(ToolError):
        await net.ping_sweep("10.0.0.0/8")   # far too large


async def test_scan_pipeline_with_stubbed_discovery(rt, monkeypatch):
    """Discovery primitives are stubbed (they need a real LAN); the pipeline around them is real."""
    monkeypatch.setattr(net, "local_networks", lambda: [{"iface": "eth0", "ip": "192.168.1.5", "network": "192.168.1.0/24", "mac": "b8:27:eb:00:00:05"}])
    monkeypatch.setattr(net, "default_gateway", lambda: "192.168.1.1")
    monkeypatch.setattr(net, "read_neighbours", lambda: [("192.168.1.1", "aa:bb:cc:dd:ee:01"), ("192.168.1.20", "3a:11:22:33:44:55")])
    async def sweep(n, max_hosts=1024): return ["192.168.1.1", "192.168.1.20"]
    async def ssdp(timeout=3.0): return {"192.168.1.20": {"server": "Roku UPnP", "st": [], "location": ""}}
    async def mdns(timeout=3.0): raise ToolError("mDNS needs the 'zeroconf' package")
    monkeypatch.setattr(net, "ping_sweep", sweep)
    monkeypatch.setattr(net, "ssdp_discover", ssdp)
    monkeypatch.setattr(net, "mdns_discover", mdns)
    monkeypatch.setattr(net, "reverse_dns", lambda ip: None)
    q = rt.bus.open_queue()
    res = await rt.radar.scan()
    types = []
    while not q.empty():
        types.append(q.get_nowait()["type"])
    assert types[0] == "network.scan_started" and types[-1] == "network.scan_completed"
    assert types.count("network.device_found") == 3
    by_ip = {d["ip"]: d for d in res["devices"]}
    assert by_ip["192.168.1.1"]["device_type"] == "Router" and by_ip["192.168.1.5"]["vendor"] == "Raspberry Pi Foundation"
    assert by_ip["192.168.1.20"]["device_type"] == "TV" and by_ip["192.168.1.20"]["randomized_mac"] is True
    assert res["skipped_methods"] == {"mdns": "mDNS needs the 'zeroconf' package"}   # reported, not hidden
    assert len(rt.radar.known_devices()) == 3
    # tool layer
    r = await rt.registry.execute("identify_device", {"ip": "192.168.1.1"})
    assert r.success and r.result["device"]["device_type"] == "Router"
    r = await rt.registry.execute("identify_device", {"ip": "192.168.1.99"})
    assert not r.success   # not invented


async def test_scan_with_no_local_network_fails_honestly(rt, monkeypatch):
    monkeypatch.setattr(net, "local_networks", lambda: [])
    r = await rt.registry.execute("scan_network", {})
    assert not r.success and "no private local network" in r.error
