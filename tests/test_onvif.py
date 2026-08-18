import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from sitewatch_agent.onvif import probe_onvif

AUTH_FAULT = '''<?xml version="1.0"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:ter="http://www.onvif.org/ver10/error"><s:Body><s:Fault><s:Code><s:Value>s:Sender</s:Value><s:Subcode><s:Value>ter:NotAuthorized</s:Value></s:Subcode></s:Code><s:Reason><s:Text>Error 401: HTTP 401 Unauthorized</s:Text></s:Reason></s:Fault></s:Body></s:Envelope>'''
DEVICE = '''<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl"><s:Body><tds:GetDeviceInformationResponse><tds:Manufacturer>REOLINK</tds:Manufacturer><tds:Model>RLC-TEST</tds:Model><tds:FirmwareVersion>v1.2.3</tds:FirmwareVersion><tds:SerialNumber>ABC123</tds:SerialNumber><tds:HardwareId>HW1</tds:HardwareId></tds:GetDeviceInformationResponse></s:Body></s:Envelope>'''
CAPS = '''<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema"><s:Body><tds:GetCapabilitiesResponse><tds:Capabilities><tt:Media><tt:XAddr>http://127.0.0.1:{port}/onvif/media_service</tt:XAddr></tt:Media></tds:Capabilities></tds:GetCapabilitiesResponse></s:Body></s:Envelope>'''
PROFILES = '''<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema"><s:Body><trt:GetProfilesResponse><trt:Profiles token="main"><tt:Name>Main Stream</tt:Name><tt:VideoEncoderConfiguration><tt:Resolution><tt:Width>2560</tt:Width><tt:Height>1440</tt:Height></tt:Resolution></tt:VideoEncoderConfiguration></trt:Profiles><trt:Profiles token="sub"><tt:Name>Sub Stream</tt:Name><tt:VideoEncoderConfiguration><tt:Resolution><tt:Width>640</tt:Width><tt:Height>360</tt:Height></tt:Resolution></tt:VideoEncoderConfiguration></trt:Profiles></trt:GetProfilesResponse></s:Body></s:Envelope>'''
STREAM = '''<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema"><s:Body><trt:GetStreamUriResponse><trt:MediaUri><tt:Uri>{uri}</tt:Uri></trt:MediaUri></trt:GetStreamUriResponse></s:Body></s:Envelope>'''

class Server:
    def __init__(self, handler):
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    def __enter__(self): self.thread.start(); return self.server
    def __exit__(self, *_): self.server.shutdown(); self.server.server_close()

class AuthFaultHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send_response(200); self.send_header("Content-Type", "application/soap+xml"); self.end_headers(); self.wfile.write(AUTH_FAULT.encode())
    def log_message(self, *_): pass

class SuccessHandler(BaseHTTPRequestHandler):
    saw_wsse = []
    def do_POST(self):
        data = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
        type(self).saw_wsse.append("UsernameToken" in data and "PasswordDigest" in data)
        if "GetDeviceInformation" in data: body = DEVICE
        elif "GetCapabilities" in data: body = CAPS.format(port=self.server.server_port)
        elif "GetProfiles" in data: body = PROFILES
        elif "GetStreamUri" in data:
            token = "sub" if ">sub<" in data else "main"; body = STREAM.format(uri=f"rtsp://camera:554/{token}")
        else: self.send_response(500); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Type", "application/soap+xml"); self.end_headers(); self.wfile.write(body.encode())
    def log_message(self, *_): pass

class OnvifTests(unittest.TestCase):
    def test_reolink_auth_fault_in_http_200_is_auth_failed(self):
        with Server(AuthFaultHandler) as server: result = probe_onvif("127.0.0.1", server.server_port)
        self.assertEqual(result["status"], "auth_failed")
    def test_wsse_and_device_profile_metadata(self):
        SuccessHandler.saw_wsse = []
        with Server(SuccessHandler) as server: result = probe_onvif("127.0.0.1", server.server_port, "admin", "secret")
        self.assertEqual(result["status"], "success")
        data = result["result"]
        self.assertEqual(data["manufacturer"], "REOLINK")
        self.assertEqual(data["model"], "RLC-TEST")
        self.assertEqual(data["firmwareVersion"], "v1.2.3")
        self.assertEqual(data["serialNumber"], "ABC123")
        self.assertEqual(data["hardwareId"], "HW1")
        self.assertEqual(data["profiles"][0]["resolution"], {"width": 2560, "height": 1440})
        self.assertTrue(all(SuccessHandler.saw_wsse))

if __name__ == "__main__": unittest.main()
