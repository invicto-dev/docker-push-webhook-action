import http.server
import os
import socket
import subprocess
import sys
import threading
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webhook.py")


class _Handler(http.server.BaseHTTPRequestHandler):
    # The URL path selects the response status: POST /500 -> HTTP 500.
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.received.append((self.headers, body))
        status = int(self.path.split("?")[0].strip("/"))
        self.send_response(status)
        if 300 <= status < 400:
            self.send_header("Location", "/200")
        self.end_headers()

    def log_message(self, *args):
        pass


class WebhookTest(unittest.TestCase):
    def setUp(self):
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        self.server.received = []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def url(self, status=200):
        return f"http://127.0.0.1:{self.server.server_port}/{status}"

    def run_webhook(self, url=None, headers="{}", payload=b"", fail_on_error="true"):
        env = {
            "PATH": os.environ.get("PATH", ""),
            "WEBHOOK_URL": url or self.url(),
            "WEBHOOK_HEADERS": headers,
            "WEBHOOK_PAYLOAD": payload,
            "WEBHOOK_FAIL_ON_ERROR": fail_on_error,
        }
        return subprocess.run(
            [sys.executable, "-I", SCRIPT],
            env=env, capture_output=True, text=True, timeout=60,
        )

    def test_success_exits_zero(self):
        result = self.run_webhook()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Webhook responded HTTP 200", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(self.server.received), 1)

    def test_error_status_fails(self):
        result = self.run_webhook(url=self.url(500))
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::Webhook responded HTTP 500", result.stdout)

    def test_error_status_only_warns_when_fail_on_error_false(self):
        result = self.run_webhook(url=self.url(500), fail_on_error="false")
        self.assertEqual(result.returncode, 0)
        self.assertIn("::warning::Webhook responded HTTP 500", result.stdout)

    def test_redirect_is_not_followed(self):
        result = self.run_webhook(url=self.url(302), headers='{"Authorization": "Bearer s3cr3t"}')
        self.assertEqual(result.returncode, 1)
        self.assertIn("HTTP 302", result.stdout)
        self.assertEqual(len(self.server.received), 1)

    def test_connection_error_respects_fail_on_error(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}/200"
        self.assertEqual(self.run_webhook(url=url).returncode, 1)
        result = self.run_webhook(url=url, fail_on_error="false")
        self.assertEqual(result.returncode, 0)
        self.assertIn("::warning::", result.stdout)

    def test_invalid_headers_always_fail(self):
        cases = ["{", "[]", '{"X-Num": 1}', '{"X-Bad": "a\\nb"}', '{"": "a"}', '{"Bad Name": "a"}',
                 '{"X-Emoji": "\U0001F680"}']
        for raw in cases:
            with self.subTest(raw=raw):
                result = self.run_webhook(headers=raw, fail_on_error="false")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("::error::", result.stdout)
        self.assertEqual(self.server.received, [])

    def test_invalid_url_always_fails(self):
        for url in ["ftp://127.0.0.1/200", "http://", "not a url", "http://[",
                    "https://jenkins:pw@127.0.0.1/200"]:
            with self.subTest(url=url):
                result = self.run_webhook(url=url, fail_on_error="false")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_url_is_never_logged(self):
        for url in [self.url(200) + "?token=topsecret", self.url(500) + "?token=topsecret",
                    "ftp://user:topsecret@127.0.0.1/",
                    f"http://jenkins:topsecret@127.0.0.1:{self.server.server_port}/200",
                    f"http://127.0.0.1:{self.server.server_port}/a b?token=topsecret"]:
            with self.subTest(url=url):
                result = self.run_webhook(url=url)
                self.assertNotIn("topsecret", result.stdout + result.stderr)

    def test_header_values_are_masked_before_anything_else(self):
        result = self.run_webhook(headers='{"Authorization": "Bearer s3cr3t"}')
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "::add-mask::Bearer s3cr3t")
        self.assertEqual([line for line in lines[1:] if "s3cr3t" in line], [])
        self.assertEqual(self.server.received[0][0]["Authorization"], "Bearer s3cr3t")

    def test_multiline_header_value_is_masked_line_by_line(self):
        result = self.run_webhook(headers='{"X-Secret": "part1\\npart2"}')
        self.assertEqual(result.returncode, 1)
        self.assertIn("::add-mask::part1", result.stdout)
        self.assertIn("::add-mask::part2", result.stdout)
        for line in result.stdout.splitlines():
            if "part1" in line or "part2" in line:
                self.assertTrue(line.startswith("::add-mask::"), line)

    def test_default_content_type_is_json(self):
        self.run_webhook(payload=b'{"a": 1}')
        self.assertEqual(self.server.received[0][0]["Content-Type"], "application/json")

    def test_custom_content_type_is_kept_once(self):
        self.run_webhook(headers='{"content-type": "text/plain"}', payload=b"hi")
        self.assertEqual(self.server.received[0][0].get_all("Content-Type"), ["text/plain"])

    def test_payload_arrives_byte_for_byte(self):
        payload = '{"q": "aspas \\" e \'simples\'", "nl": "a\nb", "sh": "$(whoami) `id`", "u": "ação 🚀"}'.encode()
        result = self.run_webhook(payload=payload)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.server.received[0][1], payload)

    def test_empty_payload_posts_without_body(self):
        self.run_webhook(payload=b"")
        headers, body = self.server.received[0]
        self.assertEqual(body, b"")
        self.assertIsNone(headers.get("Content-Type"))


if __name__ == "__main__":
    unittest.main()
