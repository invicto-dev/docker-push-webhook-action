# v2 — Instalação à prova de erro — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tornar a action instalável por pessoas leigas sem erro: `name` curto no lugar de `image`, validação com mensagens em português, instalação por navegador (template + `INSTALAR.md`) e por terminal (`install.sh`), mais `llms.txt` para IAs.

**Architecture:** A validação sai do `action.yml` e vai para `prepare.sh` (Bash, lê tudo de variáveis de ambiente), testável localmente via `unittest` chamando `bash prepare.sh`. `action.yml` só orquestra. A documentação (template, `INSTALAR.md`, `llms.txt`, README) é amarrada ao `action.yml` por um teste de consistência (`test_docs.py`). `install.sh` gera o mesmo workflow do template e é testado com um `gh` simulado.

**Tech Stack:** GitHub Actions (composite), Bash (compatível com 3.2 do macOS e 5 do runner), Python 3.9+ stdlib (`unittest`), `gh` CLI, `actionlint`.

**Spec:** `docs/superpowers/specs/2026-10-13-v2-beginner-friendly-design.md`

## Global Constraints

- Runner alvo: Linux com `docker` e `python3` (`ubuntu-*`, Bash 5). `prepare.sh` e `install.sh` devem rodar também no Bash 3.2 do macOS: sem `mapfile`, sem arrays associativos, sem `${var,,}`, sem expandir array possivelmente vazio (`"${arr[@]}"`).
- Testes Python: somente stdlib, compatíveis com Python 3.9+.
- `action.yml` não usa action de terceiros. Nenhum `${{ inputs.* }}` dentro de bloco `run:`; todo input entra via `env:` e é usado entre aspas.
- Todo `run:` do `action.yml` começa com `set -euo pipefail` (exceto cleanup: `set -uo pipefail`).
- Mensagens de runtime voltadas ao usuário (`::error::`, `::notice::`, saída do `install.sh`, resumo) em **português do Brasil**. README técnico, descrições de inputs em `action.yml` e comentários de código em **inglês**. `INSTALAR.md`, `llms.txt` e comentários do template em português.
- Os 3 secrets padrão têm estes nomes exatos: `REGISTRY_URL`, `REGISTRY_USERNAME`, `REGISTRY_PASSWORD`. Opcional: `DEPLOY_WEBHOOK_URL`. Secrets são **por repositório** por padrão; `--org` é opcional.
- Referência da action em templates e docs: `invicto-dev/docker-push-webhook-action@v2`.
- Senhas nunca aparecem em argv nem na saída padrão.
- Arquivos novos permitidos: `prepare.sh`, `install.sh`, `templates/docker.yml`, `INSTALAR.md`, `llms.txt`, `test_prepare.py`, `test_docs.py`, `test_install.py`.
- Commits terminam com `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- O `webhook.py` e `test_webhook.py` não mudam.

## Review Focus

1. **Host colado como a pessoa copiou** (`https://Registry.Exemplo.com//`, com espaços ou maiúsculas): deve ser normalizado, não rejeitado (testes na Task 1; e2e na Task 2).
2. **Quebra de linha em `name` ou `registry_url`** não pode injetar linhas extras no `GITHUB_OUTPUT` (teste na Task 1).
3. **Secret inexistente chega vazio**: `registry_url` vazio nunca pode cair no Docker Hub; usuário/senha vazios nomeiam o secret faltante (testes na Task 1 e e2e na Task 2).
4. **`install.sh` em repo com vários Dockerfiles ou com workflow já existente**: gera `matrix` com nomes únicos e não sobrescreve sem confirmação (testes na Task 4).
5. **Senha com aspas, `$`, `!` e espaços** chega intacta ao `gh secret set` via stdin e não vaza em argv/saída (teste na Task 4).

---

## File Structure

| Arquivo | Responsabilidade |
|---|---|
| `prepare.sh` (novo) | Valida e normaliza inputs, escreve outputs (`registry`, `dockerfile`, `docker_config`, `refs`) |
| `test_prepare.py` (novo) | Testes unitários do `prepare.sh` |
| `action.yml` (modifica) | Interface v2; chama `prepare.sh`; login/cleanup usam o registry normalizado; resumo da execução |
| `.github/workflows/test.yml` (modifica) | Roda todos os testes unitários; e2e atualizado para `name`/novas validações |
| `templates/docker.yml` (novo) | Workflow pronto para copiar (caminho navegador) |
| `INSTALAR.md` (novo) | Passo a passo para leigos + tabela de erros |
| `llms.txt` (novo) | Instruções para IAs |
| `README.md` (modifica) | Documentação técnica da v2 |
| `test_docs.py` (novo) | Consistência entre `action.yml`, template, docs e mensagens do `prepare.sh` |
| `install.sh` (novo) | Instalador por terminal |
| `test_install.py` (novo) | Testes do `install.sh` com `gh` simulado |

---

### Task 1: `prepare.sh` com validação e normalização

**Files:**
- Create: `prepare.sh`, `test_prepare.py`
- Modify: `.github/workflows/test.yml` (job `unit`)

**Interfaces:**
- Consumes (variáveis de ambiente): `REGISTRY_URL`, `REGISTRY_USERNAME`, `REGISTRY_PASSWORD`, `NAME`, `TAGS`, `BUILD_CONTEXT`, `DOCKERFILE_PATH`, mais `GITHUB_REPOSITORY`, `GITHUB_SHA`, `GITHUB_OUTPUT`, `RUNNER_TEMP`.
- Produces (linhas em `$GITHUB_OUTPUT`): `registry=<host normalizado, com namespace se houver>`, `registry_host=<só o host, usado por docker login/logout>`, `dockerfile=<caminho>`, `docker_config=<pasta temporária>`, `refs<<EOF ... EOF` (uma referência `registry/name:tag` por linha). Em erro: `::error::<mensagem pt-BR>` no stdout e exit 1, sem escrever nada em `GITHUB_OUTPUT`.

- [ ] **Step 1: Escrever os testes que falham**

Create `test_prepare.py`:

```python
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "prepare.sh"


def parse_outputs(text):
    """Parses a GITHUB_OUTPUT file (key=value and key<<DELIM blocks)."""
    outputs, lines, i = {}, text.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        if "<<" in line:
            key, delim = line.split("<<", 1)
            block = []
            i += 1
            while lines[i] != delim:
                block.append(lines[i])
                i += 1
            outputs[key] = "\n".join(block)
        elif "=" in line:
            key, value = line.split("=", 1)
            outputs[key] = value
        i += 1
    return outputs


def run_prepare(files=("Dockerfile",), **env_overrides):
    """Runs prepare.sh in a temp dir. Returns (returncode, stdout+stderr, outputs)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for name in files:
            path = tmp / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("FROM scratch\n")
        out_file = tmp / "github_output"
        out_file.touch()
        env = {
            "PATH": os.environ["PATH"],
            "RUNNER_TEMP": str(tmp),
            "GITHUB_OUTPUT": str(out_file),
            "GITHUB_REPOSITORY": "Acme/My-Repo",
            "GITHUB_SHA": "abc123",
            "REGISTRY_URL": "registry.example.com",
            "REGISTRY_USERNAME": "user",
            "REGISTRY_PASSWORD": "pass",
            "NAME": "",
            "TAGS": "",
            "BUILD_CONTEXT": ".",
            "DOCKERFILE_PATH": "",
        }
        env.update(env_overrides)
        proc = subprocess.run(
            ["bash", str(SCRIPT)], cwd=tmp, env=env, capture_output=True, text=True
        )
        return proc.returncode, proc.stdout + proc.stderr, parse_outputs(out_file.read_text())


class DefaultsTest(unittest.TestCase):
    def test_name_and_tags_default_to_repo_and_sha(self):
        rc, out, o = run_prepare()
        self.assertEqual(rc, 0, out)
        self.assertEqual(o["registry"], "registry.example.com")
        self.assertEqual(
            o["refs"],
            "registry.example.com/my-repo:latest\nregistry.example.com/my-repo:abc123",
        )
        self.assertEqual(o["dockerfile"], "./Dockerfile")
        self.assertTrue(o["docker_config"])


class RegistryTest(unittest.TestCase):
    def test_pasted_registry_is_normalized(self):
        rc, out, o = run_prepare(REGISTRY_URL="  HTTPS://Registry.Example.com//  ")
        self.assertEqual(rc, 0, out)
        self.assertEqual(o["registry"], "registry.example.com")

    def test_http_scheme_and_port_are_handled(self):
        rc, out, o = run_prepare(REGISTRY_URL="http://localhost:5000/")
        self.assertEqual(rc, 0, out)
        self.assertEqual(o["registry"], "localhost:5000")

    def test_namespace_is_kept_as_prefix(self):
        rc, out, o = run_prepare(REGISTRY_URL="registry.example.com/qriar", NAME="app")
        self.assertEqual(rc, 0, out)
        self.assertTrue(o["refs"].startswith("registry.example.com/qriar/app:latest"))

    def test_empty_registry_names_the_secret(self):
        for value in ("", "   "):
            rc, out, o = run_prepare(REGISTRY_URL=value)
            self.assertEqual(rc, 1, out)
            self.assertIn("REGISTRY_URL", out)
            self.assertIn("está vazio ou não existe", out)
            self.assertEqual(o, {})

    def test_scheme_only_registry_is_rejected(self):
        rc, out, _ = run_prepare(REGISTRY_URL="https://")
        self.assertEqual(rc, 1, out)
        self.assertIn("REGISTRY_URL", out)

    def test_registry_with_line_breaks_cannot_inject_outputs(self):
        rc, out, o = run_prepare(REGISTRY_URL="a.com\nEOF\nrefs<<EOF")
        self.assertEqual(rc, 1, out)
        self.assertEqual(o, {})


class CredentialsTest(unittest.TestCase):
    def test_empty_username_names_the_secret(self):
        rc, out, _ = run_prepare(REGISTRY_USERNAME="")
        self.assertEqual(rc, 1, out)
        self.assertIn("REGISTRY_USERNAME", out)

    def test_empty_password_names_the_secret(self):
        rc, out, _ = run_prepare(REGISTRY_PASSWORD="")
        self.assertEqual(rc, 1, out)
        self.assertIn("REGISTRY_PASSWORD", out)
        self.assertNotIn("pass", out.replace("REGISTRY_PASSWORD", ""))


class NameTest(unittest.TestCase):
    def test_uppercase_is_lowercased_with_notice(self):
        rc, out, o = run_prepare(NAME="Qriar/App")
        self.assertEqual(rc, 0, out)
        self.assertIn("::notice::", out)
        self.assertTrue(o["refs"].startswith("registry.example.com/qriar/app:latest"))

    def test_tag_or_digest_in_name_is_rejected(self):
        for value in ("app:v1", "app@sha256:abc"):
            rc, out, _ = run_prepare(NAME=value)
            self.assertEqual(rc, 1, out)
            self.assertIn("tags", out)

    def test_whitespace_and_line_breaks_are_rejected(self):
        for value in ("my app", "app\nEOF\nrefs<<EOF"):
            rc, out, o = run_prepare(NAME=value)
            self.assertEqual(rc, 1, out)
            self.assertEqual(o, {})


class TagsTest(unittest.TestCase):
    def test_commas_and_line_breaks_separate_tags(self):
        rc, out, o = run_prepare(NAME="app", TAGS="v1, v2\nv3")
        self.assertEqual(rc, 0, out)
        self.assertEqual(
            o["refs"].split("\n"),
            [f"registry.example.com/app:{t}" for t in ("v1", "v2", "v3")],
        )

    def test_invalid_tag_is_rejected(self):
        rc, out, _ = run_prepare(TAGS="bad tag!")
        self.assertEqual(rc, 1, out)
        self.assertIn("bad tag!", out)

    def test_only_separators_means_no_tags(self):
        rc, out, _ = run_prepare(TAGS=",")
        self.assertEqual(rc, 1, out)
        self.assertIn("Nenhuma tag", out)


class DockerfileTest(unittest.TestCase):
    def test_missing_dockerfile_shows_path(self):
        rc, out, _ = run_prepare(DOCKERFILE_PATH="missing/Dockerfile")
        self.assertEqual(rc, 1, out)
        self.assertIn("Dockerfile não encontrado: missing/Dockerfile", out)

    def test_directory_resolves_to_dockerfile_inside(self):
        rc, out, o = run_prepare(files=("docker/Dockerfile",), DOCKERFILE_PATH="docker")
        self.assertEqual(rc, 0, out)
        self.assertEqual(o["dockerfile"], "docker/Dockerfile")

    def test_context_is_used_when_no_dockerfile_path(self):
        rc, out, o = run_prepare(files=("app/Dockerfile",), BUILD_CONTEXT="app")
        self.assertEqual(rc, 0, out)
        self.assertEqual(o["dockerfile"], "app/Dockerfile")

    def test_line_break_in_path_is_rejected(self):
        rc, out, o = run_prepare(DOCKERFILE_PATH="a\nb")
        self.assertEqual(rc, 1, out)
        self.assertEqual(o, {})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd /Users/invicto/Documents/Startup/docker-push-webhook-action && python3 -m unittest -v test_prepare 2>&1 | tail -15`
Expected: falhas (`prepare.sh` não existe: `bash: .../prepare.sh: No such file or directory`, `rc` 127).

- [ ] **Step 3: Implementar `prepare.sh`**

Create `prepare.sh`:

```bash
#!/usr/bin/env bash
# Validates and normalizes the action inputs, then writes the step outputs.
# Every input arrives as an environment variable; nothing is interpolated into code.
set -euo pipefail

fail() {
  echo "::error::$1"
  exit 1
}

missing_secret() {
  fail "O secret $1 está vazio ou não existe. Cadastre em Settings > Secrets and variables > Actions, na aba Secrets (não em Variables), com esse nome exato. Se você usou outro nome de secret, ajuste o workflow."
}

if [[ -z "${REGISTRY_URL//[[:space:]]/}" ]]; then missing_secret REGISTRY_URL; fi
if [[ -z "$REGISTRY_USERNAME" ]]; then missing_secret REGISTRY_USERNAME; fi
if [[ -z "$REGISTRY_PASSWORD" ]]; then missing_secret REGISTRY_PASSWORD; fi

# Registry: accept what people paste (spaces, scheme, trailing slash, capitals).
registry="$(printf '%s' "$REGISTRY_URL" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
registry="${registry#http://}"
registry="${registry#https://}"
while [[ "$registry" == */ ]]; do registry="${registry%/}"; done
registry_re='^[a-z0-9][a-z0-9._:/-]*$'
if [[ ! "$registry" =~ $registry_re ]]; then
  fail "O endereço do registry (REGISTRY_URL) é inválido. Use só o endereço, por exemplo: registry.empresa.com"
fi

# Name: defaults to the repository name. Docker rejects capital letters.
name="$NAME"
if [[ -z "$name" ]]; then name="${GITHUB_REPOSITORY#*/}"; fi
if [[ -z "$name" || "$name" =~ [[:space:][:cntrl:]] ]]; then
  fail "O campo 'name' não pode ser vazio nem ter espaços ou quebras de linha."
fi
lower="$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]')"
if [[ "$lower" != "$name" ]]; then
  echo "::notice::O Docker não aceita letras maiúsculas no nome da imagem; usando '$lower'."
  name="$lower"
fi
if [[ "${name##*/}" == *[:@]* ]]; then
  fail "O campo 'name' não pode ter tag nem digest (:tag ou @sha256). Use o campo 'tags'."
fi
image="$registry/$name"

dockerfile="${DOCKERFILE_PATH:-$BUILD_CONTEXT}"
if [[ -d "$dockerfile" ]]; then
  dockerfile="${dockerfile%/}/Dockerfile"
fi
if [[ "$dockerfile" == *[$'\r\n']* ]]; then
  fail "'dockerfile_path' e 'context' não podem ter quebras de linha."
fi
if [[ ! -f "$dockerfile" ]]; then
  fail "Dockerfile não encontrado: $dockerfile. Ajuste 'context' ou 'dockerfile_path' no workflow."
fi

tags="$TAGS"
if [[ -z "${tags//[[:space:]]/}" ]]; then tags="latest,$GITHUB_SHA"; fi
tag_re='^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$'
refs=()
count=0
while read -r tag; do
  if [[ -z "$tag" ]]; then continue; fi
  if [[ ! "$tag" =~ $tag_re ]]; then
    fail "Tag inválida: '$tag'. Use letras, números, '_', '.' e '-' (máximo 128 caracteres)."
  fi
  refs+=("$image:$tag")
  count=$((count + 1))
done < <(printf '%s\n' "$tags" | tr ',' '\n')
if (( count == 0 )); then fail "Nenhuma tag informada."; fi

docker_config="$(mktemp -d "${RUNNER_TEMP:-/tmp}/docker-config.XXXXXX")"
{
  echo "registry=$registry"
  echo "registry_host=${registry%%/*}"
  echo "dockerfile=$dockerfile"
  echo "docker_config=$docker_config"
  echo "refs<<EOF"
  printf '%s\n' "${refs[@]}"
  echo "EOF"
} >> "$GITHUB_OUTPUT"
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd /Users/invicto/Documents/Startup/docker-push-webhook-action && python3 -m unittest -v test_prepare 2>&1 | tail -30`
Expected: todos os testes `ok`, `OK` no final. Se algum falhar por comportamento do Bash 3.2, corrija em `prepare.sh` (respeitando as Global Constraints) e rode de novo.

- [ ] **Step 5: Atualizar o job `unit` do CI para descobrir todos os testes**

Em `.github/workflows/test.yml`, trocar a linha:

```yaml
      - run: python3 -m unittest -v test_webhook
```

por:

```yaml
      - run: python3 -m unittest -v
```

(o discovery padrão encontra todo `test_*.py` na raiz).

- [ ] **Step 6: Commit**

```bash
cd /Users/invicto/Documents/Startup/docker-push-webhook-action
git add prepare.sh test_prepare.py .github/workflows/test.yml
git commit -m "feat: prepare.sh validates and normalizes inputs with pt-BR errors

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `action.yml` v2, resumo da execução e e2e

**Files:**
- Modify: `action.yml` (reescrita completa), `.github/workflows/test.yml` (job `e2e`)

**Interfaces:**
- Consumes: outputs do `prepare.sh` (Task 1): `registry`, `dockerfile`, `docker_config`, `refs`.
- Produces: inputs v2 (`registry_url`*, `registry_username`*, `registry_password`*, `name`, `tags`, `context`, `dockerfile_path`, `build_args`, `webhook_url`, `webhook_headers`, `webhook_payload`, `webhook_fail_on_error`; `*` = `required: true`) e outputs `image`, `tags`, `digest` (iguais à v1). `test_docs.py` (Task 3) lê estes nomes.

- [ ] **Step 1: Reescrever `action.yml`**

Overwrite `action.yml` with:

```yaml
name: Docker Push & Webhook
description: Build and push a Docker image with the native Docker CLI, then optionally call a webhook. No third-party actions.
author: Victo Hugo
branding:
  icon: upload-cloud
  color: blue

