# Vários webhooks e corpo montado na execução — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `webhook_url` aceita uma URL por linha; sem `webhook_payload`, a action envia um JSON montado na execução (versão, digest, tags, commit, autor, link da execução); marcadores `{version}`, `{digest}`, `{image}`, `{sha}` na URL e no payload customizado.

**Architecture:** Toda a lógica fica em `webhook.py` (stdlib), que lê tudo de variáveis de ambiente. `action.yml` passa ao passo do webhook os dados da execução (`image` e `version`/`refs` do prepare, `digest` do push). `prepare.sh` ganha o output `image` (`<registry>/<name>`, sem tag). Documentação e templates passam a usar `webhook_url` em bloco com `DEPLOY_WEBHOOK_URL` e `PORTAINER_WEBHOOK_URL`.

**Tech Stack:** GitHub Actions (composite), Python 3 stdlib (`urllib`, `json`, `subprocess`, `unittest`), Bash.

**Spec:** `docs/superpowers/specs/2026-10-14-multi-webhook-runtime-payload-design.md`

## Global Constraints

- `webhook.py` e testes: **somente stdlib**, compatíveis com **Python 3.9+** (o Python local é 3.9.6).
- `prepare.sh` roda no Bash 3.2 do macOS e no Bash 5 do runner.
- Nenhum `${{ inputs.* }}` dentro de bloco `run:` do `action.yml`; tudo entra via `env:`.
- Webhook: sempre `POST`, timeout `30` segundos por destino, sem seguir redirecionamentos, sucesso = 2xx (regras atuais).
- URL, headers, corpo e resposta do webhook **nunca** aparecem no log. Destinos são identificados só pela posição: `Webhook 1/2: HTTP 202`, `Webhook 2/2 falhou: HTTP 500`.
- Erro de configuração (URL inválida, headers inválidos) falha o job **antes de qualquer envio**, independente de `webhook_fail_on_error`.
- Falha de entrega de um destino não impede os demais; o job falha no fim (ou só avisa, com `webhook_fail_on_error: false`).
- Corpo padrão: exatamente os campos `repository`, `image`, `version`, `tags`, `digest`, `commit`, `commit_message`, `author`, `run_url`, gerado com `json.dumps(..., ensure_ascii=False)`, `Content-Type: application/json`.
- Marcadores: só `{version}`, `{digest}`, `{image}`, `{sha}`; na URL o valor é codificado com `urllib.parse.quote(valor, safe="")`; no payload entra como texto; marcador desconhecido fica intacto; valor vazio vira texto vazio.
- Mensagens novas voltadas ao usuário em português do Brasil; README técnico em inglês; `INSTALAR.md` e `llms.txt` em português.
- Secrets usados nos exemplos: `DEPLOY_WEBHOOK_URL` e `PORTAINER_WEBHOOK_URL` (além dos já existentes).
- Commits **sem** linhas de atribuição (sem `Co-Authored-By`).

## Review Focus

1. **URL colada no secret com espaço ou `\r` no fim** (comum ao colar no GitHub): deve funcionar, não virar "URL inválida" (teste na Task 1).
2. **Mensagem de commit com acento e emoji** no corpo padrão: o JSON deve chegar válido em UTF-8 com o texto intacto (teste na Task 1).
3. **Sem histórico git** (sem `actions/checkout`, ou SHA desconhecido): `commit_message` e `author` vazios, sem quebrar o envio (teste na Task 1).
4. **URL com marcador e token na query, entre vários destinos:** nenhuma parte da URL vaza no log, mesmo após a substituição (teste na Task 1).
5. **Os dois secrets inexistentes** (texto do input só com quebras de linha): o passo termina com sucesso sem chamar nada (teste na Task 1; o `if:` do passo não filtra esse caso).

---

## File Structure

