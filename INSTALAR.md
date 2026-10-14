# Instalar o build e push de Docker em um repositório

## Instale com 1 comando

Abra o terminal **na pasta do repositório onde você quer o CI** e rode:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/invicto-dev/docker-push-webhook-action/v2/install.sh)
```

Funciona em qualquer repositório: é só entrar na pasta dele antes (`cd caminho/do/repo`). O script pergunta, uma coisa por vez:

1. endereço do registry (ex: `registry.empresa.com`)
2. usuário do registry
3. senha ou token do registry (não aparece na tela)
4. link do webhook do Teams (opcional, Enter para pular)
5. webhook do Portainer para deploy automático (opcional, Enter para pular)
6. se pode fazer commit e push

Ele cadastra tudo no GitHub, cria `.github/workflows/docker.yml` e mostra o link para acompanhar a publicação. Nada é enviado sem você confirmar.

**Antes, uma vez só:** instale e logue o [GitHub CLI](https://cli.github.com): `brew install gh` e depois `gh auth login`. Você precisa ser **admin** do repositório no GitHub (em repositório pessoal, ser o dono).

Quer ler o script antes de rodar? Abra [install.sh](install.sh): ele é curto.

Depois de rodar, confira o campo `context` de cada imagem no arquivo criado: se o build falhar procurando arquivos, troque por `.`.

---

<details>
<summary><b>Sem terminal? Instale pelo navegador</b></summary>

### 1. Cadastre a variável e os secrets

No repositório: **Settings > Secrets and variables > Actions**. Crie estes, com o nome **exatamente** assim:

Na aba **Variables** (botão **New repository variable**). O endereço não é segredo, então aparece normal nos logs e no resumo da execução:

| Nome | Valor |
|---|---|
| `REGISTRY_URL` | endereço do registry, por exemplo `registry.empresa.com` |

Na aba **Secrets** (botão **New repository secret**). Ficam escondidos:

| Nome | Valor |
|---|---|
| `REGISTRY_USERNAME` | usuário do registry |
| `REGISTRY_PASSWORD` | senha ou token do registry |
| `TEAMS_WEBHOOK_URL` | (opcional) link do webhook do Teams que avisa no canal depois do envio (veja "Avisar o Teams e o Portainer") |
| `PORTAINER_WEBHOOK_URL` | (opcional) webhook do Portainer para deploy automático. Para subir a versão exata, termine a URL com `?tag={version}` |

Se você cadastrar o `REGISTRY_URL` como Secret em vez de Variable, também funciona. A diferença é que o GitHub esconde o endereço (`***`) nos logs e no resumo.

Pode colar o endereço do registry como copiou (com `https://` ou barra no final): a action limpa isso. Para o Docker Hub, use `REGISTRY_URL` = `docker.io` **e** `name: SEU_USUARIO/app` na action (o nome precisa ter o seu usuário).

### 2. Crie o arquivo do workflow

No repositório, clique em **Add file > Create new file**. No nome do arquivo, digite `.github/workflows/docker.yml`. Cole dentro o conteúdo de [templates/docker.yml](templates/docker.yml) e clique em **Commit changes**.

### 3. Rode e confira

Abra a aba **Actions**, escolha **Docker** na lista da esquerda e clique em **Run workflow**. Quando ficar verde, a imagem está no registry.

</details>

## Consulta (só se precisar)

<details>
<summary><b>Avisar o Teams e o Portainer</b></summary>

Depois de cada envio, a action avisa o canal do Teams cadastrado em `TEAMS_WEBHOOK_URL` e o endereço cadastrado em `PORTAINER_WEBHOOK_URL` (deploy automático). Quem não estiver cadastrado é simplesmente pulado, então dá para criar o do Portainer depois, sem mexer no workflow (rode o comando de novo ou cadastre o secret pelo navegador).

**Criar o webhook do Teams:**

1. No Teams, abra o app **Fluxos de trabalho** (Workflows).
2. Escolha o modelo **"Postar em um canal quando uma solicitação de webhook for recebida"**, selecione a equipe e o canal e conclua.
3. Clique em **Copiar link do webhook** e cadastre o link como secret `TEAMS_WEBHOOK_URL`.

A action manda para o canal um cartão pronto com a versão para rollback (`2026.10.14-1532Z-c14ae67`, horário UTC), a imagem, o commit e a mensagem, quem fez e um botão para abrir a execução no GitHub. Não precisa mexer no fluxo.

</details>

<details>
<summary><b>Voltar para uma versão anterior (rollback)</b></summary>

Cada publicação gera uma tag de versão como `2026.10.14-1532Z-c14ae67`:

- `2026.10.14-1532` é a data e a hora do commit, em **UTC**. O `Z` no final quer dizer UTC. Para o horário de Brasília, **tire 3 horas**: `1532Z` é 12:32 em Brasília.
- `c14ae67` é o código curto do commit, para achar a mudança no GitHub.

Se uma versão deu problema, abra a lista de tags da imagem no registry (ela já fica em ordem de data) e use a **tag de versão logo anterior**. Não use `latest` para voltar, porque ela sempre aponta para a mais nova. O resumo de cada execução no GitHub (aba Actions) também mostra a versão publicada.

</details>

<details>
<summary><b>Secrets por repositório ou por organização</b></summary>

Por padrão, a variável e os secrets são cadastrados **por repositório** (é o que o comando e o caminho pelo navegador fazem). Cadastrar uma vez só na **organização** evita repetir em cada repositório. Para isso, rode o mesmo comando com `--org NOME-DA-ORG` no final (é preciso ser owner da organização):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/invicto-dev/docker-push-webhook-action/v2/install.sh) --org NOME-DA-ORG
```

Isso **só funciona em repositórios privados se a organização tiver plano pago**. No plano gratuito, variáveis e secrets de organização só chegam a repositórios públicos. O script avisa quando detecta essa combinação.

</details>

<details>
<summary><b>Deu erro? Tabela rápida</b></summary>

| Mensagem no log | O que significa | O que fazer |
|---|---|---|
| `O valor de REGISTRY_URL está vazio ou não existe` | O `REGISTRY_URL` não foi cadastrado, ou o nome está diferente | Cadastre na aba **Variables** (ou Secrets) com o nome exato |
| `O secret REGISTRY_USERNAME está vazio ou não existe` (ou `REGISTRY_PASSWORD`) | O secret não foi cadastrado, foi cadastrado como Variable, ou o nome está diferente | Cadastre na aba **Secrets** com o nome exato |
| `unauthorized: incorrect username or password` | Usuário ou senha do registry errados | Confira com `docker login <endereço>` no seu computador e recadastre os secrets |
| `Dockerfile não encontrado: ...` | A action não achou o Dockerfile | Ajuste `context` e `dockerfile_path` no workflow para o caminho certo |
| `COPY failed: file not found` (no build) | O `context` está apontando para a pasta errada | Troque `context` por `.` (raiz do repositório) |
| `Tag inválida: ...` | Uma tag tem espaço ou caractere não permitido | Use só letras, números, `_`, `.` e `-` |
| O workflow não aparece em **Actions** | O arquivo está no lugar errado | Ele precisa estar em `.github/workflows/docker.yml` na branch `main`, e o Actions deve estar habilitado em Settings > Actions |

</details>
