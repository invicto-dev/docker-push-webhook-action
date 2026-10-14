#!/usr/bin/env python3
"""POST a payload to every URL in WEBHOOK_URL (one per line) with WEBHOOK_HEADERS.

All input comes from environment variables, never from argv or string
interpolation, so payloads and headers may contain any character. When
WEBHOOK_PAYLOAD is empty, a JSON body describing the pushed image is sent.
"""
import http.client
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_SECONDS = 30
# RFC 9110 token; http.client alone accepts names like "Bad Name".
HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
PLACEHOLDER = re.compile(r"\{(version|digest|image|sha)\}")
PLACEHOLDER_BYTES = re.compile(rb"\{(version|digest|image|sha)\}")


def fail(message):
    print(f"::error::{message}")
    sys.exit(1)


def mask(value):
    # A workflow command ends at the line break, so mask each line on its own.
    for line in value.splitlines():
        if line.strip():
            print(f"::add-mask::{line}", flush=True)


def parse_headers(raw):
    try:
        headers = json.loads(raw or "{}")
    except ValueError:
        fail("webhook_headers is not valid JSON")
    if not isinstance(headers, dict):
        fail("webhook_headers must be a JSON object")
    for value in headers.values():
        if isinstance(value, str):
            mask(value)
    for key, value in headers.items():
        if not HEADER_NAME.fullmatch(key) or not isinstance(value, str):
            fail("webhook_headers: keys must be valid header names and values must be strings")
        if any(ch in key + value for ch in "\r\n"):
            fail("webhook_headers: keys and values must not contain line breaks")
    return headers


def validate_url(url):
    try:
        parts = urllib.parse.urlsplit(url)
        valid = parts.scheme in ("http", "https") and bool(parts.hostname)
        has_credentials = bool(parts.username or parts.password)
    except ValueError:
        valid, has_credentials = False, False
    if not valid:
        fail("webhook_url must be an http(s) URL with a host")
    if has_credentials:
        fail("webhook_url must not contain credentials; send them in webhook_headers")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # Following a redirect would resend our headers (often credentials) elsewhere.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send(url, headers, payload):
    request = urllib.request.Request(url, data=payload or None, method="POST")
    for key, value in headers.items():
        request.add_header(key, value)
    if payload and not request.has_header("Content-type"):
        request.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        return response.status


def git_log(fmt, sha):
    # Empty when there is no git history for this commit (no checkout, unknown SHA).
    try:
        result = subprocess.run(
            ["git", "log", "-1", f"--format={fmt}", sha],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def run_values():
    """Values known only at run time, from the prepare and push steps."""
    refs = [ref.strip() for ref in os.environ.get("WEBHOOK_REFS", "").splitlines() if ref.strip()]
    return {
        "image": os.environ.get("WEBHOOK_IMAGE", ""),
        "version": os.environ.get("WEBHOOK_VERSION", ""),
        "digest": os.environ.get("WEBHOOK_DIGEST", ""),
        "sha": os.environ.get("GITHUB_SHA", ""),
        "tags": [ref.rsplit(":", 1)[1] for ref in refs],
    }


def default_payload(values):
    sha = values["sha"]
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    body = {
        "repository": repo,
        "image": values["image"],
        "version": values["version"],
        "tags": values["tags"],
        "digest": values["digest"],
        "commit": sha,
        "commit_message": git_log("%s", sha) if sha else "",
        "author": git_log("%an", sha) if sha else "",
        "run_url": f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else "",
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def fill_url(url, values):
    return PLACEHOLDER.sub(lambda m: urllib.parse.quote(values[m.group(1)], safe=""), url)


def fill_payload(payload, values):
    return PLACEHOLDER_BYTES.sub(lambda m: values[m.group(1).decode()].encode("utf-8"), payload)


def main():
    fail_on_error = os.environ.get("WEBHOOK_FAIL_ON_ERROR", "true").strip().lower() != "false"
    headers = parse_headers(os.environ.get("WEBHOOK_HEADERS", ""))
    values = run_values()
    lines = [line.strip() for line in os.environ.get("WEBHOOK_URL", "").splitlines()]
    urls = [fill_url(line, values) for line in lines if line]
    if not urls:
        print("Nenhum webhook configurado; nada a avisar.")
        return
    for url in urls:
        validate_url(url)
    payload = os.environb.get(b"WEBHOOK_PAYLOAD", b"")
    payload = fill_payload(payload, values) if payload else default_payload(values)

    failures = []
    for index, url in enumerate(urls, 1):
        label = f"Webhook {index}/{len(urls)}"
        try:
            status = send(url, headers, payload)
        except ValueError:
            fail("webhook_headers contains an invalid header name or value")
        except urllib.error.HTTPError as error:
            failures.append(f"{label} falhou: HTTP {error.code}")
            continue
        except (OSError, http.client.HTTPException) as error:
            # Only the type: exception text can echo parts of the URL or the response.
            reason = getattr(error, "reason", error)
            failures.append(f"{label} falhou: {type(reason).__name__}")
            continue
        print(f"{label}: HTTP {status}")

    level = "error" if fail_on_error else "warning"
    for message in failures:
        print(f"::{level}::{message}")
    if failures and fail_on_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