inputs:
  registry_url:
    description: Registry host (e.g. registry.example.com, ghcr.io, docker.io). Scheme and trailing slash are removed automatically.
    required: true
  registry_username:
    description: Registry username.
    required: true
  registry_password:
    description: Registry password or token. Sent via stdin, never as a command argument.
    required: true
  name:
    description: Short image name, without registry or tag (e.g. my-app or team/my-app). Defaults to the repository name. Lowercased automatically.
    required: false
    default: ''
  tags:
    description: Tags separated by commas and/or newlines. Empty means "latest" plus the commit SHA.
    required: false
    default: ''
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
        REGISTRY_URL: ${{ inputs.registry_url }}
        REGISTRY_USERNAME: ${{ inputs.registry_username }}
        REGISTRY_PASSWORD: ${{ inputs.registry_password }}
        NAME: ${{ inputs.name }}
        TAGS: ${{ inputs.tags }}
        BUILD_CONTEXT: ${{ inputs.context }}
        DOCKERFILE_PATH: ${{ inputs.dockerfile_path }}
      run: |
        set -euo pipefail
        for bin in docker python3; do
          if ! command -v "$bin" >/dev/null; then
            echo "::error::'$bin' não encontrado. Use um runner Linux com Docker (por exemplo, ubuntu-latest)."
            exit 1
          fi
        done
        bash "$GITHUB_ACTION_PATH/prepare.sh"

    - name: Log in to registry
      shell: bash
      env:
        DOCKER_CONFIG: ${{ steps.prepare.outputs.docker_config }}
        REGISTRY: ${{ steps.prepare.outputs.registry_host }}
        REGISTRY_USERNAME: ${{ inputs.registry_username }}
        REGISTRY_PASSWORD: ${{ inputs.registry_password }}
      run: |
        set -euo pipefail
        while IFS= read -r line; do
          if [[ -n "$line" ]]; then echo "::add-mask::$line"; fi
        done <<< "$REGISTRY_PASSWORD"
        printf '%s' "$REGISTRY_PASSWORD" | docker login "$REGISTRY" -u "$REGISTRY_USERNAME" --password-stdin

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
            echo "::error::build_args: cada linha deve ser CHAVE=VALOR."
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
          echo "::warning::Não consegui ler o digest da saída do 'docker push'."
        fi
        {
          echo "image=${REFS%%$'\n'*}"
          echo "digest=$digest"
          echo "tags<<EOF"
          echo "$REFS"
          echo "EOF"
        } >> "$GITHUB_OUTPUT"
        if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
          {
            echo "### Imagem publicada"
            echo
            while read -r ref; do echo "- \`$ref\`"; done <<< "$REFS"
            echo
            echo "Digest: \`${digest:-indisponível}\`"
          } >> "$GITHUB_STEP_SUMMARY"
        fi

    - name: Call webhook
      if: inputs.webhook_url != ''
      shell: bash
      env:
        WEBHOOK_URL: ${{ inputs.webhook_url }}
        WEBHOOK_HEADERS: ${{ inputs.webhook_headers }}
        WEBHOOK_PAYLOAD: ${{ inputs.webhook_payload }}
        WEBHOOK_FAIL_ON_ERROR: ${{ inputs.webhook_fail_on_error }}
      run: python3 -I "$GITHUB_ACTION_PATH/webhook.py"

    - name: Clean up credentials
      if: always() && steps.prepare.outputs.docker_config != ''
      shell: bash
      env:
        DOCKER_CONFIG: ${{ steps.prepare.outputs.docker_config }}
        REGISTRY: ${{ steps.prepare.outputs.registry_host }}
      run: |
        set -uo pipefail
        docker logout "$REGISTRY" || true
        rm -rf "$DOCKER_CONFIG"
```

- [ ] **Step 2: Validar a sintaxe do YAML**

Run: `cd /Users/invicto/Documents/Startup/docker-push-webhook-action && python3 -c "import yaml; d=yaml.safe_load(open('action.yml')); print(sorted(d['inputs']))"`
Expected: lista com `build_args, context, dockerfile_path, name, registry_password, registry_url, registry_username, tags, webhook_fail_on_error, webhook_headers, webhook_payload, webhook_url` (sem `image`).

- [ ] **Step 3: Atualizar o job `e2e` em `.github/workflows/test.yml`**

No job `e2e`, aplicar estas mudanças (o restante do arquivo — registry com auth, listener do webhook, "Assert push", "Assert webhook" — fica como está, exceto o que segue):

3a. No step `id: action`, trocar `registry_url: localhost:5000` e `image: localhost:5000/e2e` por (também exercita a normalização do host):

```yaml
          registry_url: http://localhost:5000/
          registry_username: test
          registry_password: test-pass
          name: e2e
```

(manter `tags`, `dockerfile_path`, `build_args` e os `webhook_*` desse step como estão; remover a linha `image:`.)

3b. Substituir os steps `bad_tag`, "Assert invalid tag fails…", `injected_image` e "Assert image with line breaks is rejected" (do `id: bad_tag` até o fim do arquivo) por:

```yaml
      - id: bad_tag
        uses: ./
        continue-on-error: true
        with:
          registry_url: localhost:5000
          registry_username: test
          registry_password: test-pass
          name: e2e
          dockerfile_path: test
          tags: bad tag!
          webhook_url: http://127.0.0.1:8080/deploy

      - id: missing_registry
        uses: ./
        continue-on-error: true
        with:
          registry_url: ''
          registry_username: test
          registry_password: test-pass
          name: e2e
          dockerfile_path: test
          webhook_url: http://127.0.0.1:8080/deploy

      - name: Assert bad config fails without calling the webhook
        env:
          BAD_TAG: ${{ steps.bad_tag.outcome }}
          MISSING_REGISTRY: ${{ steps.missing_registry.outcome }}
        run: |
          [[ "$BAD_TAG" == "failure" ]]
          [[ "$MISSING_REGISTRY" == "failure" ]]
          [[ ! -e "$RUNNER_TEMP/webhook.json" ]]

      - id: injected_name
        uses: ./
        continue-on-error: true
        with:
          registry_url: localhost:5000
          registry_username: test
          registry_password: test-pass
          name: "e2e\nEOF\nrefs<<EOF\nlocalhost:5000/e2e:injected\nlocalhost:5000/e2e"
          dockerfile_path: test
          build_args: GREETING=x
          tags: v9

      - name: Assert name with line breaks is rejected
        env:
          OUTCOME: ${{ steps.injected_name.outcome }}
        run: '[[ "$OUTCOME" == "failure" ]]'

      - id: defaults
        uses: ./
        with:
          registry_url: localhost:5000
          registry_username: test
          registry_password: test-pass
          dockerfile_path: test
          build_args: GREETING=defaults

      - id: uppercase_name
        uses: ./
        with:
          registry_url: localhost:5000
          registry_username: test
          registry_password: test-pass
          name: E2E-Upper
          dockerfile_path: test
          build_args: GREETING=upper
          tags: v1

      - name: Assert default name, default tags and lowercased name
        env:
          SHA: ${{ github.sha }}
          REPO: ${{ github.event.repository.name }}
        run: |
          set -euo pipefail
          repo="$(printf '%s' "$REPO" | tr '[:upper:]' '[:lower:]')"
          echo test-pass | docker login localhost:5000 -u test --password-stdin
          docker pull "localhost:5000/$repo:latest"
          docker pull "localhost:5000/$repo:$SHA"
          docker pull localhost:5000/e2e-upper:v1
```

- [ ] **Step 4: Validar o workflow de teste com `actionlint`**

Run: `cd /Users/invicto/Documents/Startup/docker-push-webhook-action && actionlint .github/workflows/test.yml && echo lint-ok`
Expected: `lint-ok`.

- [ ] **Step 5: Commit e rodar o CI (o e2e só roda lá)**

```bash
cd /Users/invicto/Documents/Startup/docker-push-webhook-action
git add action.yml .github/workflows/test.yml
git commit -m "feat!: v2 interface (name instead of image, required registry_url, job summary)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push origin main
export GH_TOKEN=$(gh auth token -u invicto-dev)
sleep 20; gh run list -R invicto-dev/docker-push-webhook-action -L 1
```

Pedir confirmação ao usuário antes do `git push` (ação externa). Depois acompanhar com `gh run watch <id> -R invicto-dev/docker-push-webhook-action --exit-status`.
Expected: jobs `unit` e `e2e` verdes. Se o `e2e` falhar, ler o log (`gh run view <id> --log-failed`), corrigir a causa raiz e repetir.

- [ ] **Step 6: Conferir o resumo da execução na interface**

Abrir a run no GitHub (aba Actions) e confirmar que o job `e2e` mostra a seção "Imagem publicada" com as imagens e o digest (o resumo não é testável por script; checagem manual única).

---

### Task 3: Template, `INSTALAR.md`, `llms.txt`, README e teste de consistência

**Files:**
- Create: `templates/docker.yml`, `INSTALAR.md`, `llms.txt`, `test_docs.py`
- Modify: `README.md` (reescrita completa)

**Interfaces:**
- Consumes: inputs e `required` do `action.yml` (Task 2); mensagens `está vazio ou não existe` e `Dockerfile não encontrado` do `prepare.sh` (Task 1).
- Produces: de `test_docs.py`, a função `check_workflow(test, text, where)` que `test_install.py` (Task 4) importa; e as constantes `SECRETS`.

- [ ] **Step 1: Escrever `test_docs.py` (falha: arquivos ainda não existem)**

Create `test_docs.py`:

```python
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ACTION_REF = "docker-push-webhook-action@v2"
SECRETS = {
    "REGISTRY_URL",
    "REGISTRY_USERNAME",
    "REGISTRY_PASSWORD",
    "DEPLOY_WEBHOOK_URL",
    "DEPLOY_TOKEN",
    "GITHUB_TOKEN",
}
# Messages that prepare.sh prints and the docs explain; they must match.
SHARED_MESSAGES = ["está vazio ou não existe", "Dockerfile não encontrado"]
# Errors from outside the action that the docs also explain.
DOC_ERRORS = ["unauthorized", "COPY failed"]


