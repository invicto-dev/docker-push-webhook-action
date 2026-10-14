import http.server
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webhook.py")


class _Handler(http.server.BaseHTTPRequestHandler):
    # The URL path selects the response status: POST /500 -> HTTP 500.
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.received.append((self.headers, body))
        self.server.paths.append(self.path)
        status = int(self.path.split("?")[0].strip("/"))
        self.send_response(status)
        if 300 <= status < 400:
            self.send_header("Location", "/200")
        self.end_headers()

    def log_message(self, *args):
        pass


def make_commit(repo, message, author):
    """Creates a git repo with one empty commit; returns its SHA."""
    git = ["git", "-c", f"user.name={author}", "-c", "user.email=a@a", "-c", "commit.gpgsign=false"]
    subprocess.run(git + ["init", "-q"], cwd=repo, check=True)
    subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", message], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


class WebhookTest(unittest.TestCase):
    def setUp(self):
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        self.server.received = []
        self.server.paths = []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def url(self, status=200):
        return f"http://127.0.0.1:{self.server.server_port}/{status}"

    def run_webhook(self, url=None, headers="{}", payload=b"", fail_on_error="true", env=None, cwd=None):
        full_env = {
            "PATH": os.environ.get("PATH", ""),
            "WEBHOOK_URL": url or self.url(),
            "WEBHOOK_HEADERS": headers,
            "WEBHOOK_PAYLOAD": payload,
            "WEBHOOK_FAIL_ON_ERROR": fail_on_error,
        }
        full_env.update(env or {})
        return subprocess.run(
            [sys.executable, "-I", SCRIPT],
            env=full_env, capture_output=True, text=True, timeout=60, cwd=cwd or self.tmp.name,
        )

    def test_success_exits_zero(self):
        result = self.run_webhook()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Webhook 1/1: HTTP 200", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(self.server.received), 1)

    def test_error_status_fails(self):
        result = self.run_webhook(url=self.url(500))
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::Webhook 1/1 falhou: HTTP 500", result.stdout)

    def test_error_status_only_warns_when_fail_on_error_false(self):
        result = self.run_webhook(url=self.url(500), fail_on_error="false")
        self.assertEqual(result.returncode, 0)
        self.assertIn("::warning::Webhook 1/1 falhou: HTTP 500", result.stdout)

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

    def test_empty_payload_sends_the_default_json(self):
        self.run_webhook(payload=b"")
        headers, body = self.server.received[0]
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(
            sorted(json.loads(body)),
            ["author", "commit", "commit_message", "digest", "image", "repository", "run_url", "tags", "version"],
        )

    RUN_ENV = {
        "WEBHOOK_IMAGE": "registry.example.com/app",
        "WEBHOOK_VERSION": "2026.10.14-1532Z-abc1234",
        "WEBHOOK_DIGEST": "sha256:" + "a" * 64,
        "GITHUB_REPOSITORY": "acme/app",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_RUN_ID": "42",
    }

    def test_every_url_gets_the_same_body_and_blank_lines_are_ignored(self):
        urls = f"\n{self.url(200)} \r\n\n  {self.url(201)}\n"
        result = self.run_webhook(url=urls, payload=b'{"a": 1}')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([p for p in self.server.paths], ["/200", "/201"])
        self.assertEqual([body for _, body in self.server.received], [b'{"a": 1}', b'{"a": 1}'])
        self.assertIn("Webhook 1/2: HTTP 200", result.stdout)
        self.assertIn("Webhook 2/2: HTTP 201", result.stdout)

    def test_only_blank_lines_calls_nothing(self):
        result = self.run_webhook(url="\n  \n\r\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Nenhum webhook configurado", result.stdout)
        self.assertEqual(self.server.received, [])

    def test_a_failing_destination_does_not_stop_the_others(self):
        urls = f"{self.url(500)}\n{self.url(200)}"
        result = self.run_webhook(url=urls)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.server.paths, ["/500", "/200"])
        self.assertIn("::error::Webhook 1/2 falhou: HTTP 500", result.stdout)
        self.assertIn("Webhook 2/2: HTTP 200", result.stdout)
        result = self.run_webhook(url=urls, fail_on_error="false")
        self.assertEqual(result.returncode, 0)
        self.assertIn("::warning::Webhook 1/2 falhou: HTTP 500", result.stdout)

    def test_one_invalid_url_sends_nothing(self):
        result = self.run_webhook(url=f"{self.url(200)}\nftp://127.0.0.1/200", fail_on_error="false")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.server.received, [])

    def test_default_payload_describes_the_push(self):
        sha = make_commit(self.tmp.name, "feat: ação 🚀\n\ncorpo do commit", "Ana Souza")
        version = self.RUN_ENV["WEBHOOK_VERSION"]
        env = dict(self.RUN_ENV, GITHUB_SHA=sha, WEBHOOK_REFS=(
            f"registry.example.com/app:latest\nregistry.example.com/app:{sha}\nregistry.example.com/app:{version}"
        ))
        result = self.run_webhook(env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        headers, body = self.server.received[0]
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(body.decode("utf-8")), {
            "repository": "acme/app",
            "image": "registry.example.com/app",
            "version": version,
            "tags": ["latest", sha, version],
            "digest": "sha256:" + "a" * 64,
            "commit": sha,
            "commit_message": "feat: ação 🚀",
            "author": "Ana Souza",
            "run_url": "https://github.com/acme/app/actions/runs/42",
        })

    def test_default_payload_without_git_history(self):
        env = dict(self.RUN_ENV, GITHUB_SHA="0" * 40, WEBHOOK_REFS="registry.example.com/app:latest")
        result = self.run_webhook(env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        body = json.loads(self.server.received[0][1])
        self.assertEqual((body["commit_message"], body["author"]), ("", ""))
        self.assertEqual(body["tags"], ["latest"])

    def test_placeholders_in_url_are_filled_and_encoded(self):
        url = self.url(200) + "?tag={version}&d={digest}&x={foo}"
        result = self.run_webhook(url=url, env={"WEBHOOK_VERSION": "2026.10.14-1532Z-abc1234", "WEBHOOK_DIGEST": "sha256:ab"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.server.paths[0], "/200?tag=2026.10.14-1532Z-abc1234&d=sha256%3Aab&x={foo}")

    def test_placeholders_in_custom_payload(self):
        payload = b'{"v": "{version}", "i": "{image}", "s": "{sha}", "e": "{digest}", "x": "{foo}"}'
        env = {"WEBHOOK_VERSION": "v9", "WEBHOOK_IMAGE": "r/app", "GITHUB_SHA": "abc", "WEBHOOK_DIGEST": ""}
        self.run_webhook(payload=payload, env=env)
        self.assertEqual(self.server.received[0][1], b'{"v": "v9", "i": "r/app", "s": "abc", "e": "", "x": "{foo}"}')

    def test_non_ascii_url_among_valid_ones_sends_nothing(self):
        port = self.server.server_port
        urls = f"{self.url(200)}\nhttp://127.0.0.1:{port}/a\u00e7\u00e3o\n{self.url(200)}"
        result = self.run_webhook(url=urls, fail_on_error="false")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("::error::webhook_url", result.stdout)
        self.assertEqual(self.server.received, [])

    def test_url_failing_only_at_send_time_does_not_stop_the_others(self):
        # "a..b" passes validation but the request itself raises (empty host label)
        result = self.run_webhook(url=f"http://a..b/\n{self.url(200)}", fail_on_error="false")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.server.paths, ["/200"])
        self.assertIn("::warning::Webhook 1/2 falhou:", result.stdout)
        self.assertNotIn("webhook_headers", result.stdout)

    def test_commit_message_that_is_not_utf8_still_notifies(self):
        # git converts a Latin-1 message to UTF-8 when committing, so it arrives intact
        sha = make_commit(self.tmp.name, b"feat: a\xe7\xe3o", "Ana Souza")
        result = self.run_webhook(env=dict(self.RUN_ENV, GITHUB_SHA=sha, WEBHOOK_REFS="r/app:latest"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(self.server.received[0][1])["commit_message"], "feat: ação")

    def test_urls_with_placeholders_are_never_logged(self):
        urls = f"{self.url(500)}?token=topsecret\n{self.url(200)}?tag={{version}}&token=topsecret"
        result = self.run_webhook(url=urls, env={"WEBHOOK_VERSION": "v1"})
        self.assertNotIn("topsecret", result.stdout + result.stderr)
        self.assertNotIn("127.0.0.1", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
