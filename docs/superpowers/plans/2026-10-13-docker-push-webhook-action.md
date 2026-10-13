# Docker Push & Webhook — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Composite action pública que faz login, build e push de uma imagem Docker com o CLI nativo e, opcionalmente, chama um webhook com headers e payload arbitrários.

**Architecture:** `action.yml` (composite, passos Bash curtos, todos os inputs via `env:`) orquestra prepare → login → build → push → webhook → cleanup. O webhook é um script Python stdlib (`webhook.py`) que lê tudo de variáveis de ambiente. Credenciais ficam num `DOCKER_CONFIG` temporário, apagado no cleanup.

**Tech Stack:** GitHub Actions (composite), Bash 5, Docker CLI, Python 3 stdlib (`urllib`, `unittest`).

**Spec:** `docs/superpowers/specs/2026-10-13-docker-push-webhook-action-design.md`

## Global Constraints

- Runner alvo: Linux com `docker` e `python3` (GitHub-hosted `ubuntu-*`, Bash 5).
- `webhook.py` e `test_webhook.py`: **somente stdlib**, compatíveis com **Python 3.9+** (o Python local é 3.9.6).
- `action.yml` não usa nenhuma action de terceiros.
- Nenhum `${{ inputs.* }}` dentro de blocos `run:` do `action.yml`; todo input entra via `env:` e é usado entre aspas.
- Todo `run:` do `action.yml` começa com `set -euo pipefail` (exceto cleanup: `set -uo pipefail`, que não pode abortar antes do `rm`).
- Webhook: sempre `POST`, timeout `30` segundos, sucesso = status 2xx.
- Webhook nunca loga URL, headers, payload ou corpo da resposta; loga só status/tipo de erro.
- Erro de configuração do webhook (headers/URL inválidos) sempre falha (exit 1), independente de `webhook_fail_on_error`.
- Textos voltados ao usuário da action (descrições de inputs, mensagens de log, README) em **inglês**, já que o público é o Marketplace global.
- Arquivos permitidos: `action.yml`, `webhook.py`, `test_webhook.py`, `test/Dockerfile`, `.github/workflows/test.yml`, `README.md`, `LICENSE`.
- Commits terminam com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Header com quebra de linha:** `::add-mask::` termina na quebra de linha, então mascarar o valor inteiro vazaria a 2ª linha no log. Esperado: mascarar linha a linha (teste na Task 1).
2. **Redirect do webhook:** o `urllib` segue 301/302 reenviando `Authorization` para outro host. Esperado: não seguir e tratar 3xx como falha (teste na Task 1).
3. **URL com token** (`?token=...`, padrão de Coolify/Discord): esperado que nunca apareça no log, nem em sucesso nem em erro (teste na Task 1).
4. **Credenciais vazando para o Docker do caller:** depois da action, o `~/.docker` do runner não pode estar logado no registry (teste e2e na Task 2).
5. **Webhook disparado quando o build/push falhou:** esperado que não dispare (teste e2e na Task 3).

---

### Task 1: `webhook.py` com testes unitários

**Files:**
- Create: `webhook.py`
- Test: `test_webhook.py`

**Interfaces:**
- Consumes: nada.
- Produces: `python3 -I webhook.py`, que lê as variáveis `WEBHOOK_URL`, `WEBHOOK_HEADERS` (JSON, vazio = `{}`), `WEBHOOK_PAYLOAD` (bytes crus, vazio = POST sem corpo) e `WEBHOOK_FAIL_ON_ERROR` (`"false"` desliga; qualquer outro valor = ligado). Exit 0 em sucesso ou em falha de entrega com `fail_on_error` desligado; exit 1 caso contrário. A Task 3 chama exatamente assim.

- [ ] **Step 1: Escrever os testes que devem falhar**

`test_webhook.py`:

