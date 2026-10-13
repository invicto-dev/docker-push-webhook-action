#!/usr/bin/env python3
"""POST WEBHOOK_PAYLOAD to WEBHOOK_URL with WEBHOOK_HEADERS.

All input comes from environment variables, never from argv or string
interpolation, so payloads and headers may contain any character.
"""
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_SECONDS = 30
# RFC 9110 token; http.client alone accepts names like "Bad Name".
HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


def fail(message):
    print(f"::error::{message}")
    sys.exit(1)


def delivery_failed(message, fail_on_error):
    if fail_on_error:
        fail(message)
    print(f"::warning::{message}")
    sys.exit(0)


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
    except ValueError:
        valid = False
    if not valid:
        fail("webhook_url must be an http(s) URL with a host")


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


def main():
    url = os.environ.get("WEBHOOK_URL", "")
    fail_on_error = os.environ.get("WEBHOOK_FAIL_ON_ERROR", "true").strip().lower() != "false"
    headers = parse_headers(os.environ.get("WEBHOOK_HEADERS", ""))
    validate_url(url)
    payload = os.environb.get(b"WEBHOOK_PAYLOAD", b"")
    try:
        status = send(url, headers, payload)
    except ValueError:
        fail("webhook_headers contains an invalid header name or value")
    except urllib.error.HTTPError as error:
        delivery_failed(f"Webhook responded HTTP {error.code}", fail_on_error)
    except (OSError, http.client.HTTPException) as error:
        delivery_failed(f"Webhook request failed: {getattr(error, 'reason', error)}", fail_on_error)
    print(f"Webhook responded HTTP {status}")


if __name__ == "__main__":
    main()