def action_inputs(action_yml):
    """Returns {input name: required?} from the top-level `inputs:` block."""
    block = action_yml.split("\ninputs:\n", 1)[1].split("\noutputs:\n", 1)[0] + "\n"
    inputs = {}
    for m in re.finditer(r"^  ([a-z_]+):\n((?:    .*\n)+)", block, re.M):
        inputs[m.group(1)] = "required: true" in m.group(2)
    return inputs


def step_inputs(workflow_text):
    """Input names passed to the action. The action step must be the last step."""
    tail = workflow_text.split("docker-push-webhook-action@", 1)[1]
    after_with = tail.split("with:\n", 1)[1]
    return set(re.findall(r"^\s+([a-z_]+):", after_with, re.M))


def check_workflow(test, text, where):
    inputs = action_inputs((ROOT / "action.yml").read_text())
    used = step_inputs(text)
    unknown = used - set(inputs)
    test.assertFalse(unknown, f"{where}: inputs que não existem no action.yml: {unknown}")
    required = {k for k, v in inputs.items() if v}
    test.assertFalse(required - used, f"{where}: faltam inputs obrigatórios: {required - used}")
    secrets = set(re.findall(r"secrets\.([A-Z_]+)", text))
    test.assertFalse(secrets - SECRETS, f"{where}: secrets desconhecidos: {secrets - SECRETS}")
    test.assertIn(ACTION_REF, text, where)
    test.assertNotIn("vars.", text, f"{where}: credenciais/endereço não devem vir de vars.*")


def yaml_blocks_using_action(markdown):
    blocks = re.findall(r"```yaml\n(.*?)```", markdown, re.S)
    return [b for b in blocks if "docker-push-webhook-action@" in b]


class DocsConsistencyTest(unittest.TestCase):
    def test_template(self):
        check_workflow(self, (ROOT / "templates/docker.yml").read_text(), "templates/docker.yml")

    def test_markdown_examples(self):
        for name in ("INSTALAR.md", "llms.txt", "README.md"):
            blocks = yaml_blocks_using_action((ROOT / name).read_text())
            self.assertTrue(blocks, f"{name}: nenhum exemplo yaml da action")
            for i, block in enumerate(blocks):
                check_workflow(self, block, f"{name} bloco {i + 1}")

    def test_error_tables_match_the_script(self):
        script = (ROOT / "prepare.sh").read_text()
        for message in SHARED_MESSAGES:
            self.assertIn(message, script, f"prepare.sh não imprime mais: {message}")
        for name in ("INSTALAR.md", "llms.txt"):
            text = (ROOT / name).read_text()
            for message in SHARED_MESSAGES + DOC_ERRORS:
                self.assertIn(message, text, f"{name} não explica o erro: {message}")

    def test_docs_state_secret_scope_rule(self):
        for name in ("INSTALAR.md", "llms.txt"):
            text = (ROOT / name).read_text()
            self.assertIn("por repositório", text, name)
            self.assertIn("organização", text, name)


if __name__ == "__main__":
    unittest.main()
```

Run: `cd /Users/invicto/Documents/Startup/docker-push-webhook-action && python3 -m unittest -v test_docs 2>&1 | tail -8`
Expected: falha (`FileNotFoundError: templates/docker.yml`).

- [ ] **Step 2: Criar `templates/docker.yml`**

```yaml
# Este arquivo publica (envia) a imagem Docker do projeto para o registry da empresa.
#
# ANTES DE USAR, cadastre estes secrets neste repositório, em:
#   Settings > Secrets and variables > Actions > aba "Secrets"
#   (cadastre como SECRET, não como Variable, e use os nomes exatos)
#
#     REGISTRY_URL        -> endereço do registry. Ex: registry.empresa.com
#     REGISTRY_USERNAME   -> usuário do registry
#     REGISTRY_PASSWORD   -> senha ou token do registry
#     DEPLOY_WEBHOOK_URL  -> (opcional) endereço avisado após o envio, para
#                            fazer deploy automático. Sem ele, nada é avisado.

name: Docker
run-name: Publicar imagem Docker

# QUANDO roda:
on:
  push:
    branches: [main]   # automaticamente, sempre que algo novo entrar na branch "main"
  workflow_dispatch:   # ou manualmente: aba Actions > Docker > Run workflow

jobs:
  publish:
    runs-on: ubuntu-latest  # máquina Linux do GitHub que faz o trabalho
    permissions:
      contents: read        # só precisa LER o código do repositório

    # PARA PUBLICAR MAIS DE UMA IMAGEM (ex.: backend e front no mesmo repositório):
    # descomente o bloco "strategy" abaixo, ajuste os valores e, no passo final,
    # descomente as 3 linhas marcadas com "matrix".
    #
    # strategy:
    #   matrix:
    #     include:
    #       - name: meu-backend          # nome da imagem
    #         context: .                 # pasta usada para montar a imagem
    #         dockerfile: Dockerfile     # "receita" da imagem
    #       - name: meu-front
    #         context: front
    #         dockerfile: front/Dockerfile

    steps:
      # Passo 1: baixa o código do repositório
      - uses: actions/checkout@v4

      # Passo 2: monta a imagem, envia para o registry e (opcional) avisa o webhook.
      # Por padrão a imagem tem o nome do repositório e as tags "latest" e o
      # código do commit. Para mudar o nome: adicione  name: meu-app
      - uses: invicto-dev/docker-push-webhook-action@v2
        with:
          registry_url: ${{ secrets.REGISTRY_URL }}
          registry_username: ${{ secrets.REGISTRY_USERNAME }}
          registry_password: ${{ secrets.REGISTRY_PASSWORD }}
          webhook_url: ${{ secrets.DEPLOY_WEBHOOK_URL }}
          # name: ${{ matrix.name }}                  # matrix
          # context: ${{ matrix.context }}            # matrix
          # dockerfile_path: ${{ matrix.dockerfile }} # matrix
```

- [ ] **Step 3: Criar `INSTALAR.md`**

````markdown
# Instalar o build e push de Docker em um repositório

Este guia é para quem **não conhece CI/CD**. Siga os passos na ordem. Leva uns 5 minutos.

Você precisa ter: acesso de escrita ao repositório no GitHub, e saber o endereço, o usuário e a senha (ou token) do registry da empresa.

## Caminho A: pelo navegador (sem instalar nada)

### 1. Cadastre os secrets

No repositório: **Settings > Secrets and variables > Actions**, aba **Secrets** (não a aba Variables), botão **New repository secret**. Crie estes, com o nome **exatamente** assim:

| Nome | Valor |
|---|---|
| `REGISTRY_URL` | endereço do registry, por exemplo `registry.empresa.com` |
| `REGISTRY_USERNAME` | usuário do registry |
| `REGISTRY_PASSWORD` | senha ou token do registry |
| `DEPLOY_WEBHOOK_URL` | (opcional) endereço avisado depois do envio, para fazer deploy |

Pode colar o endereço do registry como copiou (com `https://` ou barra no final): a action limpa isso.

### 2. Crie o arquivo do workflow

No repositório, clique em **Add file > Create new file**. No nome do arquivo, digite `.github/workflows/docker.yml`. Cole dentro o conteúdo de [templates/docker.yml](templates/docker.yml) e clique em **Commit changes**.

### 3. Rode e confira

Abra a aba **Actions**, escolha **Docker** na lista da esquerda e clique em **Run workflow**. Quando ficar verde, a imagem está no registry.

## Caminho B: pelo terminal (um comando)