```python
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
        for url in ["ftp://127.0.0.1/200", "http://", "not a url", "http://["]:
            with self.subTest(url=url):
                result = self.run_webhook(url=url, fail_on_error="false")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_url_is_never_logged(self):
        for url in [self.url(200) + "?token=topsecret", self.url(500) + "?token=topsecret",
                    "ftp://user:topsecret@127.0.0.1/"]:
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
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python3 -m unittest -v test_webhook`
Expected: FAIL em todos os testes (o subprocess retorna exit 2, `can't open file ... webhook.py`).

- [ ] **Step 3: Implementar `webhook.py`**

```python
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
```

Notas para o implementador:
- `request.add_header` aplica `capitalize()` à chave (`content-type` → `Content-type`), por isso `has_header("Content-type")` detecta qualquer caixa.
- Nome de header é validado por `HEADER_NAME` porque o `http.client` aceita espaços no nome. Valor fora do latin-1 (ex.: emoji) faz o `http.client` levantar `UnicodeEncodeError` (subclasse de `ValueError`) ao enviar, e cai no `except ValueError` (erro de configuração, sempre exit 1).
- `os.environb` garante que o payload chega byte a byte como foi definido.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python3 -m unittest -v test_webhook`
Expected: `OK`, 14 testes. Se `test_url_is_never_logged` falhar no caso `ftp://`, revisar se alguma mensagem inclui a URL.

- [ ] **Step 5: Commit**

```bash
git add webhook.py test_webhook.py
git commit -m "feat: add webhook sender with env-only input and masking

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `action.yml` (prepare, login, build, push, cleanup) + e2e

**Files:**
- Create: `action.yml`
- Create: `test/Dockerfile`
- Create: `.github/workflows/test.yml`

**Interfaces:**
- Consumes: nada da Task 1 (o webhook é ligado na Task 3).
- Produces: step ids `prepare` (outputs `dockerfile`, `docker_config`, `refs`) e `push` (outputs `image`, `tags`, `digest`); outputs da action `image`, `tags`, `digest`. A Task 3 insere o passo do webhook **entre** `push` e `cleanup`.

- [ ] **Step 1: Escrever o e2e que deve falhar**

`test/Dockerfile`:

```dockerfile
FROM busybox:1.36
ARG GREETING
RUN test -n "$GREETING"
LABEL greeting="$GREETING"
```

`.github/workflows/test.yml`:

```yaml
name: test

on:
  push:
  pull_request:

permissions:
  contents: read

