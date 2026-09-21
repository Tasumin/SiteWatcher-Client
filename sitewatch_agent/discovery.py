import concurrent.futures
import ipaddress
import os
import re
import socket
import subprocess
import time

from .snmp import probe_snmp

DISCOVERY_PORTS = (80, 443, 554, 8000, 9000)
MAX_ADDRESSES_PER_CIDR = 1024
MAC_PATTERN = re.compile(r"(?i)\b([0-9a-f]{2}(?:[:-][0-9a-f]{2}){5})\b")


def normalize_mac(value):
    text = str(value or "").strip()
    match = MAC_PATTERN.search(text)
    if not match:
        return None
    parts = re.split(r"[:-]", match.group(1))
    return ":".join(part.upper() for part in parts)


def _run_neighbor_command(command):
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def neighbor_mac(host):
    host = str(host)
    if os.name == "nt":
        commands = (["arp", "-a", host],)
    else:
        commands = (
            ["ip", "neigh", "show", host],
            ["arp", "-n", host],
        )
    for command in commands:
        mac = normalize_mac(_run_neighbor_command(command))
        if mac:
            return mac
    return None


def local_ipv4():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def discovery_networks():
    configured = os.getenv("SITEWATCH_DISCOVERY_CIDRS", "").strip()
    if not configured:
        configured = os.getenv("SITEWATCH_DISCOVERY_CIDR", "").strip()
    if configured:
        raw_values = [x.strip() for x in re.split(r"[,;\s]+", configured) if x.strip()]
    else:
        ip = local_ipv4()
        raw_values = [f"{ip}/24"]

    networks = []
    seen = set()
    for raw in raw_values:
        network = ipaddress.ip_network(raw, strict=False)
        if network.version != 4:
            raise ValueError(f"Discovery currently supports IPv4 only: {raw}")
        if network.num_addresses > MAX_ADDRESSES_PER_CIDR:
            raise ValueError(
                f"Discovery CIDR {network} is too large ({network.num_addresses} addresses); "
                f"maximum is {MAX_ADDRESSES_PER_CIDR} addresses per CIDR"
            )
        key = str(network)
        if key not in seen:
            networks.append(network)
            seen.add(key)
    return networks


def discovery_snmp_communities():
    configured = os.getenv("SITEWATCH_SNMP_DISCOVERY_COMMUNITIES", "public")
    return [x.strip() for x in re.split(r"[,;\s]+", configured) if x.strip()]


def open_port(host, port, timeout=0.35):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        sock.close()


def scan_host(host):
    host = str(host)
    ports = [p for p in DISCOVERY_PORTS if open_port(host, p)]
    snmp = probe_snmp(
        host,
        discovery_snmp_communities(),
        port=161,
        timeout_seconds=float(os.getenv("SITEWATCH_SNMP_DISCOVERY_TIMEOUT_SECONDS", "0.5")),
        retries=0,
    )
    if snmp.get("detected"):
        ports.append(161)
    if not ports:
        return None
    try:
        hostname = socket.gethostbyaddr(host)[0]
    except Exception:
        hostname = None
    result = {
        "host": host,
        "hostname": hostname,
        "openPorts": sorted(set(ports)),
        "macAddress": neighbor_mac(host),
    }
    if snmp.get("detected"):
        result["snmp"] = {
            "detected": True,
            "port": 161,
            "version": snmp.get("version"),
            "sysDescr": snmp.get("sysDescr"),
        }
    return result


def scan_network():
    networks = discovery_networks()
    started = time.monotonic()
    all_results = {}

    for network in networks:
        hosts = list(network.hosts())
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(64, max(1, len(hosts)))) as pool:
            for result in pool.map(scan_host, hosts):
                if result:
                    existing = all_results.get(result["host"])
                    if existing:
                        existing["openPorts"] = sorted(set(existing["openPorts"]) | set(result["openPorts"]))
                        if not existing.get("hostname") and result.get("hostname"):
                            existing["hostname"] = result["hostname"]
                        if not existing.get("macAddress") and result.get("macAddress"):
                            existing["macAddress"] = result["macAddress"]
                        if result.get("snmp"):
                            existing["snmp"] = result["snmp"]
                    else:
                        all_results[result["host"]] = result

    results = sorted(all_results.values(), key=lambda x: ipaddress.ip_address(x["host"]))
    return networks, results, time.monotonic() - started