| Arquivo | Responsabilidade |
|---|---|
| `webhook.py` (modifica) | Lê as URLs (uma por linha), valida tudo, monta o corpo (padrão ou customizado com marcadores), envia a cada destino e resume o resultado |
| `test_webhook.py` (modifica) | Testes unitários do `webhook.py` com servidor HTTP local |
| `prepare.sh` (modifica) | Novo output `image` |
| `test_prepare.py` (modifica) | Confere o output `image` |
| `action.yml` (modifica) | Passa `WEBHOOK_IMAGE`, `WEBHOOK_VERSION`, `WEBHOOK_REFS`, `WEBHOOK_DIGEST` ao webhook; descrições dos inputs |
| `.github/workflows/test.yml` (modifica) | Listener grava por caminho; e2e com dois destinos e corpo padrão |
| `README.md`, `llms.txt`, `INSTALAR.md`, `templates/docker.yml`, `install.sh`, `test_docs.py` (modificam) | Documentação, templates e allowlist de secrets |

---

### Task 1: `webhook.py` com vários destinos, corpo padrão e marcadores

**Files:**
- Modify: `webhook.py` (reescrita completa)
- Modify: `test_webhook.py`

**Interfaces:**
- Consumes (variáveis de ambiente): `WEBHOOK_URL` (uma URL por linha), `WEBHOOK_HEADERS`, `WEBHOOK_PAYLOAD` (bytes via `os.environb`), `WEBHOOK_FAIL_ON_ERROR`, `WEBHOOK_IMAGE`, `WEBHOOK_VERSION`, `WEBHOOK_REFS` (refs `imagem:tag`, uma por linha), `WEBHOOK_DIGEST`, `GITHUB_SHA`, `GITHUB_REPOSITORY`, `GITHUB_SERVER_URL`, `GITHUB_RUN_ID`. Executado no diretório do workspace (para `git log`).
- Produces: o contrato de variáveis acima, usado pelo `action.yml` na Task 2. Saída de log: `Webhook <i>/<n>: HTTP <status>` em sucesso; `::error::Webhook <i>/<n> falhou: ...` (ou `::warning::` com `fail_on_error=false`); `Nenhum webhook configurado; nada a avisar.` quando não há URLs.

- [ ] **Step 1: Atualizar o servidor de teste e o helper**

Em `test_webhook.py`, trocar o cabeçalho de imports, o `_Handler`, o `setUp` e o `run_webhook` por:

```python
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
```

- [ ] **Step 2: Ajustar os testes existentes ao novo formato de log e ao corpo padrão**

Substituir estes testes (mantendo os demais como estão):

```python
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
```

E trocar `test_empty_payload_posts_without_body` por:

```python
    def test_empty_payload_sends_the_default_json(self):
        self.run_webhook(payload=b"")
        headers, body = self.server.received[0]
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(
            sorted(json.loads(body)),
            ["author", "commit", "commit_message", "digest", "image", "repository", "run_url", "tags", "version"],
        )
```

- [ ] **Step 3: Escrever os testes novos (falham)**

Adicionar à classe `WebhookTest`:

```python
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

    def test_urls_with_placeholders_are_never_logged(self):
        urls = f"{self.url(500)}?token=topsecret\n{self.url(200)}?tag={{version}}&token=topsecret"
        result = self.run_webhook(url=urls, env={"WEBHOOK_VERSION": "v1"})
        self.assertNotIn("topsecret", result.stdout + result.stderr)
        self.assertNotIn("127.0.0.1", result.stdout + result.stderr)
```

- [ ] **Step 4: Rodar e ver falhar**

Run: `cd /Users/invicto/Documents/Startup/docker-push-webhook-action && python3 -m unittest test_webhook 2>&1 | grep -E "^(FAIL|ERROR):|^Ran|^FAILED|^OK"`
Expected: várias falhas (ex.: `test_success_exits_zero` procura `Webhook 1/1: HTTP 200`; `test_every_url_gets_the_same_body...` falha porque a URL com quebra de linha é inválida).

