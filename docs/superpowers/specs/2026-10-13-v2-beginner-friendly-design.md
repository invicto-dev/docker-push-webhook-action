# Design Doc: v2 — instalação à prova de erro

- **Data:** 13 de Outubro de 2026
- **Status:** Aguardando revisão
- **Escopo:** `docker-push-webhook-action` v2.0.0 (quebra de compatibilidade com a v1, aceita: ninguém usa a v1 ainda)

---

## 1. Objetivo

Qualquer pessoa, **sem noção de CI/CD**, instala o build + push para o registry da empresa em outro repositório **sem errar**. Para isso:

1. O uso mínimo da action é curto (poucos campos, defaults inteligentes).
2. Toda configuração errada falha cedo, com mensagem em português dizendo **o que fazer**.
3. Existem dois caminhos de instalação que produzem o mesmo workflow: **navegador** (copiar arquivo) e **terminal** (`install.sh`).
4. Há um `llms.txt` para que uma IA instale corretamente quando a pessoa pedir.

### Origem do problema

Na primeira instalação real (repo `scimgateway-entraid-modified`) o job falhou com `unauthorized` no Docker Hub. Causa raiz: `REGISTRY_URL` foi cadastrado como *Secret*, mas o workflow lia `vars.REGISTRY_URL` (Variable), chegando vazio; com host vazio o `docker login` foi para o Docker Hub. Dois defeitos de desenho da v1:

- `image` exigia o host dentro do nome, o que obrigava o workflow a montar o nome com `vars.*` num `matrix` (onde `secrets.*` não existe).
- Registry vazio caía em silêncio no Docker Hub.

### Fora do escopo

| Item | Quando adicionar |
|---|---|
| Multi-plataforma, cache, build secrets | Mesmo critério da v1: quando houver pedido |
| Instalador para Windows nativo | Quando houver pedido (WSL/Git Bash funcionam) |
| Cadastro de secrets via API sem `gh` | Quando houver pedido (navegador já cobre) |

## 2. Interface da action (`action.yml`)

### Mudanças em relação à v1

| Campo | v1 | v2 |
|---|---|---|
| `image` | obrigatório, nome completo | **removido** |
| `name` | — | **novo**, opcional. Nome curto, pode ter caminho (`qriar/app`). Padrão: nome do repositório |
| `registry_url` | opcional, vazio = Docker Hub | **obrigatório**. Docker Hub = escrever `docker.io` |
| `registry_username` / `registry_password` | obrigatórios | iguais, mas validados contra valor vazio (secret inexistente chega como vazio) |
| `tags` | padrão `latest` | padrão `latest, <github.sha>` |
| demais (`context`, `dockerfile_path`, `build_args`, `webhook_*`) | — | **sem mudança** |

Imagem final: `<registry_url normalizado>/<name normalizado>`.

### Normalização (o que a pessoa colar é limpo, não rejeitado)

- `registry_url`: remove espaços, prefixo `http://`/`https://` e `/` finais. Se sobrar caminho (`host/ns`), ele é mantido como prefixo da imagem.
- `name`: convertido para minúsculas (o Docker rejeita maiúsculas). Quando houve conversão, imprime um aviso informando o nome final.
- Regras de segurança da v1 continuam: sem espaço/controle em `name`, sem tag/digest em `name`, tags validadas por regex, sem quebra de linha em `dockerfile_path`/`context`.

### Validação antecipada (primeiro passo, antes de login/build)

Cada falha usa `::error::` em português: **o que está errado + onde corrigir**.

| Condição | Mensagem (resumo) |
|---|---|
| `registry_url` vazio | "O secret REGISTRY_URL está vazio ou não existe. Cadastre em Settings > Secrets and variables > Actions > **Secrets**." |
| `registry_username` vazio | idem para `REGISTRY_USERNAME` |
| `registry_password` vazio | idem para `REGISTRY_PASSWORD` |
| Dockerfile não encontrado | informa o caminho procurado e como ajustar `context`/`dockerfile_path` |
| Tag inválida | mostra a tag e os caracteres permitidos |

Os nomes dos secrets citados nas mensagens são os do template padrão; o texto diz "se você usou outro nome, ajuste".

### Resumo da execução

Ao final do push, escreve em `$GITHUB_STEP_SUMMARY`: imagem, tags, digest. Sem credenciais, sem URL/headers do webhook.

### Versionamento

Lançar `v2.0.0` e mover a tag `v2`. A `v1` permanece como está. Templates e docs apontam para `@v2`.

## 3. Instalação

Três artefatos na raiz do repo da action, mantidos **consistentes entre si** (ver seção 5).

### 3.1 `templates/docker.yml` (caminho navegador)

