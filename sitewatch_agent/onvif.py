from __future__ import annotations

import base64
import hashlib
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests
from lxml import etree

SOAP = "http://www.w3.org/2003/05/soap-envelope"
WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
TDS = "http://www.onvif.org/ver10/device/wsdl"
TRT = "http://www.onvif.org/ver10/media/wsdl"
TT = "http://www.onvif.org/ver10/schema"

CONNECT_TIMEOUT = float(os.getenv("SITEWATCH_ONVIF_CONNECT_TIMEOUT_SECONDS", "4"))
READ_TIMEOUT = float(os.getenv("SITEWATCH_ONVIF_READ_TIMEOUT_SECONDS", "8"))

class OnvifAuthFailed(Exception): pass
class OnvifUnavailable(Exception): pass
class OnvifProtocolError(Exception): pass

@dataclass
class SoapResponse:
    root: etree._Element
    status_code: int
    text: str


def _lname(el: etree._Element) -> str:
    try:
        return etree.QName(el).localname
    except Exception:
        return ""


def _find_descendants(root: etree._Element, local_name: str):
    return [el for el in root.iter() if _lname(el) == local_name]


def _first_text(root: etree._Element, local_name: str) -> str | None:
    for el in root.iter():
        if _lname(el) == local_name and el.text is not None:
            return el.text.strip()
    return None


def _security_header(username: str | None, password: str | None):
    if not username:
        return None
    password = password or ""
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    nonce = os.urandom(16)
    digest = hashlib.sha1(nonce + created.encode() + password.encode()).digest()

    security = etree.Element(etree.QName(WSSE, "Security"), nsmap={"wsse": WSSE, "wsu": WSU})
    token = etree.SubElement(security, etree.QName(WSSE, "UsernameToken"))
    etree.SubElement(token, etree.QName(WSSE, "Username")).text = username
    pwd = etree.SubElement(token, etree.QName(WSSE, "Password"))
    pwd.set("Type", "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest")
    pwd.text = base64.b64encode(digest).decode()
    nonce_el = etree.SubElement(token, etree.QName(WSSE, "Nonce"))
    nonce_el.set("EncodingType", "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary")
    nonce_el.text = base64.b64encode(nonce).decode()
    etree.SubElement(token, etree.QName(WSU, "Created")).text = created
    return security