- [ ] **Step 5: Reescrever `webhook.py`**

Overwrite `webhook.py` with:

```python
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
```

- [ ] **Step 6: Rodar e ver passar**

Run: `cd /Users/invicto/Documents/Startup/docker-push-webhook-action && python3 -m unittest -v test_webhook 2>&1 | tail -30`
Expected: todos `ok`. Depois rodar a suíte inteira uma vez: `python3 -m unittest 2>&1 | tail -3` → `OK`.

- [ ] **Step 7: Commit**

```bash
cd /Users/invicto/Documents/Startup/docker-push-webhook-action
git add webhook.py test_webhook.py
git commit -m "feat: webhook_url takes one URL per line; default JSON payload built at run time; placeholders"
```

---

### Task 2: Ligar os dados da execução ao webhook (`prepare.sh`, `action.yml`, e2e)

**Files:**
- Modify: `prepare.sh` (bloco de outputs), `test_prepare.py`, `action.yml` (passo "Call webhook" e descrições de inputs), `.github/workflows/test.yml`

**Interfaces:**
- Consumes: contrato de variáveis do `webhook.py` (Task 1): `WEBHOOK_IMAGE`, `WEBHOOK_VERSION`, `WEBHOOK_REFS`, `WEBHOOK_DIGEST`.
- Produces: output `image` do passo `prepare` (`<registry>/<name>`, sem tag).

- [ ] **Step 1: Teste do novo output (falha)**

Em `test_prepare.py`, em `DefaultsTest.test_name_and_tags_default_to_repo_and_sha`, logo após `self.assertEqual(o["registry"], "registry.example.com")`, adicionar:

```python
        self.assertEqual(o["image"], "registry.example.com/my-repo")
```

Run: `python3 -m unittest test_prepare.DefaultsTest 2>&1 | tail -3`
Expected: FAIL (`KeyError: 'image'`).

- [ ] **Step 2: Emitir o output `image` no `prepare.sh`**

No bloco que escreve em `$GITHUB_OUTPUT`, logo após a linha `  echo "registry_host=${registry%%/*}"`, adicionar:

```bash
  echo "image=$image"
```

Run: `python3 -m unittest test_prepare 2>&1 | tail -3`
Expected: `OK`.

- [ ] **Step 3: Passar os dados ao webhook no `action.yml`**

Substituir o passo `Call webhook` por:

```yaml
    - name: Call webhook
      if: inputs.webhook_url != ''
      shell: bash
      env:
        WEBHOOK_URL: ${{ inputs.webhook_url }}
        WEBHOOK_HEADERS: ${{ inputs.webhook_headers }}
        WEBHOOK_PAYLOAD: ${{ inputs.webhook_payload }}
        WEBHOOK_FAIL_ON_ERROR: ${{ inputs.webhook_fail_on_error }}
        WEBHOOK_IMAGE: ${{ steps.prepare.outputs.image }}
        WEBHOOK_VERSION: ${{ steps.prepare.outputs.version }}
        WEBHOOK_REFS: ${{ steps.prepare.outputs.refs }}
        WEBHOOK_DIGEST: ${{ steps.push.outputs.digest }}
      run: python3 -I "$GITHUB_ACTION_PATH/webhook.py"
```

E trocar as descrições dos inputs:

```yaml
  webhook_url:
    description: URLs to POST to after a successful push, one per line (empty lines are ignored). Accepts {version}, {digest}, {image} and {sha} placeholders. Empty skips the webhook.
```

```yaml
  webhook_payload:
    description: Webhook body, sent as given with {version}, {digest}, {image} and {sha} filled in. Empty sends a JSON body describing the push (repository, image, version, tags, digest, commit, commit_message, author, run_url).
```

(manter `required: false` e `default: ''` de cada um.)

- [ ] **Step 4: Listener do e2e grava por caminho**