Precisa do [GitHub CLI](https://cli.github.com) (`brew install gh`) logado (`gh auth login`). Na raiz do repositório:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/invicto-dev/docker-push-webhook-action/v2/install.sh)
```

O script acha os Dockerfiles, pergunta o endereço, o usuário e a senha do registry (a senha não aparece na tela), cadastra os secrets, cria `.github/workflows/docker.yml` e pergunta se pode fazer commit e push. Quer ler antes de rodar? Baixe o arquivo `install.sh` e abra no editor: ele é curto.

Em seguida, confira o campo `context` de cada imagem no arquivo criado: se o build falhar procurando arquivos, troque por `.`.

## Secrets por repositório ou por organização

Por padrão, os secrets são cadastrados **por repositório** (é o que os dois caminhos acima fazem). Cadastrar uma vez só na **organização** (`bash install.sh --org NOME-DA-ORG`) evita repetir em cada repositório, **mas só funciona em repositórios privados se a organização tiver plano pago**. No plano gratuito, secrets de organização só chegam a repositórios públicos. O script avisa quando detecta essa combinação.

## Deu erro? Tabela rápida

| Mensagem no log | O que significa | O que fazer |
|---|---|---|
| `O secret REGISTRY_URL está vazio ou não existe` (ou `REGISTRY_USERNAME`, `REGISTRY_PASSWORD`) | O secret não foi cadastrado, foi cadastrado como Variable, ou o nome está diferente | Cadastre na aba **Secrets** com o nome exato |
| `unauthorized: incorrect username or password` | Usuário ou senha do registry errados | Confira com `docker login <endereço>` no seu computador e recadastre os secrets |
| `Dockerfile não encontrado: ...` | A action não achou o Dockerfile | Ajuste `context` e `dockerfile_path` no workflow para o caminho certo |
| `COPY failed: file not found` (no build) | O `context` está apontando para a pasta errada | Troque `context` por `.` (raiz do repositório) |
| `Tag inválida: ...` | Uma tag tem espaço ou caractere não permitido | Use só letras, números, `_`, `.` e `-` |
| O workflow não aparece em **Actions** | O arquivo está no lugar errado | Ele precisa estar em `.github/workflows/docker.yml` na branch `main`, e o Actions deve estar habilitado em Settings > Actions |
````

- [ ] **Step 4: Criar `llms.txt`**

````markdown
# docker-push-webhook-action

Composite GitHub Action. Faz login num registry Docker, build e push de uma imagem com o Docker CLI nativo e, opcionalmente, chama um webhook (ex.: deploy). Uso: `invicto-dev/docker-push-webhook-action@v2`.

Quem pede ajuda costuma ser leigo em CI/CD. Seja literal: use os nomes exatos abaixo, não invente inputs, explique cada passo em português.

## Instalar em um repositório

Caminho 1 (terminal, recomendado se a pessoa tem `gh` logado): na raiz do repositório, rode `bash <(curl -fsSL https://raw.githubusercontent.com/invicto-dev/docker-push-webhook-action/v2/install.sh)` e deixe a pessoa responder as perguntas (endereço, usuário e senha do registry). Não responda a senha por ela.

Caminho 2 (sem terminal): peça à pessoa para cadastrar os secrets na interface do GitHub e crie o arquivo `.github/workflows/docker.yml` com o conteúdo abaixo.

## Secrets (cadastrar na aba Secrets, nunca em Variables)

Cadastro **por repositório** (padrão): Settings > Secrets and variables > Actions > Secrets.

- `REGISTRY_URL`: endereço do registry, ex. `registry.empresa.com` (para Docker Hub, `docker.io`)
- `REGISTRY_USERNAME`: usuário
- `REGISTRY_PASSWORD`: senha ou token
- `DEPLOY_WEBHOOK_URL`: opcional

Cadastro por **organização** só funciona em repositórios privados com plano pago; no plano gratuito vale apenas para repositórios públicos. Na dúvida, cadastre por repositório.

## Workflow mínimo (`.github/workflows/docker.yml`)

```yaml
name: Docker
run-name: Publicar imagem Docker
on:
  push:
    branches: [main]
  workflow_dispatch:
jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: invicto-dev/docker-push-webhook-action@v2
        with:
          registry_url: ${{ secrets.REGISTRY_URL }}
          registry_username: ${{ secrets.REGISTRY_USERNAME }}
          registry_password: ${{ secrets.REGISTRY_PASSWORD }}
          webhook_url: ${{ secrets.DEPLOY_WEBHOOK_URL }}
```

Para vários Dockerfiles, use `strategy.matrix.include` com `name`, `context` e `dockerfile`, e passe `name: ${{ matrix.name }}`, `context: ${{ matrix.context }}`, `dockerfile_path: ${{ matrix.dockerfile }}` à action. Modelo completo em `templates/docker.yml`.

## Inputs

| Input | Obrigatório | Padrão | Descrição |
|---|---|---|---|
| `registry_url` | sim | | Endereço do registry. `https://` e `/` final são removidos |
| `registry_username` | sim | | Usuário |
| `registry_password` | sim | | Senha ou token (vai por stdin) |
| `name` | não | nome do repositório | Nome curto da imagem, sem registry nem tag; vira minúsculo |
| `tags` | não | `latest` e o SHA do commit | Separadas por vírgula ou quebra de linha |
| `context` | não | `.` | Contexto do build |
| `dockerfile_path` | não | `<context>/Dockerfile` | Arquivo ou pasta do Dockerfile |
| `build_args` | não | | Um `CHAVE=VALOR` por linha. Nunca coloque segredos aqui |
| `webhook_url` | não | | POST após o push; vazio pula |
| `webhook_headers` | não | `{}` | Objeto JSON de strings |
| `webhook_payload` | não | | Corpo enviado como está |
| `webhook_fail_on_error` | não | `true` | Falha o job se o webhook falhar |

A imagem final é `<registry_url>/<name>:<tag>`. Outputs: `image`, `tags`, `digest`.

## Regras para a IA

- Nunca escreva senha, token ou endereço de registry direto no YAML. Sempre `${{ secrets.NOME }}`.
- Nunca use `vars.*` (Variables) para `REGISTRY_URL` ou credenciais.
- Use sempre `@v2`. Não use `image:` (era da v1; não existe mais). Use `name:`.
- Não invente inputs além da tabela acima.
- Não rode `git push` sem a pessoa pedir.

## Erros comuns

| Mensagem | Causa | Correção |
|---|---|---|
| `O secret REGISTRY_URL está vazio ou não existe` (ou `REGISTRY_USERNAME`, `REGISTRY_PASSWORD`) | Secret não cadastrado, cadastrado como Variable, ou nome diferente | Cadastrar na aba Secrets com o nome exato |
| `unauthorized: incorrect username or password` | Credenciais erradas | Testar com `docker login <endereço>` e recadastrar |
| `Dockerfile não encontrado: ...` | Caminho errado | Ajustar `context` / `dockerfile_path` |
| `COPY failed: file not found` | `context` errado | Usar `context: .` |
| `Tag inválida: ...` | Caractere não permitido na tag | Letras, números, `_`, `.`, `-` |
````

- [ ] **Step 5: Reescrever `README.md`**

````markdown
# Docker Push & Webhook

Build and push a Docker image to your registry with the native Docker CLI, then optionally call a webhook (for example, to trigger a deploy). No third-party actions, no extra dependencies.

> **New to CI/CD?** Follow [INSTALAR.md](INSTALAR.md) (Portuguese, step by step) or run [install.sh](install.sh). Using an AI assistant? Point it to [llms.txt](llms.txt).

## Requirements

A Linux runner with Docker and Python 3, such as `ubuntu-latest`. GitHub-hosted macOS and Windows runners do not ship Docker.

## Usage

### Minimal

```yaml
- uses: actions/checkout@v4
- uses: invicto-dev/docker-push-webhook-action@v2
  with:
    registry_url: ${{ secrets.REGISTRY_URL }}
    registry_username: ${{ secrets.REGISTRY_USERNAME }}
    registry_password: ${{ secrets.REGISTRY_PASSWORD }}
```

Pushes `<registry_url>/<repository name>` with the tags `latest` and the commit SHA.

### Docker Hub

```yaml
- uses: actions/checkout@v4
- uses: invicto-dev/docker-push-webhook-action@v2
  with:
    registry_url: docker.io
    registry_username: ${{ secrets.REGISTRY_USERNAME }}
    registry_password: ${{ secrets.REGISTRY_PASSWORD }}
    name: myuser/myapp
```

### Several images and a deploy webhook

```yaml
jobs:
  publish:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        include:
          - name: backend
            context: .
            dockerfile: Dockerfile
          - name: front
            context: front
            dockerfile: front/Dockerfile
    steps:
      - uses: actions/checkout@v4
      - uses: invicto-dev/docker-push-webhook-action@v2
        with:
          registry_url: ${{ secrets.REGISTRY_URL }}
          registry_username: ${{ secrets.REGISTRY_USERNAME }}
          registry_password: ${{ secrets.REGISTRY_PASSWORD }}
          name: ${{ matrix.name }}
          context: ${{ matrix.context }}
          dockerfile_path: ${{ matrix.dockerfile }}
          build_args: |
            APP_VERSION=${{ github.sha }}
          webhook_url: ${{ secrets.DEPLOY_WEBHOOK_URL }}
          webhook_headers: '{"Authorization": "Bearer ${{ secrets.DEPLOY_TOKEN }}"}'
          webhook_payload: '{"image": "${{ matrix.name }}"}'
```

## Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `registry_url` | yes | | Registry host (e.g. `registry.example.com`, `ghcr.io`, `docker.io`). Scheme and trailing slash are removed. |
| `registry_username` | yes | | Registry username. |
| `registry_password` | yes | | Registry password or token. Sent via stdin. |
| `name` | no | repository name | Short image name without registry or tag (e.g. `my-app`, `team/my-app`). Lowercased automatically. |
| `tags` | no | `latest` and the commit SHA | Tags separated by commas and/or newlines. |
| `context` | no | `.` | Build context. |
| `dockerfile_path` | no | `<context>/Dockerfile` | Dockerfile path, or a directory containing one. |
| `build_args` | no | `''` | One `KEY=VALUE` per line. |
| `webhook_url` | no | `''` | URL to POST to after a successful push. Empty skips the webhook. |
| `webhook_headers` | no | `{}` | JSON object of string headers. Values are masked in logs. |
| `webhook_payload` | no | `''` | Request body, sent exactly as given. `Content-Type` defaults to `application/json`. |
| `webhook_fail_on_error` | no | `true` | Fail the job on network errors, timeouts or non-2xx responses. Invalid `webhook_url`/`webhook_headers` always fail. |

The pushed image is `<registry_url>/<name>:<tag>`. Validation errors are written in Brazilian Portuguese and say which secret or field to fix.

## Outputs

| Output | Description |
|---|---|
| `image` | First pushed reference (`image:tag`). |
| `tags` | All pushed references, one per line. |
| `digest` | Digest of the pushed image (`sha256:...`). |

A summary with the pushed references and the digest is also added to the job summary.

## Webhook behavior

- Always `POST`, 30-second timeout. Redirects are not followed and count as failures.
- The webhook runs only after a successful push and cannot reference this action's own outputs. Image and tags are already known to the caller. The digest is not available inside the payload.
- URL, headers, payload and response body are never printed in the logs.
- `webhook_url` must not embed credentials (`https://user:pass@host`); send them in `webhook_headers` instead.

## Security notes

- **Do not pass secrets in `build_args`.** Build args are stored in the image history.
- Registry credentials are kept in a temporary Docker config that is deleted at the end of the action, even on failure. Your job's default Docker config is never touched.
  As a consequence, registry logins made earlier in the job (for example, for a private base image in another registry) and per-user CLI plugins in `~/.docker/cli-plugins` are not visible during the build.
- Inputs are passed to scripts as environment variables, never interpolated into shell code.
- `install.sh` sends the password to `gh secret set` through stdin; it never appears in arguments or on screen.

## Upgrading from v1

`image` was removed. Replace `image: ghcr.io/org/app` with `registry_url: ghcr.io` and `name: org/app`. `registry_url` is now required (use `docker.io` for Docker Hub).

## License

MIT
````

- [ ] **Step 6: Rodar os testes de consistência**

Run: `cd /Users/invicto/Documents/Startup/docker-push-webhook-action && python3 -m unittest -v test_docs 2>&1 | tail -15`
Expected: 4 testes `ok`. Se `test_markdown_examples` falhar por um exemplo de README/INSTALAR, corrija o exemplo (não relaxe o teste).

- [ ] **Step 7: Commit**

```bash
cd /Users/invicto/Documents/Startup/docker-push-webhook-action
git add templates/docker.yml INSTALAR.md llms.txt README.md test_docs.py
git commit -m "docs: template, INSTALAR.md, llms.txt and consistency test for v2

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `install.sh` e seus testes

**Files:**
- Create: `install.sh`, `test_install.py`
- Modify: `.github/workflows/test.yml` (job `unit`: instalar `actionlint`)

**Interfaces:**
- Consumes: `check_workflow(test, text, where)` de `test_docs.py` (Task 3); template `templates/docker.yml` como referência de formato.
- Produces: `install.sh [--org ORG]`. Ordem exata das perguntas lidas do stdin: (1) confirmação de sobrescrever workflow existente (só se existir); (2) confirmação do aviso de `--org` (só com `--org` em plano gratuito + repo privado); (3) endereço do registry; (4) usuário; (5) senha; (6) URL do webhook (vazio pula); (7) confirmação de commit e push. Confirmações aceitam `s/S/y/Y`; EOF ou outra resposta = não.

- [ ] **Step 1: Escrever `test_install.py` (falha: `install.sh` não existe)**

```python
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_docs import check_workflow

ROOT = Path(__file__).resolve().parent
INSTALL = ROOT / "install.sh"

FAKE_GH = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_GH_LOG"
case "$1 $2" in
  "auth status") exit 0 ;;
  "repo view")
    case "$*" in
      *isPrivate*) echo "${FAKE_GH_PRIVATE:-true}" ;;
      *) echo "acme/app" ;;
    esac ;;
  "secret set") printf '%s=%s\\n' "$3" "$(cat)" >> "$FAKE_GH_SECRETS" ;;
  api*) echo "${FAKE_GH_PLAN:-free}" ;;
  *) exit 1 ;;
