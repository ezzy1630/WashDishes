"""Find an already enrolled ONVIF camera when its LAN address changes."""
from __future__ import annotations

import ipaddress
import re
import shutil
import socket
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit


def discovered_cameras(responses):
    found = []
    for data, sender in responses:
        try:
            source = ipaddress.ip_address(sender)
            if source.version != 4 or not source.is_private or source.is_loopback:
                continue
            root = ET.fromstring(data)
            for match in root.findall('.//{http://schemas.xmlsoap.org/ws/2005/04/discovery}ProbeMatch'):
                identity = match.findtext('{http://schemas.xmlsoap.org/ws/2004/08/addressing}EndpointReference/{http://schemas.xmlsoap.org/ws/2004/08/addressing}Address')
                addresses = match.findtext('{http://schemas.xmlsoap.org/ws/2005/04/discovery}XAddrs', '').split()
                if identity and any(urlsplit(url).hostname == sender for url in addresses):
                    found.append((identity, sender))
        except (ValueError, ET.ParseError):
            continue
    return found


def camera_addresses(responses, identity):
    return {host for identifier, host in discovered_cameras(responses) if identifier == identity}


def neighbour_mac(host):
    ip = shutil.which('ip') or '/usr/sbin/ip'
    try:
        result = subprocess.run([ip, 'neigh', 'show', 'to', host], capture_output=True, text=True, timeout=2, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    matches = re.findall(r'\blladdr ((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})\b', result.stdout)
    return matches[0].lower() if len(matches) == 1 else None


def discover(timeout=2):
    message = f'''<?xml version="1.0"?><e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" xmlns:dn="http://www.onvif.org/ver10/network/wsdl"><e:Header><w:MessageID>uuid:{uuid.uuid4()}</w:MessageID><w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To><w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action></e:Header><e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body></e:Envelope>'''.encode()
    replies = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(message, ('239.255.255.250', 3702))
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                sock.settimeout(max(.01, end - time.monotonic()))
                try:
                    data, (sender, _) = sock.recvfrom(16384)
                except TimeoutError:
                    break
                replies.append((data, sender))
    except OSError:
        return []
    return discovered_cameras(replies)