Em `.github/workflows/test.yml`, no step "Start webhook listener", trocar o conteúdo do `listener.py` e a linha `nohup` por:

```python
          import http.server, json, sys

          class Handler(http.server.BaseHTTPRequestHandler):
              def do_POST(self):
                  body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                  record = {"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body.decode()}
                  name = self.path.split("?")[0].strip("/").replace("/", "-") or "root"
                  with open(f"{sys.argv[1]}-{name}.json", "w") as f:
                      json.dump(record, f)
                  self.send_response(204)
                  self.end_headers()

          http.server.HTTPServer(("127.0.0.1", 8080), Handler).serve_forever()
```

```bash
          nohup python3 "$RUNNER_TEMP/listener.py" "$RUNNER_TEMP/webhook" >/dev/null 2>&1 &
```

E atualizar as três referências ao arquivo antigo: no step "Assert webhook", `"$RUNNER_TEMP/webhook.json"` (duas vezes: no `python3 -` e no `rm`) vira `"$RUNNER_TEMP/webhook-deploy.json"`; no step "Assert bad config fails without calling the webhook", `[[ ! -e "$RUNNER_TEMP/webhook.json" ]]` vira `[[ ! -e "$RUNNER_TEMP/webhook-deploy.json" ]]`.

- [ ] **Step 5: E2E com dois destinos e corpo padrão**

No step `id: defaults`, adicionar ao `with:`:

```yaml
          webhook_url: |
            http://127.0.0.1:8080/a?tag={version}

            http://127.0.0.1:8080/b
```

E adicionar, logo após o step "Assert default name, default tags and lowercased name", o step:

```yaml
      - name: Assert default webhook payload
        env:
          SHA: ${{ github.sha }}
          REPO: ${{ github.event.repository.name }}
          VERSION: ${{ steps.defaults.outputs.version }}
          DIGEST: ${{ steps.defaults.outputs.digest }}
          RUN_ID: ${{ github.run_id }}
        run: |
          python3 - "$RUNNER_TEMP" <<'PY'
          import json, os, sys
          tmp = sys.argv[1]
          a = json.load(open(f"{tmp}/webhook-a.json"))
          b = json.load(open(f"{tmp}/webhook-b.json"))
          assert a["body"] == b["body"], "both destinations must get the same body"
          assert a["path"] == "/a?tag=" + os.environ["VERSION"], a["path"]
          assert a["headers"]["content-type"] == "application/json", a["headers"]
          body = json.loads(a["body"])
          sha = os.environ["SHA"]
          assert body["image"] == "localhost:5000/" + os.environ["REPO"].lower(), body
          assert body["version"] == os.environ["VERSION"], body
          assert body["digest"] == os.environ["DIGEST"], body
          assert body["tags"] == ["latest", sha, os.environ["VERSION"]], body
          assert body["commit"] == sha, body
          assert body["commit_message"], body
          assert body["author"], body
          assert body["run_url"].endswith("/actions/runs/" + os.environ["RUN_ID"]), body
          PY
```

- [ ] **Step 6: Validar**

Run: `cd /Users/invicto/Documents/Startup/docker-push-webhook-action && python3 -c "import yaml; yaml.safe_load(open('action.yml'))" && actionlint .github/workflows/test.yml && python3 -m unittest 2>&1 | tail -3`
Expected: sem erro de YAML, `actionlint` limpo, `OK`. (O e2e só roda no CI; ver Task 4.)

- [ ] **Step 7: Commit**

```bash
git add prepare.sh test_prepare.py action.yml .github/workflows/test.yml
git commit -m "feat: pass image, version, tags and digest to the webhook; e2e with two destinations"
```

---

### Task 3: Documentação, templates e instalador

**Files:**
- Modify: `README.md`, `llms.txt`, `INSTALAR.md`, `templates/docker.yml`, `install.sh` (workflow gerado), `test_docs.py`

