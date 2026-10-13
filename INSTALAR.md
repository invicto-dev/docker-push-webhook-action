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