jobs:
  unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: python3 -m unittest -v test_webhook

  e2e:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Start registry with auth
        run: |
          set -euo pipefail
          mkdir -p "$RUNNER_TEMP/auth"
          docker run --rm --entrypoint htpasswd httpd:2 -Bbn test test-pass > "$RUNNER_TEMP/auth/htpasswd"
          docker run -d -p 5000:5000 \
            -v "$RUNNER_TEMP/auth:/auth" \
            -e REGISTRY_AUTH=htpasswd \
            -e REGISTRY_AUTH_HTPASSWD_REALM=e2e \
            -e REGISTRY_AUTH_HTPASSWD_PATH=/auth/htpasswd \
            registry:2
          for _ in $(seq 30); do curl -s -o /dev/null localhost:5000/v2/ && break; sleep 1; done

      - id: action
        uses: ./
        with:
          registry_url: localhost:5000
          registry_username: test
          registry_password: test-pass
          image: localhost:5000/e2e
          tags: |
            v1, ${{ github.sha }}
          dockerfile_path: test
          build_args: GREETING=olá mundo

      - name: Assert push
        env:
          IMAGE: ${{ steps.action.outputs.image }}
          TAGS: ${{ steps.action.outputs.tags }}
          DIGEST: ${{ steps.action.outputs.digest }}
          SHA: ${{ github.sha }}
        run: |
          set -euo pipefail
          [[ "$IMAGE" == "localhost:5000/e2e:v1" ]]
          [[ "$TAGS" == "localhost:5000/e2e:v1"$'\n'"localhost:5000/e2e:$SHA" ]]
          [[ "$DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]
          docker rmi localhost:5000/e2e:v1 "localhost:5000/e2e:$SHA"
          if docker pull localhost:5000/e2e:v1; then
            echo "::error::credentials leaked into the default Docker config"
            exit 1
          fi
          echo test-pass | docker login localhost:5000 -u test --password-stdin
          docker pull localhost:5000/e2e:v1
          docker pull "localhost:5000/e2e:$SHA"
          docker inspect --format '{{json .RepoDigests}}' localhost:5000/e2e:v1 | grep -q "$DIGEST"
          [[ "$(docker inspect --format '{{index .Config.Labels "greeting"}}' localhost:5000/e2e:v1)" == "olá mundo" ]]

      - id: bad_tag
        uses: ./
        continue-on-error: true
        with:
          registry_username: test
          registry_password: test-pass
          image: localhost:5000/e2e
          tags: bad tag!

      - name: Assert invalid tag fails
        env:
          OUTCOME: ${{ steps.bad_tag.outcome }}
        run: '[[ "$OUTCOME" == "failure" ]]'
```

Nota: a spec citava `registry:2` como *service*. Aqui o registry sobe com `docker run` depois do checkout, **com autenticação htpasswd**, para que o e2e prove que o login via stdin funciona de verdade e que as credenciais não vazam para o `~/.docker` padrão (Review Focus 4).

- [ ] **Step 2: Confirmar o vermelho**

Run: `test -f action.yml; echo $?`
Expected: `1`. Atenção: o `actionlint` **não** acusa action local ausente (passa com exit 0), então ele não serve como teste vermelho aqui. O vermelho real do e2e aparece no CI (Step 7).

- [ ] **Step 3: Implementar `action.yml`**

```yaml
name: Docker Push & Webhook
description: Build and push a Docker image with the native Docker CLI, then optionally call a webhook. No third-party actions.
author: Victo Hugo
branding:
  icon: upload-cloud
  color: blue

inputs:
  registry_url:
    description: Registry host (e.g. ghcr.io). Empty means Docker Hub.
    required: false
    default: ''
  registry_username:
    description: Registry username.
    required: true
  registry_password:
    description: Registry password or token. Sent via stdin, never as a command argument.
    required: true
  image:
    description: Full image name without tag (e.g. ghcr.io/org/app).
    required: true
  tags:
    description: Tags separated by commas and/or newlines.
    required: false
    default: latest
  context:
    description: Build context.
    required: false
    default: .
  dockerfile_path:
    description: Path to the Dockerfile, or to a directory containing one. Empty means <context>/Dockerfile.
    required: false
    default: ''
  build_args:
    description: Build args, one KEY=VALUE per line. They are stored in the image history, so never pass secrets.
    required: false
    default: ''
  webhook_url:
    description: URL to POST to after a successful push. Empty skips the webhook.
    required: false
    default: ''
  webhook_headers:
    description: Webhook headers as a JSON object of strings.
    required: false
    default: '{}'
  webhook_payload:
    description: Webhook body, sent exactly as given. Empty sends no body.
    required: false
    default: ''
  webhook_fail_on_error:
    description: Fail the job when the webhook call fails (network error, timeout or non-2xx status).
    required: false
    default: 'true'

outputs:
  image:
    description: First pushed reference (image:tag).
    value: ${{ steps.push.outputs.image }}
  tags:
    description: All pushed references, one per line.
    value: ${{ steps.push.outputs.tags }}
  digest:
    description: Digest of the pushed image (sha256:...).
    value: ${{ steps.push.outputs.digest }}

runs:
  using: composite
  steps:
    - name: Validate inputs
      id: prepare
      shell: bash
      env:
        IMAGE: ${{ inputs.image }}
        TAGS: ${{ inputs.tags }}
        BUILD_CONTEXT: ${{ inputs.context }}
        DOCKERFILE_PATH: ${{ inputs.dockerfile_path }}
        REGISTRY_USERNAME: ${{ inputs.registry_username }}
        REGISTRY_PASSWORD: ${{ inputs.registry_password }}
      run: |
        set -euo pipefail
        for bin in docker python3; do
          if ! command -v "$bin" >/dev/null; then
            echo "::error::'$bin' not found. Use a Linux runner with Docker (e.g. ubuntu-latest)."
            exit 1
          fi
        done
        if [[ -z "$IMAGE" || -z "$REGISTRY_USERNAME" || -z "$REGISTRY_PASSWORD" ]]; then
          echo "::error::Inputs 'image', 'registry_username' and 'registry_password' are required."
          exit 1
        fi
        if [[ "${IMAGE##*/}" == *[:@]* ]]; then
          echo "::error::'image' must not include a tag or digest; use the 'tags' input."
          exit 1
        fi
        dockerfile="${DOCKERFILE_PATH:-$BUILD_CONTEXT}"
        if [[ -d "$dockerfile" ]]; then
          dockerfile="${dockerfile%/}/Dockerfile"
        fi
        if [[ ! -f "$dockerfile" ]]; then
          echo "::error::Dockerfile not found: $dockerfile"
          exit 1
        fi
        refs=()
        while read -r tag; do
          if [[ -z "$tag" ]]; then continue; fi
          if [[ ! "$tag" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
            echo "::error::Invalid tag: '$tag'"
            exit 1
          fi
          refs+=("$IMAGE:$tag")
        done < <(printf '%s\n' "$TAGS" | tr ',' '\n')
        if (( ${#refs[@]} == 0 )); then
          echo "::error::No tags given."
          exit 1
        fi
        docker_config="$(mktemp -d "${RUNNER_TEMP:-/tmp}/docker-config.XXXXXX")"
        {
          echo "dockerfile=$dockerfile"
          echo "docker_config=$docker_config"
          echo "refs<<EOF"
          printf '%s\n' "${refs[@]}"
          echo "EOF"
        } >> "$GITHUB_OUTPUT"

    - name: Log in to registry
      shell: bash
      env:
        DOCKER_CONFIG: ${{ steps.prepare.outputs.docker_config }}
        REGISTRY_URL: ${{ inputs.registry_url }}
        REGISTRY_USERNAME: ${{ inputs.registry_username }}
        REGISTRY_PASSWORD: ${{ inputs.registry_password }}
      run: |
        set -euo pipefail
        while IFS= read -r line; do
          if [[ -n "$line" ]]; then echo "::add-mask::$line"; fi
        done <<< "$REGISTRY_PASSWORD"
        registry=()
        if [[ -n "$REGISTRY_URL" ]]; then registry=("$REGISTRY_URL"); fi
        printf '%s' "$REGISTRY_PASSWORD" | docker login "${registry[@]}" -u "$REGISTRY_USERNAME" --password-stdin

    - name: Build image
      shell: bash
      env:
        DOCKER_CONFIG: ${{ steps.prepare.outputs.docker_config }}
        DOCKERFILE: ${{ steps.prepare.outputs.dockerfile }}
        REFS: ${{ steps.prepare.outputs.refs }}
        BUILD_CONTEXT: ${{ inputs.context }}
        BUILD_ARGS: ${{ inputs.build_args }}
      run: |
        set -euo pipefail
        args=(--file "$DOCKERFILE")
        while read -r ref; do
          args+=(--tag "$ref")
        done <<< "$REFS"
        while IFS= read -r arg; do
          if [[ -z "${arg//[[:space:]]/}" ]]; then continue; fi
          if [[ "$arg" != *=* ]]; then
            echo "::error::build_args: every line must be KEY=VALUE."
            exit 1
          fi
          args+=(--build-arg "$arg")
        done <<< "$BUILD_ARGS"
        docker build "${args[@]}" "$BUILD_CONTEXT"

    - name: Push image
      id: push
      shell: bash
      env:
        DOCKER_CONFIG: ${{ steps.prepare.outputs.docker_config }}
        REFS: ${{ steps.prepare.outputs.refs }}
      run: |
        set -euo pipefail
        digest_re='digest: (sha256:[0-9a-f]{64})'
        digest=''
        while read -r ref; do
          out="$(docker push "$ref" </dev/null | tee /dev/stderr)"
          if [[ "$out" =~ $digest_re ]]; then digest="${BASH_REMATCH[1]}"; fi
        done <<< "$REFS"
        if [[ -z "$digest" ]]; then
          echo "::warning::Could not read the image digest from the 'docker push' output."
        fi
        {
          echo "image=${REFS%%$'\n'*}"
          echo "digest=$digest"
          echo "tags<<EOF"
          echo "$REFS"
          echo "EOF"
        } >> "$GITHUB_OUTPUT"

    - name: Clean up credentials
      if: always() && steps.prepare.outputs.docker_config != ''
      shell: bash
      env:
        DOCKER_CONFIG: ${{ steps.prepare.outputs.docker_config }}
        REGISTRY_URL: ${{ inputs.registry_url }}
      run: |
        set -uo pipefail
        registry=()
        if [[ -n "$REGISTRY_URL" ]]; then registry=("$REGISTRY_URL"); fi
        docker logout "${registry[@]}" || true
        rm -rf "$DOCKER_CONFIG"
```

Notas para o implementador:
- O `DOCKER_CONFIG` vai **só** como `env:` de cada passo, nunca para `GITHUB_ENV`; caso contrário ele vazaria para os passos seguintes do workflow do caller e apontaria para um diretório apagado.
- `if` sem `else` retorna 0, por isso os laços usam `if ...; then ...; fi` em vez de `[[ ]] && ...`. Com `set -e`, um `&&` falso como último comando do laço derrubaria o passo.
- `required: true` não é imposto pelo GitHub em actions, por isso a validação no `prepare`.

- [ ] **Step 4: Rodar lint e shellcheck e confirmar que passa**

Run: `actionlint .github/workflows/test.yml`
Expected: sem saída, exit 0. Dentro de um repositório git, o actionlint valida os `with:` contra os inputs do `action.yml` local (um input com typo gera erro) e roda o shellcheck nos `run:` **do workflow**.

O actionlint não analisa os `run:` do `action.yml`. Para isso:

Run: `ruby -ryaml -e 'YAML.load_file("action.yml")["runs"]["steps"].each { |s| IO.popen(["shellcheck", "-s", "bash", "-"], "w") { |io| io.write(s["run"]) }; exit 1 unless $?.success? }'`
Expected: sem saída, exit 0.

- [ ] **Step 5: Commit**

```bash
git add action.yml test/Dockerfile .github/workflows/test.yml
git commit -m "feat: add composite action for docker login, build and push

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 6: PARAR e pedir confirmação ao usuário para criar o repositório no GitHub**

Esta é uma ação externa e precisa de aprovação explícita. Perguntar o owner (usuário ou org) e confirmar que o repo será criado **privado**. Só depois:

```bash
gh repo create <owner>/docker-push-webhook-action --private --source . --push
```

- [ ] **Step 7: Verificar o CI**

Run: `gh run watch --exit-status "$(gh run list --limit 1 --json databaseId -q '.[0].databaseId')"`
Expected: jobs `unit` e `e2e` verdes. Se o digest vier vazio (warning no passo `Push image`), conferir o formato da saída do `docker push` no log e ajustar `digest_re`.

---

### Task 3: Ligar o webhook na action + e2e do webhook

**Files:**
- Modify: `action.yml` (inserir um passo entre `Push image` e `Clean up credentials`)
- Modify: `.github/workflows/test.yml` (job `e2e`)

**Interfaces:**
- Consumes: `webhook.py` da Task 1 (interface via env descrita lá); step id `push` e o passo `Clean up credentials` da Task 2.
- Produces: comportamento final da action conforme a spec.

- [ ] **Step 1: Escrever o e2e que deve falhar**

Substituir o job `e2e` inteiro em `.github/workflows/test.yml` por:

```yaml
  e2e:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Start registry with auth
        run: |
          set -euo pipefail
          mkdir -p "$RUNNER_TEMP/auth"
          docker run --rm --entrypoint htpasswd httpd:2 -Bbn test test-pass > "$RUNNER_TEMP/auth/htpasswd"
          docker run -d -p 5000:5000 \
            -v "$RUNNER_TEMP/auth:/auth" \
            -e REGISTRY_AUTH=htpasswd \
            -e REGISTRY_AUTH_HTPASSWD_REALM=e2e \
            -e REGISTRY_AUTH_HTPASSWD_PATH=/auth/htpasswd \
            registry:2
          for _ in $(seq 30); do curl -s -o /dev/null localhost:5000/v2/ && break; sleep 1; done

      - name: Start webhook listener
        run: |
          cat > "$RUNNER_TEMP/listener.py" <<'PY'
          import http.server, json, sys

          class Handler(http.server.BaseHTTPRequestHandler):
              def do_POST(self):
                  body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                  record = {"headers": {k.lower(): v for k, v in self.headers.items()}, "body": body.decode()}
                  with open(sys.argv[1], "w") as f:
                      json.dump(record, f)
                  self.send_response(204)
                  self.end_headers()

          http.server.HTTPServer(("127.0.0.1", 8080), Handler).serve_forever()
          PY
          nohup python3 "$RUNNER_TEMP/listener.py" "$RUNNER_TEMP/webhook.json" >/dev/null 2>&1 &

      - id: action
        uses: ./
        with:
          registry_url: localhost:5000
          registry_username: test
          registry_password: test-pass
          image: localhost:5000/e2e
          tags: |
            v1, ${{ github.sha }}
          dockerfile_path: test
          build_args: GREETING=olá mundo
          webhook_url: http://127.0.0.1:8080/deploy
          webhook_headers: '{"X-Test-Token": "abc123"}'
          webhook_payload: '{"msg": "aspas \" e ''simples'' $(whoami)"}'

      - name: Assert push
        env:
          IMAGE: ${{ steps.action.outputs.image }}
          TAGS: ${{ steps.action.outputs.tags }}
          DIGEST: ${{ steps.action.outputs.digest }}
          SHA: ${{ github.sha }}
        run: |
          set -euo pipefail
          [[ "$IMAGE" == "localhost:5000/e2e:v1" ]]
          [[ "$TAGS" == "localhost:5000/e2e:v1"$'\n'"localhost:5000/e2e:$SHA" ]]
          [[ "$DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]
          docker rmi localhost:5000/e2e:v1 "localhost:5000/e2e:$SHA"
          if docker pull localhost:5000/e2e:v1; then
            echo "::error::credentials leaked into the default Docker config"
            exit 1
          fi
          echo test-pass | docker login localhost:5000 -u test --password-stdin
          docker pull localhost:5000/e2e:v1
          docker pull "localhost:5000/e2e:$SHA"
          docker inspect --format '{{json .RepoDigests}}' localhost:5000/e2e:v1 | grep -q "$DIGEST"
          [[ "$(docker inspect --format '{{index .Config.Labels "greeting"}}' localhost:5000/e2e:v1)" == "olá mundo" ]]

      - name: Assert webhook
        run: |
          python3 - "$RUNNER_TEMP/webhook.json" <<'PY'
          import json, sys
          got = json.load(open(sys.argv[1]))
          assert got["headers"]["x-test-token"] == "abc123", "missing X-Test-Token header"
          assert got["headers"]["content-type"] == "application/json", got["headers"]
          assert got["body"] == '{"msg": "aspas \\" e \'simples\' $(whoami)"}', got["body"]
          PY
          rm "$RUNNER_TEMP/webhook.json"

      - id: bad_tag
        uses: ./
        continue-on-error: true
        with:
          registry_username: test
          registry_password: test-pass
          image: localhost:5000/e2e
          tags: bad tag!
          webhook_url: http://127.0.0.1:8080/deploy

      - name: Assert invalid tag fails without calling the webhook
        env:
          OUTCOME: ${{ steps.bad_tag.outcome }}
        run: |
          [[ "$OUTCOME" == "failure" ]]
          [[ ! -e "$RUNNER_TEMP/webhook.json" ]]
```

- [ ] **Step 2: Rodar o lint e confirmar que falha**

Run: `actionlint .github/workflows/test.yml`
Expected: o actionlint não valida o comportamento, então passa. A falha real aparece no CI: `Assert webhook` falha com `FileNotFoundError` porque a action ainda não chama o webhook. Confirmar com push na branch:

```bash
git add .github/workflows/test.yml
git commit -m "test: add webhook e2e assertions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push
gh run watch --exit-status "$(gh run list --limit 1 --json databaseId -q '.[0].databaseId')"
```
Expected: job `e2e` FAIL no passo `Assert webhook`.

- [ ] **Step 3: Inserir o passo do webhook no `action.yml`**

Imediatamente **antes** de `- name: Clean up credentials`:

```yaml
    - name: Call webhook
      if: inputs.webhook_url != ''
      shell: bash
      env:
        WEBHOOK_URL: ${{ inputs.webhook_url }}
        WEBHOOK_HEADERS: ${{ inputs.webhook_headers }}
        WEBHOOK_PAYLOAD: ${{ inputs.webhook_payload }}
        WEBHOOK_FAIL_ON_ERROR: ${{ inputs.webhook_fail_on_error }}
      run: python3 -I "$GITHUB_ACTION_PATH/webhook.py"
```

Notas: o `if` sem função de status implica `success()`, então o webhook só roda se todos os passos anteriores passaram (Review Focus 5). O `-I` faz o Python ignorar `PYTHONPATH` e o site do usuário.

- [ ] **Step 4: Rodar lint, unit e CI e confirmar que passa**

Run: `actionlint .github/workflows/test.yml && python3 -m unittest test_webhook`
Expected: exit 0 e `OK`.

```bash
git add action.yml
git commit -m "feat: call webhook after successful push

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push
gh run watch --exit-status "$(gh run list --limit 1 --json databaseId -q '.[0].databaseId')"
```
Expected: `unit` e `e2e` verdes.

---

### Task 4: README e LICENSE

**Files:**
- Create: `README.md`
- Create: `LICENSE`

**Interfaces:**
- Consumes: inputs/outputs do `action.yml` (Task 2/3); os nomes na tabela devem ser idênticos.
- Produces: documentação exibida no Marketplace.

- [ ] **Step 1: Escrever `LICENSE`**

```text
MIT License

Copyright (c) 2026 Victo Hugo

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 2: Escrever `README.md`**

````markdown
# Docker Push & Webhook

Build and push a Docker image with the native Docker CLI, then optionally call a webhook (for example, to trigger a deploy). No third-party actions, no extra dependencies.

## Requirements

A Linux runner with Docker and Python 3, such as `ubuntu-latest`. GitHub-hosted macOS and Windows runners do not ship Docker.

## Usage

### Docker Hub

```yaml
- uses: actions/checkout@v4
- uses: <owner>/docker-push-webhook-action@v1
  with:
    registry_username: ${{ secrets.DOCKERHUB_USERNAME }}
    registry_password: ${{ secrets.DOCKERHUB_TOKEN }}
    image: myuser/myapp
    tags: latest, ${{ github.sha }}
```

### GHCR with a deploy webhook

```yaml
- uses: actions/checkout@v4
- uses: <owner>/docker-push-webhook-action@v1
  with:
    registry_url: ghcr.io
    registry_username: ${{ github.actor }}
    registry_password: ${{ secrets.GITHUB_TOKEN }}
    image: ghcr.io/${{ github.repository }}
    tags: |
      latest
      ${{ github.sha }}
    context: .
    dockerfile_path: ./apps/api
    build_args: |
      APP_VERSION=${{ github.sha }}
    webhook_url: ${{ secrets.DEPLOY_WEBHOOK_URL }}
    webhook_headers: '{"Authorization": "Bearer ${{ secrets.DEPLOY_TOKEN }}"}'
    webhook_payload: '{"image": "ghcr.io/${{ github.repository }}:${{ github.sha }}"}'
```

Add `permissions: packages: write` to the job when pushing to GHCR with `GITHUB_TOKEN`.

## Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `registry_url` | no | `''` | Registry host (e.g. `ghcr.io`). Empty means Docker Hub. |
| `registry_username` | yes | | Registry username. |
| `registry_password` | yes | | Registry password or token. Sent via stdin. |
| `image` | yes | | Full image name without tag (e.g. `ghcr.io/org/app`). |
| `tags` | no | `latest` | Tags separated by commas and/or newlines. |
| `context` | no | `.` | Build context. |
| `dockerfile_path` | no | `''` | Dockerfile path, or a directory containing one. Empty means `<context>/Dockerfile`. |
| `build_args` | no | `''` | One `KEY=VALUE` per line. |
| `webhook_url` | no | `''` | URL to POST to after a successful push. Empty skips the webhook. |
| `webhook_headers` | no | `{}` | JSON object of string headers. Values are masked in logs. |
| `webhook_payload` | no | `''` | Request body, sent exactly as given. `Content-Type` defaults to `application/json`. |
| `webhook_fail_on_error` | no | `true` | Fail the job on network errors, timeouts or non-2xx responses. Invalid `webhook_url`/`webhook_headers` always fail. |

## Outputs

| Output | Description |
|---|---|
| `image` | First pushed reference (`image:tag`). |
| `tags` | All pushed references, one per line. |
| `digest` | Digest of the pushed image (`sha256:...`). |

## Webhook behavior

- Always `POST`, 30-second timeout. Redirects are not followed and count as failures.
- The webhook runs only after a successful push and cannot reference this action's own outputs. Image and tags are already known to the caller. The digest is not available inside the payload.
- URL, headers, payload and response body are never printed in the logs.

## Security notes

- **Do not pass secrets in `build_args`.** Build args are stored in the image history.
- Registry credentials are kept in a temporary Docker config that is deleted at the end of the action, even on failure. Your job's default Docker config is never touched.
- Inputs are passed to scripts as environment variables, never interpolated into shell code.

## License

MIT
````

- [ ] **Step 3: Conferir que a tabela bate com o `action.yml`**

Run: `diff <(grep -oE '^  [a-z_]+:$' action.yml | sed -n '1,12p' | tr -d ' :') <(grep -oE '^\| `[a-z_]+`' README.md | sed -n '1,12p' | tr -d '|` ')`
Expected: sem saída (os 12 inputs batem, na mesma ordem).

- [ ] **Step 4: Commit**

```bash
git add README.md LICENSE
git commit -m "docs: add README and MIT license

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Publicação (manual, requer confirmação do usuário)

**Files:** nenhum.

**Interfaces:**
- Consumes: CI verde na `main` (Tasks 1–4).
- Produces: release `v1.0.0` + tag móvel `v1`.

- [ ] **Step 1: PARAR e pedir confirmação ao usuário**

Tornar o repositório público e publicar no Marketplace são ações externas e irreversíveis na prática. Confirmar o owner e substituir `<owner>` no README.

- [ ] **Step 2: Tornar público e criar as tags**

```bash
gh repo edit <owner>/docker-push-webhook-action --visibility public --accept-visibility-change-consequences
git tag -a v1.0.0 -m "v1.0.0"
git tag -f v1 v1.0.0
git push origin v1.0.0
git push -f origin v1
```

- [ ] **Step 3: Publicar a release no Marketplace**

O `gh release create` não marca a opção do Marketplace. O usuário precisa criar a release `v1.0.0` pela UI do GitHub e marcar **"Publish this Action to the GitHub Marketplace"**, que exige aceitar o termo do Marketplace e ter 2FA ativo.