**Interfaces:**
- Consumes: comportamento das Tasks 1-2 (inputs com o mesmo nome; corpo padrão com os 9 campos).
- Produces: nada consumido por outras tasks.

- [ ] **Step 1: Teste de consistência (falha)**

Em `test_docs.py`, adicionar `"PORTAINER_WEBHOOK_URL",` ao conjunto `SECRETS`, e adicionar à classe `DocsConsistencyTest`:

```python
    def test_docs_describe_the_default_webhook_payload(self):
        fields = ["repository", "image", "version", "tags", "digest", "commit", "commit_message", "author", "run_url"]
        for name in ("README.md", "llms.txt"):
            text = (ROOT / name).read_text()
            for field in fields:
                self.assertIn(f'"{field}"', text, f"{name}: corpo padrão sem o campo {field}")
            self.assertIn("{version}", text, f"{name}: sem o marcador {{version}}")
            self.assertIn("PORTAINER_WEBHOOK_URL", text, name)
        for name in ("templates/docker.yml",):
            text = (ROOT / name).read_text()
            self.assertIn("secrets.PORTAINER_WEBHOOK_URL", text, name)
```

Run: `python3 -m unittest test_docs 2>&1 | tail -3`
Expected: FAIL.

- [ ] **Step 2: `templates/docker.yml`**

Trocar a linha `          webhook_url: ${{ secrets.DEPLOY_WEBHOOK_URL }}` por:

```yaml
          # Avisos depois do envio, um destino por linha (secret que não existe é ignorado):
          #   DEPLOY_WEBHOOK_URL    -> ex.: fluxo do Power Automate que avisa no Teams
          #   PORTAINER_WEBHOOK_URL -> webhook do Portainer para deploy automático
          # Cada destino recebe um JSON com versão, digest, commit, autor e link da execução.
          webhook_url: |
            ${{ secrets.DEPLOY_WEBHOOK_URL }}
            ${{ secrets.PORTAINER_WEBHOOK_URL }}
```

E, no cabeçalho do arquivo (lista de Secrets), logo após a linha do `DEPLOY_WEBHOOK_URL` e suas linhas de continuação, adicionar:

```yaml
#     PORTAINER_WEBHOOK_URL -> (opcional) webhook do Portainer para deploy
#                            automático. Para escolher a versão exata, termine
#                            a URL com ?tag={version}
```

- [ ] **Step 3: `install.sh` (workflow gerado)**

Trocar a linha `          webhook_url: \${{ secrets.DEPLOY_WEBHOOK_URL }}` por:

```bash
          webhook_url: |
            \${{ secrets.DEPLOY_WEBHOOK_URL }}
            \${{ secrets.PORTAINER_WEBHOOK_URL }}
```

Run: `python3 -m unittest test_install 2>&1 | tail -3`
Expected: `OK` (o `check_workflow` aceita os dois secrets e o `actionlint` valida o YAML gerado).

- [ ] **Step 4: `README.md`**

4a. Na tabela de inputs, trocar as linhas de `webhook_url` e `webhook_payload` por:

```markdown
| `webhook_url` | no | `''` | URLs to POST to after a successful push, one per line. Empty lines are ignored; empty skips the webhook. Accepts placeholders (see [Webhooks](#webhooks)). |
| `webhook_payload` | no | `''` | Request body, sent as given with placeholders filled in. Empty sends the default JSON (see [Webhooks](#webhooks)). `Content-Type` defaults to `application/json`. |
```

4b. No exemplo "Several images and a deploy webhook", trocar as linhas `webhook_url: ${{ secrets.DEPLOY_WEBHOOK_URL }}`, `webhook_headers: ...` e `webhook_payload: '{"image": "${{ matrix.name }}"}'` por:

```yaml
          webhook_url: |
            ${{ secrets.DEPLOY_WEBHOOK_URL }}
            ${{ secrets.PORTAINER_WEBHOOK_URL }}
```