esac
"""

PASSWORD = "p@ss \"w$rd'!"


class Sandbox:
    def __init__(self, files=("Dockerfile",), existing_workflow=None, env=None):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        self.log = self.tmp / "gh.log"
        self.secrets = self.tmp / "secrets"
        self.log.touch()
        self.secrets.touch()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        for name in files:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("FROM scratch\n")
        if existing_workflow is not None:
            wf = self.repo / ".github/workflows/docker.yml"
            wf.parent.mkdir(parents=True)
            wf.write_text(existing_workflow)
        self.env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(self.tmp),
            "FAKE_GH_LOG": str(self.log),
            "FAKE_GH_SECRETS": str(self.secrets),
        }
        self.env.update(env or {})

    def run(self, answers, *args):
        proc = subprocess.run(
            ["bash", str(INSTALL), *args],
            cwd=self.repo,
            env=self.env,
            input=answers,
            capture_output=True,
            text=True,
        )
        self.stdout = proc.stdout + proc.stderr
        return proc.returncode

    @property
    def workflow(self):
        return (self.repo / ".github/workflows/docker.yml").read_text()

    def recorded_secrets(self):
        return dict(l.split("=", 1) for l in self.secrets.read_text().splitlines())

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def answers(host="registry.acme.com", user="ci", password=PASSWORD, hook="", push="n"):
    return f"{host}\n{user}\n{password}\n{hook}\n{push}\n"


class InstallTest(unittest.TestCase):
    def sandbox(self, **kw):
        sb = Sandbox(**kw)
        self.addCleanup(sb.cleanup)
        return sb

    def test_single_root_dockerfile(self):
        sb = self.sandbox()
        self.assertEqual(sb.run(answers()), 0, sb.stdout)
        check_workflow(self, sb.workflow, "workflow gerado")
        self.assertNotIn("matrix", sb.workflow)
        self.assertEqual(
            sb.recorded_secrets(),
            {
                "REGISTRY_URL": "registry.acme.com",
                "REGISTRY_USERNAME": "ci",
                "REGISTRY_PASSWORD": PASSWORD,
            },
        )

    def test_password_with_special_characters_never_leaks(self):
        sb = self.sandbox()
        self.assertEqual(sb.run(answers()), 0, sb.stdout)
        self.assertEqual(sb.recorded_secrets()["REGISTRY_PASSWORD"], PASSWORD)
        self.assertNotIn(PASSWORD, sb.log.read_text())
        self.assertNotIn(PASSWORD, sb.stdout)

    def test_webhook_secret_only_when_given(self):
        sb = self.sandbox()
        self.assertEqual(sb.run(answers(hook="https://hook.acme.com/x")), 0, sb.stdout)
        self.assertEqual(sb.recorded_secrets()["DEPLOY_WEBHOOK_URL"], "https://hook.acme.com/x")

    def test_several_dockerfiles_generate_a_matrix_with_unique_names(self):
        sb = self.sandbox(files=("config/docker/Dockerfile", "front/Dockerfile"))
        self.assertEqual(sb.run(answers()), 0, sb.stdout)
        check_workflow(self, sb.workflow, "workflow com matrix")
        self.assertIn("matrix:", sb.workflow)
        self.assertIn("name: config-docker", sb.workflow)
        self.assertIn("name: front", sb.workflow)
        self.assertIn("dockerfile: front/Dockerfile", sb.workflow)
        self.assertIn("name: ${{ matrix.name }}", sb.workflow)

    def test_single_dockerfile_in_subfolder(self):
        sb = self.sandbox(files=("api/Dockerfile",))
        self.assertEqual(sb.run(answers()), 0, sb.stdout)
        check_workflow(self, sb.workflow, "workflow subpasta")
        self.assertIn("context: api", sb.workflow)
        self.assertIn("dockerfile_path: api/Dockerfile", sb.workflow)
        self.assertNotIn("matrix:", sb.workflow)

    def test_existing_workflow_is_kept_unless_confirmed(self):
        sb = self.sandbox(existing_workflow="ORIGINAL\n")
        self.assertEqual(sb.run("n\n"), 0, sb.stdout)
        self.assertEqual(sb.workflow, "ORIGINAL\n")
        self.assertEqual(sb.recorded_secrets(), {})

    def test_existing_workflow_can_be_overwritten(self):
        sb = self.sandbox(existing_workflow="ORIGINAL\n")
        self.assertEqual(sb.run("s\n" + answers()), 0, sb.stdout)
        self.assertNotEqual(sb.workflow, "ORIGINAL\n")

    def test_no_dockerfile_fails_with_message(self):
        sb = self.sandbox(files=())
        self.assertNotEqual(sb.run(answers()), 0)
        self.assertIn("Nenhum Dockerfile", sb.stdout)

    def test_missing_gh_fails_with_instructions(self):
        sb = self.sandbox()
        (Path(sb.env["PATH"].split(":")[0]) / "gh").unlink()
        sb.env["PATH"] = sb.env["PATH"].split(":", 1)[0] + ":/usr/bin:/bin"
        self.assertNotEqual(sb.run(answers()), 0)
        self.assertIn("brew install gh", sb.stdout)

    def test_empty_credentials_are_rejected(self):
        sb = self.sandbox()
        self.assertNotEqual(sb.run(answers(user="")), 0)
        self.assertEqual(sb.recorded_secrets(), {})

    def test_org_with_free_plan_and_private_repo_warns_and_can_cancel(self):
        sb = self.sandbox(env={"FAKE_GH_PLAN": "free", "FAKE_GH_PRIVATE": "true"})
        self.assertEqual(sb.run("n\n", "--org", "acme"), 0, sb.stdout)
        self.assertIn("plano gratuito", sb.stdout)
        self.assertEqual(sb.recorded_secrets(), {})

    def test_org_with_paid_plan_sets_org_secrets(self):
        sb = self.sandbox(env={"FAKE_GH_PLAN": "team", "FAKE_GH_PRIVATE": "true"})
        self.assertEqual(sb.run(answers(), "--org", "acme"), 0, sb.stdout)
        self.assertIn("secret set REGISTRY_URL --org acme", sb.log.read_text())
        self.assertNotIn("--repo", sb.log.read_text())

    @unittest.skipUnless(shutil.which("actionlint"), "actionlint não instalado")
    def test_generated_workflows_pass_actionlint(self):
        for files in (("Dockerfile",), ("api/Dockerfile",), ("a/Dockerfile", "b/Dockerfile")):
            sb = self.sandbox(files=files)
            self.assertEqual(sb.run(answers()), 0, sb.stdout)
            lint = subprocess.run(
                ["actionlint", ".github/workflows/docker.yml"],
                cwd=sb.repo,
                capture_output=True,
                text=True,
            )
            self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)


if __name__ == "__main__":
    unittest.main()
```

Run: `cd /Users/invicto/Documents/Startup/docker-push-webhook-action && python3 -m unittest test_install 2>&1 | tail -8`
Expected: falhas (`install.sh` não existe).

- [ ] **Step 2: Implementar `install.sh`**

```bash
#!/usr/bin/env bash
# Instala o CI de build + push Docker no repositório atual.
# Rode na raiz do repositório:
#   bash install.sh             # secrets cadastrados neste repositório (padrão)
#   bash install.sh --org ORG   # secrets cadastrados na organização (veja INSTALAR.md)
# Nada é commitado nem enviado sem a sua confirmação.
set -euo pipefail

ACTION="invicto-dev/docker-push-webhook-action@v2"
WORKFLOW=".github/workflows/docker.yml"
org=""

die() {
  echo "ERRO: $1" >&2
  exit 1
}

# confirm "pergunta": retorna 0 só para s/S/y/Y (EOF ou qualquer outra coisa = não).
confirm() {
  local ans
  printf '%s [s/N] ' "$1"
  read -r ans || ans=""
  [[ "$ans" == [sSyY]* ]]
}

while (( $# )); do
  case "$1" in
    --org)
      org="${2:-}"
      [[ -n "$org" ]] || die "--org precisa do nome da organização."
      shift 2 ;;
    -h|--help)
      echo "Uso: bash install.sh [--org ORGANIZACAO]"
      exit 0 ;;
    *) die "Opção desconhecida: $1" ;;
  esac
done

command -v gh >/dev/null || die "GitHub CLI (gh) não encontrado. Instale em https://cli.github.com (macOS: brew install gh) e rode de novo."
gh auth status >/dev/null 2>&1 || die "Você não está logado no GitHub CLI. Rode: gh auth login"
root="$(git rev-parse --show-toplevel 2>/dev/null)" || die "Rode este script dentro de um repositório git."
cd "$root"
repo="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null)" || die "Não consegui identificar o repositório no GitHub. Ele tem um remote 'origin'?"

dockerfiles=()
count=0
while IFS= read -r f; do
  dockerfiles+=("$f")
  count=$((count + 1))
done < <(git ls-files --cached --others --exclude-standard | grep -E '(^|/)Dockerfile$' | grep -v 'node_modules/' || true)
(( count > 0 )) || die "Nenhum Dockerfile encontrado neste repositório."

if [[ -f "$WORKFLOW" ]]; then
  confirm "$WORKFLOW já existe. Sobrescrever?" || { echo "Nada foi alterado."; exit 0; }
fi

if [[ -n "$org" ]]; then
  plan="$(gh api "orgs/$org" --jq .plan.name 2>/dev/null || echo desconhecido)"
  private="$(gh repo view --json isPrivate --jq .isPrivate 2>/dev/null || echo true)"
  if [[ "$plan" == "free" && "$private" == "true" ]]; then
    echo "AVISO: a organização '$org' está no plano gratuito e este repositório é privado."
    echo "No plano gratuito, secrets de organização NÃO chegam a repositórios privados."
    confirm "Continuar mesmo assim?" || { echo "Cancelado. Rode de novo sem --org."; exit 0; }
  fi
fi

printf 'Endereço do registry (ex: registry.empresa.com): '
read -r host || host=""
printf 'Usuário do registry: '
read -r user || user=""
printf 'Senha ou token do registry (não aparece na tela): '
read -rs pass || pass=""
echo
printf 'URL do webhook de deploy (opcional, Enter para pular): '
read -r hook || hook=""
[[ -n "${host//[[:space:]]/}" && -n "$user" && -n "$pass" ]] || die "Endereço, usuário e senha do registry são obrigatórios."

set_secret() {
  if [[ -n "$org" ]]; then
    printf '%s' "$2" | gh secret set "$1" --org "$org" --visibility all >/dev/null
  else
    printf '%s' "$2" | gh secret set "$1" --repo "$repo" >/dev/null
  fi
  echo "  secret $1 cadastrado"
}
set_secret REGISTRY_URL "$host"
set_secret REGISTRY_USERNAME "$user"
set_secret REGISTRY_PASSWORD "$pass"
if [[ -n "$hook" ]]; then set_secret DEPLOY_WEBHOOK_URL "$hook"; fi

# Image name from the Dockerfile folder: "config/docker" -> "config-docker".
slug() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9._-' '-'; }
dir_of() { local d; d="$(dirname "$1")"; printf '%s' "$d"; }

mkdir -p "$(dirname "$WORKFLOW")"
{
  cat <<'EOF'
# Publica (envia) a imagem Docker deste projeto para o registry da empresa.
# Gerado por install.sh. Os secrets REGISTRY_URL, REGISTRY_USERNAME e
# REGISTRY_PASSWORD ficam em Settings > Secrets and variables > Actions > Secrets.

name: Docker
run-name: Publicar imagem Docker

on:
  push:
    branches: [main]   # roda sempre que algo novo entrar na branch "main"
  workflow_dispatch:   # ou manualmente: aba Actions > Docker > Run workflow

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      contents: read
EOF
  if (( count > 1 )); then
    echo "    strategy:"
    echo "      matrix:"
    echo "        include:"
    for f in "${dockerfiles[@]}"; do
      d="$(dir_of "$f")"
      if [[ "$d" == "." ]]; then
        img="$(slug "$(basename "$root")")"
        ctx="."
      else
        img="$(slug "$d")"
        ctx="$d"
      fi
      echo "          - name: $img"
      echo "            context: $ctx   # se o build não achar arquivos, troque por ."
      echo "            dockerfile: $f"
    done
  fi
  cat <<EOF
    steps:
      - uses: actions/checkout@v4
      - uses: $ACTION
        with:
          registry_url: \${{ secrets.REGISTRY_URL }}
          registry_username: \${{ secrets.REGISTRY_USERNAME }}
          registry_password: \${{ secrets.REGISTRY_PASSWORD }}
          webhook_url: \${{ secrets.DEPLOY_WEBHOOK_URL }}
EOF
  if (( count > 1 )); then
    echo '          name: ${{ matrix.name }}'
    echo '          context: ${{ matrix.context }}'
    echo '          dockerfile_path: ${{ matrix.dockerfile }}'
  else
    d="$(dir_of "${dockerfiles[0]}")"
    if [[ "$d" != "." ]]; then
      echo "          context: $d   # se o build não achar arquivos, troque por ."
      echo "          dockerfile_path: ${dockerfiles[0]}"
    fi
  fi
} > "$WORKFLOW"

echo
echo "Pronto: $WORKFLOW criado."
echo "Confira o campo 'context' de cada imagem no arquivo: se o build falhar procurando arquivos, troque por '.'."
if confirm "Fazer commit e push agora?"; then
  git add "$WORKFLOW"
  git commit -m "ci: add docker build and push workflow"
  git push
else
  echo "Quando quiser: git add $WORKFLOW && git commit -m 'ci: add docker workflow' && git push"
fi
```

Observação para o executor: `dockerfiles[0]` e `"${dockerfiles[@]}"` só são expandidos quando `count > 0` (garantido pelo `die` acima), então não há expansão de array vazio.

- [ ] **Step 3: Rodar os testes**

Run: `cd /Users/invicto/Documents/Startup/docker-push-webhook-action && python3 -m unittest -v test_install 2>&1 | tail -30`
Expected: todos `ok` (o teste de `actionlint` roda porque `/opt/homebrew/bin/actionlint` existe). Se falhar por diferença do Bash 3.2 ou do `tr -c` do macOS, corrija em `install.sh` respeitando as Global Constraints.

- [ ] **Step 4: Rodar a suíte inteira e o CI deve instalar o `actionlint`**

Run: `cd /Users/invicto/Documents/Startup/docker-push-webhook-action && python3 -m unittest 2>&1 | tail -5`
Expected: `OK`.

Em `.github/workflows/test.yml`, no job `unit`, inserir antes do `run: python3 -m unittest -v`:

```yaml
      - name: Install actionlint
        run: |
          bash <(curl -fsSL https://raw.githubusercontent.com/rhysd/actionlint/v1.7.7/scripts/download-actionlint.bash) 1.7.7
          echo "$PWD" >> "$GITHUB_PATH"
```

Run: `actionlint .github/workflows/test.yml && echo lint-ok`
Expected: `lint-ok`.

- [ ] **Step 5: Commit**

```bash
cd /Users/invicto/Documents/Startup/docker-push-webhook-action
git add install.sh test_install.py .github/workflows/test.yml
git commit -m "feat: install.sh installs the workflow and secrets from the terminal

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Teste real, lançamento e migração do repositório consumidor

**Files:**
- Modify (repo `scimgateway-entraid-modified`): `.github/workflows/docker.yml`

**Interfaces:**
- Consumes: action publicada na tag `v2` (Tasks 1-4 já em `main` do repo da action e CI verde).
- Produces: tags `v2` (móvel) e `v2.0.0` + GitHub Release; consumidor usando `@v2`.

Esta task faz ações externas (push, tags, release). **Pedir confirmação ao usuário antes de cada push/tag.**

- [ ] **Step 1: Garantir CI verde no repo da action**

```bash
cd /Users/invicto/Documents/Startup/docker-push-webhook-action
git push origin main
export GH_TOKEN=$(gh auth token -u invicto-dev)
sleep 20; gh run list -R invicto-dev/docker-push-webhook-action -L 1
```

Acompanhar com `gh run watch <id> -R invicto-dev/docker-push-webhook-action --exit-status`.
Expected: `unit` e `e2e` verdes.

- [ ] **Step 2: Publicar a tag móvel `v2` (para o teste real)**

```bash
cd /Users/invicto/Documents/Startup/docker-push-webhook-action
git tag v2
git push origin v2
```

- [ ] **Step 3: Migrar o repositório consumidor**

Overwrite `/Users/invicto/Documents/QRIAR-1.0/scimgateway-entraid-modified/.github/workflows/docker.yml` with:

```yaml
# Este arquivo publica (envia) as imagens Docker do projeto para o registry da empresa.
#
# ANTES DE USAR, cadastre no GitHub, em:
#   Settings > Secrets and variables > Actions > aba "Secrets"
#   (cadastre como SECRET, não como Variable, e use os nomes exatos)
#
#     REGISTRY_URL        -> endereço do registry. Ex: registry.qriar.com
#     REGISTRY_USERNAME   -> usuário do registry
#     REGISTRY_PASSWORD   -> senha ou token do registry
#     DEPLOY_WEBHOOK_URL  -> (opcional) endereço avisado após o envio,
#                            normalmente para fazer o deploy automático.
#                            Sem ele, o aviso simplesmente não é feito.

name: Docker
run-name: Publicar imagens Docker

# QUANDO roda:
on:
  push:
    branches: [main]   # automaticamente, sempre que algo novo entrar na branch "main"
  workflow_dispatch:   # ou manualmente: aba Actions > Docker > Run workflow

jobs:
  publish:
    runs-on: ubuntu-latest  # máquina Linux do GitHub que faz o trabalho
    permissions:
      contents: read        # só precisa LER o código do repositório
    strategy:
      # Lista das imagens criadas. Cada item gera uma imagem, e as duas são
      # feitas ao mesmo tempo. Para adicionar outra, copie um bloco "- name:".
      matrix:
        include:
          # Imagem 1: o SCIM Gateway (servidor principal)
          - name: scimgateway-entraid-modified
            context: .                              # pasta usada para montar a imagem
            dockerfile: config/docker/Dockerfile    # "receita" da imagem
          # Imagem 2: o front (chat web)
          - name: scimgateway-entraid-modified-front
            context: front
            dockerfile: front/Dockerfile
    steps:
      # Passo 1: baixa o código do repositório
      - uses: actions/checkout@v4

      # Passo 2: monta a imagem, envia para o registry e (opcional) avisa o webhook.
      # As tags são "latest" e o código do commit (automático).
      - uses: invicto-dev/docker-push-webhook-action@v2
        with:
          registry_url: ${{ secrets.REGISTRY_URL }}
          registry_username: ${{ secrets.REGISTRY_USERNAME }}
          registry_password: ${{ secrets.REGISTRY_PASSWORD }}
          name: ${{ matrix.name }}
          context: ${{ matrix.context }}
          dockerfile_path: ${{ matrix.dockerfile }}
          webhook_url: ${{ secrets.DEPLOY_WEBHOOK_URL }}
          webhook_payload: '{"image": "${{ matrix.name }}", "sha": "${{ github.sha }}"}'
```

Run: `cd /Users/invicto/Documents/QRIAR-1.0/scimgateway-entraid-modified && actionlint .github/workflows/docker.yml && echo lint-ok`
Expected: `lint-ok`. (Os secrets `REGISTRY_URL`, `REGISTRY_USERNAME`, `REGISTRY_PASSWORD` e `DEPLOY_WEBHOOK_URL` já existem no repositório como Secrets.)

- [ ] **Step 4: Teste real contra `connectors-hub.qriardemo.digital`**

```bash
cd /Users/invicto/Documents/QRIAR-1.0/scimgateway-entraid-modified
git add .github/workflows/docker.yml
git commit -m "ci: use docker-push-webhook-action v2

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push origin main
export GH_TOKEN=$(gh auth token -u invicto-dev)
sleep 20; gh run list -R QRIAR-CyberSecurity/scimgateway-entraid-modified -L 1
```

Acompanhar com `gh run watch <id> -R QRIAR-CyberSecurity/scimgateway-entraid-modified --exit-status`.
Expected: os dois jobs da matrix verdes; no log, `REGISTRY_URL` não vazio no passo de login; resumo "Imagem publicada" visível. Confirmar no registry:

`curl -k -u <usuario>:<senha> https://connectors-hub.qriardemo.digital/v2/_catalog`
Expected: lista com `scimgateway-entraid-modified` e `scimgateway-entraid-modified-front`.

Se falhar: ler `gh run view <id> --log-failed`, corrigir a causa raiz na action, commitar, mover a tag `v2` (`git tag -f v2 && git push -f origin v2`, com confirmação do usuário) e repetir. Também testar um segundo cenário manual: rodar `install.sh` em um repositório descartável (ou `--help`) e abrir `INSTALAR.md` no GitHub para conferir a renderização.

- [ ] **Step 5: Lançar `v2.0.0`**

Só depois do Step 4 verde e com confirmação do usuário:

```bash
cd /Users/invicto/Documents/Startup/docker-push-webhook-action
git tag v2.0.0
git push origin v2.0.0
gh release create v2.0.0 -R invicto-dev/docker-push-webhook-action --title "v2.0.0" --notes "Breaking: 'image' replaced by 'name'; 'registry_url' is now required (use docker.io for Docker Hub). New: pt-BR validation messages, default name/tags, job summary, install.sh, INSTALAR.md, llms.txt."
```

Expected: release visível em `https://github.com/invicto-dev/docker-push-webhook-action/releases/tag/v2.0.0`.

---

## Self-Review

**Cobertura da spec**
- Seção 2 (interface): `name`, `registry_url` obrigatório, normalização, tags padrão, validações pt-BR, resumo, versionamento → Tasks 1, 2, 5.
- Seção 3.1 template → Task 3. 3.2 `INSTALAR.md` → Task 3. 3.3 `install.sh` → Task 4. 3.4 `--org` + checagem de plano → Task 4 (testes `test_org_*`).
- Seção 4 `llms.txt` → Task 3.
- Seção 5 testes: validações e normalização → Tasks 1 e 2; `install.sh` com `gh` simulado + `actionlint` → Task 4; consistência de docs → Task 3; teste real → Task 5.
- Seção 6 riscos: caminho `docker.io` explícito fica coberto pelo README/INSTALAR; o teste real do Docker Hub não está no plano (exigiria conta). Anotado: confirmar manualmente se alguém for usar `docker.io`.

**Placeholders:** nenhum "TBD". Cada passo de código tem o código.

**Consistência de nomes:** outputs `registry`, `dockerfile`, `docker_config`, `refs` (Task 1) são os mesmos lidos em `action.yml` (Task 2). `check_workflow` e `SECRETS` (Task 3) são os importados por `test_install.py` (Task 4). Nomes de secrets iguais em template, `install.sh`, `INSTALAR.md`, `llms.txt`, README e consumidor.

**Review Focus:** cada item tem teste: (1) `test_pasted_registry_is_normalized` e e2e `http://localhost:5000/`; (2) `test_registry_with_line_breaks_cannot_inject_outputs`, `test_whitespace_and_line_breaks_are_rejected`, e2e `injected_name`; (3) `test_empty_registry_names_the_secret`, e2e `missing_registry`; (4) `test_several_dockerfiles_generate_a_matrix_with_unique_names`, `test_existing_workflow_is_kept_unless_confirmed`; (5) `test_password_with_special_characters_never_leaks`.
