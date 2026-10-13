# Design Doc: Docker Push & Webhook (GitHub Action)

- **Data:** 13 de Outubro de 2026
- **Status:** Em revisão
- **Escopo:** repositório novo `docker-push-webhook-action`

---

## 1. Objetivo

Action pública para o GitHub Marketplace que, num único passo:

1. Faz login num registry Docker (qualquer um; vazio = Docker Hub).
2. Faz build da imagem e push de uma ou mais tags.
3. Opcionalmente chama um webhook (ex.: disparar deploy) com headers e payload arbitrários.

Público: qualquer usuário do Marketplace. Prioridades, nesta ordem: **segurança** (sem shell injection, sem vazar credenciais), **simplicidade** (zero actions externas, zero dependências além do runner), **robustez** (payload/headers com qualquer caractere chegam intactos).

### Fora do escopo da v1

| Item | Quando adicionar |
|---|---|
| Multi-plataforma (`platforms`) | Quando houver pedido; exige buildx + QEMU |
| Cache de camadas (GHA/registry) | Quando houver pedido; exige builder `docker-container` |
| Build secrets (`--secret`) | Quando houver pedido |
| Método HTTP / timeout configuráveis no webhook | Quando houver pedido |
| Retries no webhook | Quando houver pedido |
| Placeholders/payload automático com digest | Quando houver pedido (payload padrão automático, sem quebrar a interface) |

## 2. Abordagem escolhida

**Composite action com Docker CLI nativo + Python 3 (stdlib) para o webhook.** Nenhuma action de terceiros.

Alternativas descartadas:

- **Actions oficiais (`docker/login-action`, `setup-buildx-action`, `build-push-action`):** só se justificam com multi-arch/cache, ambos fora da v1. Adicionam dependência do ciclo de vida de 3 actions externas.
- **100% Bash + jq + curl:** viável, mas validar headers (objeto, valores string, sem CR/LF) e mascarar valores é mais claro e testável em Python.

Requisito de runner: **Linux com `docker` e `python3`** (todos os GitHub-hosted `ubuntu-*`). macOS/Windows hosted não têm Docker; a action falha cedo com mensagem clara.

## 3. Interface (`action.yml`)

### Inputs

| Input | Obrig. | Padrão | Comportamento |
|---|---|---|---|
| `registry_url` | não | `""` | Host do registry. Vazio = Docker Hub (argumento omitido no `docker login`) |
| `registry_username` | sim | — | |
| `registry_password` | sim | — | Enviada via stdin (`--password-stdin`); nunca em argv nem log |
| `image` | sim | — | Nome completo sem tag, ex.: `ghcr.io/org/app` |
| `tags` | não | `latest` | Separadas por vírgula e/ou quebra de linha; trim; vazias descartadas |
| `context` | não | `.` | Contexto do build |
| `dockerfile_path` | não | `""` | Arquivo **ou** pasta. Pasta → `<pasta>/Dockerfile`. Vazio → `<context>/Dockerfile` |
| `build_args` | não | `""` | Multilinha, uma `KEY=VALUE` por linha; linhas vazias ignoradas |
| `webhook_url` | não | `""` | Vazio = passo do webhook é pulado |
| `webhook_headers` | não | `{}` | Objeto JSON; chaves e valores string |
| `webhook_payload` | não | `""` | Corpo enviado byte a byte como recebido. Vazio = POST sem corpo |
| `webhook_fail_on_error` | não | `true` | `false` → falha do webhook vira warning e o job segue |

### Outputs

| Output | Conteúdo |
|---|---|
| `image` | Primeira ref completa (`image:tag`) |
| `tags` | Todas as refs completas, uma por linha |
| `digest` | `sha256:...` da imagem publicada |

Nota: o webhook roda dentro da própria action, então o caller **não** consegue referenciar esses outputs em `webhook_payload`. Imagem e tags o caller já conhece (foram inputs); o digest não entra no payload na v1.

### Comportamentos fixos

- Webhook sempre via **POST**, timeout **30s**.
- Se houver payload e `webhook_headers` não definir `Content-Type` (comparação case-insensitive), envia `Content-Type: application/json`.
- `branding` definido (exigido pelo Marketplace).

## 4. Fluxo interno

Todos os passos `run:` usam `shell: bash` e começam com `set -euo pipefail`.

| # | Passo | Condição | O que faz |
|---|---|---|---|
| 1 | `prepare` | sempre | Checa `docker` e `python3`; valida `image` não vazio; resolve dockerfile (deve existir); parseia e valida tags com `^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$`; monta refs; cria `DOCKER_CONFIG=$(mktemp -d)` e expõe como **output do passo** |
| 2 | `login` | sucesso anterior | `printf '%s' "$PASSWORD" \| docker login [registry] -u "$USER" --password-stdin` |
| 3 | `build` | sucesso anterior | `docker build -f "$DOCKERFILE" -t ref… --build-arg linha… "$CONTEXT"` com argumentos em arrays Bash |
| 4 | `push` | sucesso anterior | `docker push` de cada ref; digest via `docker inspect` (`RepoDigests`, entrada do `image`); grava outputs |
| 5 | `webhook` | sucesso anterior **e** `webhook_url != ''` | `python3 "$GITHUB_ACTION_PATH/webhook.py"` |
| 6 | `cleanup` | `always()` (e `DOCKER_CONFIG` criado) | `docker logout [registry] \|\| true`; `rm -rf "$DOCKER_CONFIG"` |