4c. Substituir a seção inteira `## Webhook behavior` (até antes de `## Security notes`) por:

````markdown
## Webhooks

`webhook_url` takes one URL per line. After a successful push, each one receives a `POST`, in order. Empty lines are ignored, so a secret that does not exist yet simply skips that destination:

```yaml
webhook_url: |
  ${{ secrets.DEPLOY_WEBHOOK_URL }}
  ${{ secrets.PORTAINER_WEBHOOK_URL }}
```

Without `webhook_payload`, every destination receives this JSON, built after the push:

```json
{
  "repository": "acme/app",
  "image": "registry.example.com/app",
  "version": "2026.10.14-1532Z-c14ae67",
  "tags": ["latest", "c14ae67b85586abfa9fa7fbed1998d35bb9719d8", "2026.10.14-1532Z-c14ae67"],
  "digest": "sha256:15b6ff5249a174ad0f7dc406b8fcd0950e323d1c316c12f08102a892091281fd",
  "commit": "c14ae67b85586abfa9fa7fbed1998d35bb9719d8",
  "commit_message": "fix: retry connector login",
  "author": "Ana Souza",
  "run_url": "https://github.com/acme/app/actions/runs/123456789"
}
```

`commit_message` and `author` are empty when there is no git history (no `actions/checkout`). `version` is empty when you set `tags`.

Placeholders, filled in every URL line and in a custom `webhook_payload`:

| Placeholder | Value |
|---|---|
| `{version}` | version tag (e.g. `2026.10.14-1532Z-c14ae67`) |
| `{digest}` | image digest |
| `{image}` | image without tag |
| `{sha}` | full commit SHA |

In URLs the value is URL-encoded. Unknown placeholders are left as they are. Example, a Portainer service webhook that deploys the exact version: store `https://portainer.example.com/api/webhooks/<id>?tag={version}` in `PORTAINER_WEBHOOK_URL`.

For a Power Automate flow ("When an HTTP request is received"), use this request body schema to get every field as dynamic content:

```json
{
  "type": "object",
  "properties": {
    "repository": {"type": "string"},
    "image": {"type": "string"},
    "version": {"type": "string"},
    "tags": {"type": "array", "items": {"type": "string"}},
    "digest": {"type": "string"},
    "commit": {"type": "string"},
    "commit_message": {"type": "string"},
    "author": {"type": "string"},
    "run_url": {"type": "string"}
  }
}
```

Behavior:

- Always `POST`, 30-second timeout per destination. Redirects are not followed and count as failures.
- Every URL and header is validated before anything is sent; a configuration error sends nothing.
- A failing destination does not stop the others. The job fails at the end (or only warns with `webhook_fail_on_error: false`).
- The log names destinations only by position (`Webhook 2/2 falhou: HTTP 500`). URLs, headers, payload and response bodies are never printed.
- `webhook_url` must not embed credentials (`https://user:pass@host`); send them in `webhook_headers` instead. Headers go to every destination.
- Since v2.3.0, an empty `webhook_payload` sends the default JSON (it used to send no body).

````

- [ ] **Step 5: `llms.txt`**

5a. Na tabela de inputs, trocar as linhas de `webhook_url` e `webhook_payload` por:

```markdown
| `webhook_url` | não | | URLs para POST após o push, uma por linha; linhas vazias são ignoradas; aceita marcadores |
| `webhook_payload` | não | | Corpo enviado como está, com marcadores preenchidos; vazio = JSON padrão (ver Webhooks) |
```

5b. No workflow mínimo, trocar `          webhook_url: ${{ secrets.DEPLOY_WEBHOOK_URL }}` por:

```yaml
          webhook_url: |
            ${{ secrets.DEPLOY_WEBHOOK_URL }}
            ${{ secrets.PORTAINER_WEBHOOK_URL }}
```

5c. Na lista de secrets, logo após a linha do `DEPLOY_WEBHOOK_URL`, adicionar:

```markdown
- `PORTAINER_WEBHOOK_URL` (Secret): opcional; webhook do Portainer para deploy automático. Para a versão exata, termine com `?tag={version}`
```

5d. Antes de `## Regras para a IA`, adicionar:

````markdown
## Webhooks

`webhook_url` aceita uma URL por linha; cada uma recebe um `POST` depois do push. Secret inexistente vira linha vazia e é ignorado. Sem `webhook_payload`, todos recebem este JSON:

```json
{
  "repository": "acme/app",
  "image": "registry.example.com/app",
  "version": "2026.10.14-1532Z-c14ae67",
  "tags": ["latest", "c14ae67b85586abfa9fa7fbed1998d35bb9719d8", "2026.10.14-1532Z-c14ae67"],
  "digest": "sha256:15b6ff52…",
  "commit": "c14ae67b85586abfa9fa7fbed1998d35bb9719d8",
  "commit_message": "fix: retry connector login",
  "author": "Ana Souza",
  "run_url": "https://github.com/acme/app/actions/runs/123456789"
}
```

Marcadores na URL e no payload customizado: `{version}`, `{digest}`, `{image}`, `{sha}`. Se um destino falhar, os outros ainda são chamados; o log identifica só a posição (`Webhook 2/2 falhou: HTTP 500`).

- Não escreva `webhook_payload` se o JSON padrão serve (ele já leva a versão, que um payload escrito no YAML não consegue levar).
- O cartão do Teams é montado no fluxo do Power Automate, a partir desses campos; a action não formata mensagens.
````

- [ ] **Step 6: `INSTALAR.md`**

Na tabela da aba Secrets, logo após a linha do `DEPLOY_WEBHOOK_URL`, adicionar:

```markdown
| `PORTAINER_WEBHOOK_URL` | (opcional) webhook do Portainer para deploy automático. Para subir a versão exata, termine a URL com `?tag={version}` |
```

E, antes de `## Voltar para uma versão anterior (rollback)`, adicionar:

```markdown
## Avisar o Teams e o Portainer

Depois de cada envio, a action avisa cada endereço cadastrado em `DEPLOY_WEBHOOK_URL` (por exemplo, o fluxo do Power Automate que posta no Teams) e em `PORTAINER_WEBHOOK_URL` (deploy automático). Quem não estiver cadastrado é simplesmente pulado, então dá para criar o do Portainer depois, sem mexer no workflow.

Cada aviso leva a versão (`2026.10.14-1532Z-c14ae67`), o commit, quem fez, a mensagem do commit e o link da execução. No Power Automate, use esses campos para montar a mensagem do Teams (o README tem o esquema JSON para o gatilho).
```

- [ ] **Step 7: Rodar tudo**

Run: `cd /Users/invicto/Documents/Startup/docker-push-webhook-action && python3 -m unittest 2>&1 | tail -3 && actionlint templates/docker.yml && echo lint-ok`
Expected: `OK` e `lint-ok`.

- [ ] **Step 8: Commit**

```bash
git add README.md llms.txt INSTALAR.md templates/docker.yml install.sh test_docs.py
git commit -m "docs: multiple webhooks, default JSON payload, placeholders and Portainer"
```

---

### Task 4: CI, lançamento e repositório consumidor

**Files:**
- Modify (repo `scimgateway-entraid-modified`): `.github/workflows/docker.yml`

**Interfaces:**
- Consumes: branch com Tasks 1-3.
- Produces: `v2.3.0`, tag `v2` movida; consumidor com `webhook_url` em bloco.

Ações externas (push, PR, merge, tag, release, push no consumidor) foram aprovadas no spec (seção 9). Usar `GH_TOKEN=$(gh auth token -u invicto-dev)`.

- [ ] **Step 1: PR e CI**