def _soap_call(url: str, action: str, body_element: etree._Element, username: str | None, password: str | None) -> SoapResponse:
    envelope = etree.Element(etree.QName(SOAP, "Envelope"), nsmap={"s": SOAP})
    header = etree.SubElement(envelope, etree.QName(SOAP, "Header"))
    sec = _security_header(username, password)
    if sec is not None:
        header.append(sec)
    body = etree.SubElement(envelope, etree.QName(SOAP, "Body"))
    body.append(body_element)
    payload = etree.tostring(envelope, xml_declaration=True, encoding="utf-8")

    try:
        r = requests.post(url, data=payload, headers={"Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"'}, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    except (requests.ConnectTimeout, requests.ConnectionError, socket.timeout) as exc:
        raise OnvifUnavailable(str(exc)) from exc
    except requests.Timeout as exc:
        raise OnvifProtocolError(f"ONVIF timeout after connection: {exc}") from exc

    text = r.text or ""
    try:
        root = etree.fromstring(r.content)
    except Exception as exc:
        if r.status_code in (401, 403):
            raise OnvifAuthFailed(f"HTTP {r.status_code}") from exc
        raise OnvifProtocolError(f"Malformed SOAP/XML response: {exc}") from exc

    # SOAP faults may be returned with HTTP 200, so always inspect the body.
    fault = next((el for el in root.iter() if _lname(el) == "Fault"), None)
    if fault is not None:
        fault_text = " ".join(x.strip() for x in fault.itertext() if x and x.strip())
        lowered = fault_text.lower()
        if "notauthorized" in lowered or "not authorized" in lowered or "401 unauthorized" in lowered or "http 401" in lowered:
            raise OnvifAuthFailed(fault_text[:1000])
        raise OnvifProtocolError(f"SOAP Fault: {fault_text[:1000]}")

    if r.status_code in (401, 403):
        raise OnvifAuthFailed(f"HTTP {r.status_code}")
    if r.status_code >= 400:
        raise OnvifProtocolError(f"HTTP {r.status_code}: {text[:500]}")

    return SoapResponse(root=root, status_code=r.status_code, text=text)


def _get_device_information(device_url: str, username: str | None, password: str | None):
    body = etree.Element(etree.QName(TDS, "GetDeviceInformation"))
    response = _soap_call(device_url, "http://www.onvif.org/ver10/device/wsdl/GetDeviceInformation", body, username, password)
    return {
        "manufacturer": _first_text(response.root, "Manufacturer"),
        "model": _first_text(response.root, "Model"),
        "firmwareVersion": _first_text(response.root, "FirmwareVersion"),
        "serialNumber": _first_text(response.root, "SerialNumber"),
        "hardwareId": _first_text(response.root, "HardwareId"),
    }


def _get_capabilities(device_url: str, username: str | None, password: str | None):
    body = etree.Element(etree.QName(TDS, "GetCapabilities"))
    category = etree.SubElement(body, etree.QName(TDS, "Category")); category.text = "All"
    response = _soap_call(device_url, "http://www.onvif.org/ver10/device/wsdl/GetCapabilities", body, username, password)
    media_xaddr = None
    for media in _find_descendants(response.root, "Media"):
        media_xaddr = _first_text(media, "XAddr")
        if media_xaddr: break
    return {"mediaXAddr": media_xaddr}


def _profile_resolution(profile: etree._Element):
    for resolution in _find_descendants(profile, "Resolution"):
        width = _first_text(resolution, "Width")
        height = _first_text(resolution, "Height")
        try:
            if width and height: return {"width": int(width), "height": int(height)}
        except ValueError: pass
    return None


def _get_profiles(media_url: str, username: str | None, password: str | None):
    body = etree.Element(etree.QName(TRT, "GetProfiles"))
    response = _soap_call(media_url, "http://www.onvif.org/ver10/media/wsdl/GetProfiles", body, username, password)
    profiles = []
    for profile in _find_descendants(response.root, "Profiles"):
        profiles.append({"token": profile.get("token") or profile.get("Token"), "name": _first_text(profile, "Name"), "resolution": _profile_resolution(profile)})
    return profiles


def _get_stream_uri(media_url: str, token: str, username: str | None, password: str | None):
    body = etree.Element(etree.QName(TRT, "GetStreamUri"))
    setup = etree.SubElement(body, etree.QName(TRT, "StreamSetup"))
    etree.SubElement(setup, etree.QName(TT, "Stream")).text = "RTP-Unicast"
    transport = etree.SubElement(setup, etree.QName(TT, "Transport"))
    etree.SubElement(transport, etree.QName(TT, "Protocol")).text = "RTSP"
    etree.SubElement(body, etree.QName(TRT, "ProfileToken")).text = token
    response = _soap_call(media_url, "http://www.onvif.org/ver10/media/wsdl/GetStreamUri", body, username, password)
    return _first_text(response.root, "Uri")


def probe_onvif(host: str, port: int = 8000, username: str | None = None, password: str | None = None) -> dict[str, Any]:
    device_url = f"http://{host}:{int(port)}/onvif/device_service"
    try:
        info = _get_device_information(device_url, username, password)
        if not any(info.values()):
            raise OnvifProtocolError("GetDeviceInformation returned no device information")
        result = {"deviceServiceUrl": device_url, **info, "profiles": []}
        media_url = None
        try:
            caps = _get_capabilities(device_url, username, password)
            result["capabilities"] = caps
            media_url = caps.get("mediaXAddr")
        except OnvifAuthFailed:
            raise
        except Exception as exc:
            result["capabilitiesError"] = str(exc)
        if not media_url:
            media_url = f"http://{host}:{int(port)}/onvif/media_service"
        result["mediaServiceUrl"] = media_url
        try:
            profiles = _get_profiles(media_url, username, password)
            for profile in profiles:
                token = profile.get("token")
                if token:
                    try: profile["rtspUri"] = _get_stream_uri(media_url, str(token), username, password)
                    except OnvifAuthFailed: raise
                    except Exception as exc: profile["streamUriError"] = str(exc)
            result["profiles"] = profiles
        except OnvifAuthFailed:
            raise
        except Exception as exc:
            result["profilesError"] = str(exc)
        return {"status": "success", "message": "ONVIF device information obtained", "result": result}
    except OnvifAuthFailed as exc:
        return {"status": "auth_failed", "message": "ONVIF authentication failed", "result": {"deviceServiceUrl": device_url, "fault": str(exc)}}
    except OnvifUnavailable as exc:
        return {"status": "unavailable", "message": f"ONVIF service unavailable: {exc}", "result": {"deviceServiceUrl": device_url}}
    except Exception as exc:
        return {"status": "error", "message": str(exc), "result": {"deviceServiceUrl": device_url}}