Workflow pronto e comentado em português (mesmo estilo do workflow já usado no `scimgateway-entraid-modified`): dispara em push na `main` e `workflow_dispatch`; `run-name` curto fixo; 3 secrets; `uses: ...@v2`; sem `matrix`. Inclui um bloco comentado mostrando como publicar mais de uma imagem (`matrix` com `name`/`context`/`dockerfile_path`).

### 3.2 `INSTALAR.md`

Passo a passo para quem só usa o GitHub web:

1. Cadastrar os 3 secrets (nomes exatos, onde clicar).
2. *Add file > Create new file* em `.github/workflows/docker.yml` e colar o template.
3. Rodar em *Actions > Docker > Run workflow*.
4. Tabela "erro → o que fazer".

Inclui a nota sobre secrets de organização (ver 3.4).

### 3.3 `install.sh` (caminho terminal)

Executado na raiz do repo de destino: `bash <(curl -fsSL https://raw.githubusercontent.com/invicto-dev/docker-push-webhook-action/v2/install.sh)`.

Comportamento:

1. Pré-checagens: `gh` instalado e autenticado; dentro de um repo git com remote GitHub. Falhas dizem como resolver (`brew install gh`, `gh auth login`).
2. Detecta Dockerfiles com `git ls-files` (ignora `node_modules`). Um só → workflow simples. Vários → gera `matrix` (nome da imagem derivado da pasta, editável).
3. Pergunta host, usuário e senha (`read -s`, sem eco). Senha vai por stdin para `gh secret set` (nunca em argv/log). Webhook é opcional.
4. Cadastra `REGISTRY_URL`, `REGISTRY_USERNAME`, `REGISTRY_PASSWORD` (e `DEPLOY_WEBHOOK_URL` se informado) **por repositório** (padrão).
5. Escreve `.github/workflows/docker.yml`. Se o arquivo já existe, pergunta antes de sobrescrever.
6. **Não faz commit nem push** por padrão: mostra o próximo passo e pergunta se quer fazer.
7. Idempotente: rodar de novo não duplica nada além de atualizar secrets.

### 3.4 Secrets de organização (opcional)

`install.sh --org <org>` cadastra os secrets no nível da organização, para configurar uma vez e reutilizar em vários repositórios.

**Limitação a documentar e checar:** no plano GitHub Free, secrets de organização **não são acessíveis por repositórios privados** (funcionam em públicos). O script, com `--org`, consulta o plano da organização e a visibilidade do repo; em Free + privado, avisa que não vai funcionar e pergunta se quer continuar. Padrão do projeto continua sendo secret por repositório. (Fonte: documentação "Using secrets in GitHub Actions"; o comportamento de planos pagos não ficou claro e deve ser confirmado antes de afirmar na doc.)

## 4. `llms.txt`

Na raiz do repo. Conteúdo:

- O que a action faz, em poucas linhas.
- Workflow mínimo copiável.
- Os 3 secrets obrigatórios (nomes exatos) e onde cadastrar; secret **por repositório** por padrão, organização só em plano pago ou repo público.
- Tabela de inputs.
- **Regras para a IA:** nunca colocar senha/token no YAML; nunca usar `vars.*` para credenciais; usar `@v2`; não inventar inputs que não existem; não rodar `git push` sem a pessoa pedir.
- Erros comuns: mensagem → causa → correção (mesma tabela do `INSTALAR.md`).

## 5. Testes

Mantém o estilo atual (`test.yml` e2e + `test_webhook.py`) e adiciona:

1. **Validações no `test.yml`**, cada caso esperando falha com a mensagem certa: `registry_url` vazio, senha vazia, Dockerfile inexistente, tag inválida. Casos de normalização esperando sucesso: nome com maiúsculas é convertido; host colado com `https://` e `/` final é limpo.
2. **Teste do `install.sh` sem rede**: repo temporário, `gh` simulado (script no `PATH`). Verifica: workflow gerado passa no `actionlint`; usa `matrix` com mais de um Dockerfile; não sobrescreve arquivo existente sem confirmação; senha nunca aparece em argv nem na saída; `--org` em Free + privado emite o aviso.
3. **Consistência da documentação**: um script confere que `templates/docker.yml` e o exemplo do `llms.txt` só usam inputs definidos no `action.yml` e os mesmos nomes de secrets.
4. **Teste real contra o registry** (`connectors-hub.qriardemo.digital`): manual, uma vez, antes de lançar `v2.0.0`. Não entra no CI automático por exigir credenciais.

## 6. Riscos e pontos de atenção

- `docker login docker.io` e `docker push docker.io/<user>/<app>` precisam ser testados para confirmar que o caminho do Docker Hub funciona com o host explícito.
- `curl | bash` é prático mas exige confiar na fonte; o README sugere baixar e ler o script antes, e o `install.sh` fica pequeno e legível.
- Mensagens em português do Brasil são uma escolha de público; o README técnico segue em inglês.
