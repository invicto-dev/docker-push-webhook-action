# Design Doc: vários webhooks e corpo montado na execução

- **Data:** 14 de Outubro de 2026
- **Status:** Aguardando revisão
- **Escopo:** `docker-push-webhook-action` v2.3.0 (sem quebra de interface; uma mudança de comportamento documentada na seção 6)

---

## 1. Objetivo

Depois de cada imagem publicada, avisar **mais de um destino** com os dados que os operadores precisam:

1. **Teams**, via um fluxo do Power Automate (gatilho "Quando uma solicitação HTTP é recebida"), que já existe e responde 202.
2. **Portainer**, para deploy automático. Ainda não existe webhook no Portainer (recurso pago), mas a action deve ficar **pronta**: basta cadastrar um secret no dia em que existir, sem mudar workflow nem action.

Público da mensagem do Teams: **operadores dos conectores em produção**. Eles precisam ver a **tag de versão** (a de rollback, ex. `2026.10.13-2235Z-77b2a3b`) e o que mudou.

### Problema atual

- `webhook_url` aceita **uma** URL.
- `webhook_payload` é texto escrito no YAML. O GitHub o monta **antes** de a action rodar, então ele não consegue levar dados que só existem durante a execução: tag de versão, digest, tags publicadas.

### Fora do escopo

| Item | Quando adicionar |
|---|---|
| Headers diferentes por destino (ex.: token só para um) | Quando um destino precisar |
| Formatar o cartão do Teams na action | Nunca: isso é do fluxo do Power Automate |
| Informar o resultado do deploy do Portainer no Teams | Se quiserem, no Power Automate (abordagem B), sem mudar a action |
| Retentativas por destino | Quando houver pedido |

## 2. Abordagem escolhida

**A. A action chama vários webhooks, com o corpo montado na execução.** Genérica: Teams, Portainer ou qualquer outro destino funcionam igual. Alternativas descartadas: o Power Automate orquestrando tudo (lógica fora do código, e ainda exigiria o corpo montado na execução para levar a versão) e um input `portainer_webhook_url` dedicado (amarra a action a um produto).

## 3. Interface (`action.yml`)

Nenhum input novo. Mudam os significados:

| Input | Antes | Depois |
|---|---|---|
| `webhook_url` | uma URL; vazio pula | **uma URL por linha**. Linhas vazias são ignoradas. Nenhuma URL sobrando: o passo termina sem chamar nada. Aceita marcadores (seção 5) |
| `webhook_payload` | corpo enviado como está; vazio = POST sem corpo | corpo enviado como está, com marcadores substituídos; **vazio = corpo JSON padrão** (seção 4) |
| `webhook_headers` | headers do webhook | iguais, aplicados a **todos** os destinos |
| `webhook_fail_on_error` | falha o job se o webhook falhar | igual, considerando todos os destinos |

O passo do webhook continua só depois de um push bem-sucedido e recebe os dados da execução por variáveis de ambiente (nunca interpolados em código): referência da imagem sem tag, tag de versão, tags publicadas, digest. `prepare.sh` passa a emitir o output `image` (`<registry>/<name>`, sem tag) para isso.

Uso típico com dois destinos:

```yaml
webhook_url: |
  ${{ secrets.DEPLOY_WEBHOOK_URL }}
  ${{ secrets.PORTAINER_WEBHOOK_URL }}
```

Secret inexistente chega como linha vazia e é ignorado. Atenção: a condição atual `if: inputs.webhook_url != ''` deixa o passo rodar quando o texto tem só quebras de linha; o script trata "nenhuma URL" como "nada a fazer" (sucesso, mensagem informativa).

## 4. Corpo JSON padrão

Usado quando `webhook_payload` está vazio. Montado pelo script com `json.dumps` (nada é concatenado como texto):

| Campo | Exemplo | Origem |
|---|---|---|
| `repository` | `QRIAR-CyberSecurity/scimgateway-entraid-modified` | `GITHUB_REPOSITORY` |
| `image` | `connectors-hub.qriardemo.digital/scimgateway-entraid-modified` | output `image` do prepare |
| `version` | `2026.10.13-2235Z-77b2a3b` | output `version` (vazio com `tags` customizadas) |
| `tags` | `["latest", "77b2a3b…", "2026.10.13-2235Z-77b2a3b"]` | refs publicadas, só a parte da tag |
| `digest` | `sha256:15b6…` | saída do push (vazio se não lido) |
| `commit` | `77b2a3b…` (completo) | `GITHUB_SHA` |
| `commit_message` | primeira linha da mensagem | `git log -1 --format=%s`; vazio sem git |
| `author` | `Victo Hugo` | `git log -1 --format=%an`; vazio sem git |
| `run_url` | `https://github.com/<repo>/actions/runs/<id>` | `GITHUB_SERVER_URL`, `GITHUB_REPOSITORY`, `GITHUB_RUN_ID` |

