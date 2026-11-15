import base64
import hashlib
import hmac
import unittest
from unittest.mock import Mock, patch

import requests
from nankan_http import NankanSession


class NankanHttpTests(unittest.TestCase):
    def test_gateway_sends_signature_instead_of_key_and_preserves_source_encoding(self):
        gateway_response = Mock()
        gateway_response.json.return_value = {
            "status": 200, "body": base64.b64encode("大井競馬".encode("cp932")).decode(),
            "contentType": "text/html; charset=Shift_JIS",
        }
        with patch.dict("os.environ", {"NANKAN_FETCH_GATEWAY": "https://example.com/api/source", "SUPABASE_KEY": "private-test-key"}), \
             patch("nankan_http.time.time", return_value=1794730000), \
             patch.object(requests.Session, "request", return_value=gateway_response) as upstream:
            response = NankanSession().get("https://www.nankankeiba.com/syousai/2026111620120101.do?type=uma_shosai")
            self.assertEqual(response.text, "大井競馬")
            args, kwargs = upstream.call_args
            self.assertEqual(args, ("POST", "https://example.com/api/source"))
            self.assertNotIn("private-test-key", str(kwargs))
            path = kwargs["json"]["path"]
            expected = hmac.new(b"private-test-key", f"nankan-fetch-v1\n1794730000\n{path}".encode(), hashlib.sha256).hexdigest()
            self.assertEqual(kwargs["headers"]["x-nankan-signature"], expected)

    def test_other_hosts_are_never_forwarded(self):
        with patch.dict("os.environ", {"NANKAN_FETCH_GATEWAY": "https://example.com/api/source"}), \
             patch.object(requests.Session, "request") as upstream:
            NankanSession().get("https://s.keibabook.co.jp/login/login")
            self.assertEqual(upstream.call_args.args, ("GET", "https://s.keibabook.co.jp/login/login"))

    def test_local_session_uses_original_url(self):
        with patch.dict("os.environ", {"NANKAN_FETCH_GATEWAY": ""}), \
             patch.object(requests.Session, "request") as upstream:
            NankanSession().get("https://www.nankankeiba.com/calendar/202610.do")
            self.assertEqual(upstream.call_args.args[0], "GET")

    def test_gateway_auth_failure_does_not_become_success(self):
        gateway_response = Mock()
        gateway_response.raise_for_status.side_effect = requests.HTTPError("401")
        with patch.dict("os.environ", {"NANKAN_FETCH_GATEWAY": "https://example.com/api/source", "SUPABASE_KEY": "private-test-key"}), \
             patch.object(requests.Session, "request", return_value=gateway_response):
            with self.assertRaises(requests.HTTPError):
                NankanSession().get("https://www.nankankeiba.com/calendar/202610.do")


if __name__ == "__main__":
    unittest.main()