```bash
cd /Users/invicto/Documents/Startup/docker-push-webhook-action
git push -u origin multi-webhook
export GH_TOKEN=$(gh auth token -u invicto-dev)
gh pr create -R invicto-dev/docker-push-webhook-action --base main --head multi-webhook \
  --title "feat: vários webhooks com corpo montado na execução (v2.3.0)" \
  --body "Implementa docs/superpowers/specs/2026-10-14-multi-webhook-runtime-payload-design.md: webhook_url com uma URL por linha, JSON padrão com versão/digest/commit/autor/link, marcadores {version} {digest} {image} {sha}, Portainer pronto via PORTAINER_WEBHOOK_URL. Mudança de comportamento: webhook_payload vazio agora envia o JSON padrão."
gh pr checks <número> -R invicto-dev/docker-push-webhook-action --watch --interval 20
```

Expected: `unit` e `e2e` verdes. Se o e2e falhar, ler `gh run view <id> --log-failed`, corrigir a causa raiz e repetir.

- [ ] **Step 2: Merge, tag e release**

```bash
gh pr merge <número> -R invicto-dev/docker-push-webhook-action --merge
M=$(gh api repos/invicto-dev/docker-push-webhook-action/commits/main --jq .sha)
git fetch -q origin --tags --force
git tag -f v2 "$M" && git push -f origin v2
git tag v2.3.0 "$M" && git push origin v2.3.0
gh release create v2.3.0 -R invicto-dev/docker-push-webhook-action --verify-tag --title "v2.3.0" --notes "- webhook_url aceita uma URL por linha (ex.: Teams via Power Automate e Portainer); secret inexistente é ignorado.
- Sem webhook_payload, cada destino recebe um JSON montado na execução: repository, image, version, tags, digest, commit, commit_message, author, run_url.
- Marcadores {version}, {digest}, {image}, {sha} na URL e no payload (ex.: Portainer ?tag={version}).
- Um destino com falha não impede os outros; o log mostra só a posição.
- Mudança de comportamento: webhook_payload vazio agora envia o JSON padrão (antes, POST sem corpo)."
```

- [ ] **Step 3: Consumidor**

Em `/Users/invicto/Documents/QRIAR-1.0/scimgateway-entraid-modified/.github/workflows/docker.yml`, trocar:

```yaml
          webhook_url: ${{ secrets.DEPLOY_WEBHOOK_URL }}
          webhook_payload: '{"image": "${{ matrix.name }}", "sha": "${{ github.sha }}"}'
```

por:

```yaml
          # Avisos depois do envio (secret que não existe é ignorado):
          #   DEPLOY_WEBHOOK_URL    -> fluxo do Power Automate que avisa no Teams
          #   PORTAINER_WEBHOOK_URL -> deploy automático (quando existir)
          webhook_url: |
            ${{ secrets.DEPLOY_WEBHOOK_URL }}
            ${{ secrets.PORTAINER_WEBHOOK_URL }}
```

```bash
cd /Users/invicto/Documents/QRIAR-1.0/scimgateway-entraid-modified
actionlint .github/workflows/docker.yml
git add .github/workflows/docker.yml
git commit -m "ci: notify every webhook with the action's default JSON payload"
git push origin main
```

- [ ] **Step 4: Verificação real**

```bash
export GH_TOKEN=$(gh auth token -u invicto-dev)
R=QRIAR-CyberSecurity/scimgateway-entraid-modified
ID=$(gh run list -R $R -L 1 --json databaseId --jq '.[0].databaseId')
gh run watch $ID -R $R --exit-status
gh run view $ID -R $R --log | grep -E "Webhook [0-9]+/[0-9]+"
```

Expected: run verde; em cada job, `Webhook 1/1: HTTP 202` (só o Power Automate existe; a linha vazia do Portainer é ignorada). Pedir ao usuário para conferir no histórico do fluxo do Power Automate que o corpo novo chegou com `version`, `commit_message` e `run_url`.