`Content-Type: application/json` (padrão já existente). O mesmo corpo vai para todos os destinos.

## 5. Marcadores

Substituídos em `webhook_url` (cada linha) e em `webhook_payload` customizado:

| Marcador | Valor |
|---|---|
| `{version}` | tag de versão |
| `{digest}` | digest |
| `{image}` | imagem sem tag |
| `{sha}` | commit completo |

- Na URL, o valor é codificado para URL. Exemplo para o webhook de serviço do Portainer: `https://portainer…/api/webhooks/<id>?tag={version}`.
- No payload customizado, o valor entra como texto puro. Todos os valores têm caracteres restritos (tag validada por regex, digest hexadecimal, imagem validada pelo prepare, SHA hexadecimal), então não quebram JSON.
- Marcador desconhecido (ex. `{foo}`) fica como está.
- Marcador cujo valor está vazio (ex. `{version}` com tags customizadas) vira texto vazio.

## 6. Comportamento e erros

- **Validação antes de qualquer envio:** todas as URLs (http/https, com host, sem credenciais embutidas) e os headers são validados primeiro. Erro de configuração falha o job sem avisar nenhum destino (regra atual, estendida a várias URLs).
- **Envio:** um `POST` por destino, na ordem das linhas, 30 s de timeout cada, sem seguir redirecionamentos (regras atuais).
- **Falha de um destino não impede os outros:** todos são chamados; no fim, se algum falhou, o job falha (ou só avisa, com `webhook_fail_on_error: false`).
- **Log:** identifica destinos por posição, nunca pela URL: `Webhook 1/2: HTTP 202`, `Webhook 2/2 falhou: HTTP 500`. URL, headers, corpo e resposta continuam fora do log.
- **Mudança de comportamento (documentar no release):** `webhook_payload` vazio passava a fazer POST **sem corpo**; agora envia o JSON padrão. Destinos que ignoram o corpo (como o Portainer) não são afetados.

## 7. Documentação

- README: seção do webhook com várias URLs, o JSON padrão (campos e exemplo), marcadores e o exemplo do Portainer com `?tag={version}`.
- `llms.txt`: o mesmo, de forma curta, e a regra "não escreva `webhook_payload` se o JSON padrão serve".
- `INSTALAR.md`: um parágrafo "avisar o Teams e o Portainer": cadastrar `DEPLOY_WEBHOOK_URL` e, quando existir, `PORTAINER_WEBHOOK_URL`.
- `templates/docker.yml` e workflow gerado pelo `install.sh`: `webhook_url` em bloco com a linha do `DEPLOY_WEBHOOK_URL` e a do `PORTAINER_WEBHOOK_URL` (ignorada enquanto o secret não existir).
- Fora deste repositório (ação do usuário): ajustar o fluxo do Power Automate para usar os campos novos no cartão do Teams. Um exemplo de esquema JSON para o gatilho vai no README.

## 8. Testes

Em `test_webhook.py`, no estilo atual (servidor HTTP local):

1. Duas URLs: as duas recebem o mesmo corpo; linhas vazias são ignoradas.
2. Só linhas vazias: sai com sucesso sem chamar nada.
3. Uma URL falha (500) e a outra responde 200: a segunda ainda é chamada; o job falha; com `fail_on_error=false`, só avisa. O log cita a posição, nunca a URL.
4. Uma URL inválida entre válidas: nada é enviado.
5. Corpo padrão: campos e valores conferidos a partir de variáveis de ambiente fixas; JSON válido.
6. Marcadores na URL (`?tag={version}` chega codificado) e no payload customizado; marcador desconhecido intacto.
7. Os testes atuais continuam valendo, exceto `test_empty_payload_posts_without_body`, que passa a verificar o corpo padrão.

E2E (`test.yml`): o listener local recebe o corpo padrão com `version` e `digest` preenchidos. Docs: o teste de consistência confere que os exemplos de workflow usam só inputs existentes (já existe).

Verificação real: rodar o workflow do `scimgateway-entraid-modified` com o Power Automate e confirmar no histórico do fluxo que o corpo novo chegou.

## 9. Versionamento

Lançar `v2.3.0` e mover a tag `v2`. Atualizar o workflow do `scimgateway-entraid-modified` para o `webhook_url` em bloco e remover o `webhook_payload` fixo.