Os passos 2–4 e 6 recebem `DOCKER_CONFIG` via `env:` a partir do output do passo 1. O `GITHUB_ENV` **não** é usado, para não alterar o Docker de passos posteriores do workflow do caller.

### `webhook.py`

Entrada exclusivamente por variáveis de ambiente: `WEBHOOK_URL`, `WEBHOOK_HEADERS`, `WEBHOOK_PAYLOAD`, `WEBHOOK_FAIL_ON_ERROR`.

1. Faz parse de `WEBHOOK_HEADERS` como JSON. Para cada valor que for string, emite `::add-mask::<valor>` **antes** de qualquer validação que possa logar algo.
2. Valida: precisa ser objeto; chaves e valores `str`; nenhum contém `\r` ou `\n`; chave não vazia.
3. Valida a URL: esquema `http` ou `https` e host presente.
4. Monta um `urllib.request.Request(method="POST")` com corpo `WEBHOOK_PAYLOAD.encode("utf-8")` (ou `None` se vazio) e adiciona o `Content-Type` padrão quando aplicável.
5. Envia com `timeout=30`. Status 2xx = sucesso.
6. Log: apenas `Webhook respondeu HTTP <status>` ou o tipo do erro. **Nunca** loga headers, payload ou corpo da resposta.

Erros de validação de entrada (headers, URL) **sempre** falham com exit 1, independente de `fail_on_error`, porque são erros de configuração. Erros de entrega (rede, timeout, status fora de 2xx) seguem `fail_on_error`: `true` → `::error::` + exit 1; `false` → `::warning::` + exit 0.

## 5. Segurança

- Nenhum `${{ inputs.* }}` dentro de blocos `run:`. Todo input entra via `env:` e é usado sempre entre aspas.
- Argumentos do Docker montados em arrays Bash; nada de `eval` ou concatenação de string.
- Senha só via stdin.
- Credenciais isoladas num `DOCKER_CONFIG` temporário, removido no `cleanup` mesmo em falha (importante em runners self-hosted).
- Valores de headers mascarados no log; rejeição de CR/LF impede header injection.
- O README avisa: `build_args` ficam gravados no histórico da imagem e não devem conter segredos.

## 6. Estrutura do repositório

```
action.yml
webhook.py
test_webhook.py
test/Dockerfile                 # FROM busybox
.github/workflows/test.yml
README.md
LICENSE                         # MIT
```

## 7. Testes

### Unitários: `test_webhook.py` (stdlib `unittest`)

Sobe um `http.server` local numa thread que grava a requisição recebida; executa `webhook.py` via `subprocess` com env controlado.

| Caso | Esperado |
|---|---|
| Servidor responde 200 | exit 0 |
| Servidor responde 500 | exit 1, `::error::` |
| 500 com `fail_on_error=false` | exit 0, `::warning::` |
| Headers com JSON inválido / array / valor numérico / valor com `\n` | exit 1 (mesmo com `fail_on_error=false`) |
| URL `ftp://…` | exit 1 |
| Payload sem `Content-Type` nos headers | servidor recebe `application/json` |
| `Content-Type` customizado (`content-type` minúsculo) | mantido; sem duplicar |
| Headers com valor secreto | stdout contém `::add-mask::<valor>`, e o valor não aparece em nenhuma outra linha |
| Payload com aspas, `'`, `\n`, `$()`, unicode | bytes recebidos idênticos aos enviados |
| Payload vazio | POST sem corpo |

### E2E: `.github/workflows/test.yml` (push e PR, `ubuntu-latest`)

1. `python3 -m unittest test_webhook.py`.
2. Service `registry:2` em `localhost:5000`; listener Python em background gravando a requisição num arquivo.
3. `uses: ./` com `registry_url: localhost:5000`, duas tags, `dockerfile_path: test` (pasta), um `build_args`, webhook apontando para o listener com header e payload contendo caracteres especiais.
4. Asserções: `docker pull` de cada tag funciona; outputs `digest` (prefixo `sha256:`) e `tags` não vazios; arquivo do listener tem o payload e o header esperados.
5. Caso negativo: `uses: ./` com tag inválida (`bad tag!`), `continue-on-error: true`; asserção `steps.<id>.outcome == 'failure'`.

## 8. Release

- Tags `v1.0.0` e tag móvel `v1`.
- README: exemplo mínimo (Docker Hub), exemplo com GHCR + webhook, tabela de inputs/outputs, requisitos de runner e aviso sobre `build_args`.
