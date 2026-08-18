import concurrent.futures
import ipaddress
import os
import socket
import time

DISCOVERY_PORTS = (80, 443, 554, 8000, 9000)

def local_ipv4():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()

def discovery_network():
    configured = os.getenv("SITEWATCH_DISCOVERY_CIDR", "").strip()
    if configured:
        network = ipaddress.ip_network(configured, strict=False)
    else:
        ip = local_ipv4()
        network = ipaddress.ip_network(f"{ip}/24", strict=False)

    if network.version != 4:
        raise ValueError("Discovery currently supports IPv4 only")
    if network.num_addresses > 1024:
        raise ValueError(f"Discovery CIDR is too large ({network.num_addresses} addresses); maximum is 1024")
    return network

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
    if not ports:
        return None
    try:
        hostname = socket.gethostbyaddr(host)[0]
    except Exception:
        hostname = None
    return {"host": host, "hostname": hostname, "openPorts": ports}

def scan_network():
    network = discovery_network()
    hosts = list(network.hosts())
    results = []
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(64, max(1, len(hosts)))) as pool:
        for result in pool.map(scan_host, hosts):
            if result:
                results.append(result)
    return network, results, time.monotonic() - started
