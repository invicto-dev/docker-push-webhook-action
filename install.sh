#!/usr/bin/env bash
# Instala o CI de build + push Docker no repositório atual.
# Rode na raiz do repositório (sem baixar nada):
#   bash <(curl -fsSL https://raw.githubusercontent.com/invicto-dev/docker-push-webhook-action/v2/install.sh)
# ou, com o arquivo baixado:
#   bash install.sh             # secrets cadastrados neste repositório (padrão)
#   bash install.sh --org ORG   # secrets cadastrados na organização (veja INSTALAR.md)
# No Windows, rode no Git Bash (não no PowerShell nem no CMD).
# O script só cadastra a variável e os secrets e grava o workflow a partir de
# templates/docker.yml. Onde fica cada Dockerfile quem decide é você (ou a sua IA).
# Nada é commitado nem enviado sem a sua confirmação.
# Ordem das perguntas: [sobrescrever o workflow existente?] [com --org, plano gratuito +
# repo privado: continuar?] [com --org: "Continuar?" (senha vale p/ TODOS os repos da org)]
# endereço, usuário, senha, webhook do Teams, webhook do Portainer, vai usar IA?,
# [fazer commit e push? (só com Dockerfile na raiz)].
# Recusar a sobrescrita mantém o workflow, mas os secrets ainda são cadastrados.
set -euo pipefail

RAW="https://raw.githubusercontent.com/invicto-dev/docker-push-webhook-action/v2"
WORKFLOW=".github/workflows/docker.yml"
LLMS_COPY=".github/docker-push-webhook-action.llms.txt"
# Pasta deste script. Rodando por "bash <(curl ...)" ela não tem os arquivos da action, e eles são baixados.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"
org=""

die() {
  echo "ERRO: $1" >&2
  exit 1
}

# confirm "pergunta": retorna 0 só para s/sim/y/yes, em qualquer caixa (EOF ou outra coisa = não).
confirm() {
  local ans yes_re='^([sSyY]|[sS][iI][mM]|[yY][eE][sS])$'
  printf '%s [s/N] ' "$1"
  read -r ans || ans=""
  [[ "$ans" =~ $yes_re ]]
}

# asset "caminho": conteúdo de um arquivo da action (local ao lado do script, ou baixado da v2).
asset() {
  if [[ -n "$here" && -f "$here/$1" ]]; then
    cat "$here/$1"
  else
    curl -fsSL "$RAW/$1" || die "Não consegui baixar $RAW/$1. Confira a internet e rode de novo."
  fi
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

command -v gh >/dev/null || die "GitHub CLI (gh) não encontrado. Instale e rode de novo:
  macOS:   brew install gh
  Windows: winget install --id GitHub.cli   (depois abra um Git Bash novo)
  Linux:   siga https://cli.github.com (o 'gh' do apt do Ubuntu 22.04 é antigo demais)"
# 'gh variable' existe desde a versão 2.25; distribuições antigas (ex.: apt do Ubuntu 22.04) trazem a 2.4.
gh variable --help >/dev/null 2>&1 || die "Seu GitHub CLI é antigo demais ($(gh --version 2>/dev/null | head -1)). Atualize pela página oficial: https://cli.github.com"
gh auth status >/dev/null 2>&1 || die "Você não está logado no GitHub CLI. Rode: gh auth login"
root="$(git rev-parse --show-toplevel 2>/dev/null)" || die "Rode este script dentro de um repositório git."
cd "$root"
repo="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null)" || die "Não consegui identificar o repositório no GitHub. Ele tem um remote 'origin'?"
branch="$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name 2>/dev/null || true)"
[[ -n "$branch" ]] || branch="main"

# Só para informar: quem decide quais imagens publicar é a pessoa, no workflow.
dockerfiles="$(git ls-files --cached --others --exclude-standard |
  grep -E '(^|/)(Dockerfile([.][^/]*)?|[^/]*[.][Dd]ockerfile)$' | grep -v -e 'node_modules/' -e '[.]dockerignore$' || true)"
[[ -n "$dockerfiles" ]] || die "Nenhum Dockerfile encontrado neste repositório."

write_workflow=1
if [[ -f "$WORKFLOW" ]]; then
  confirm "$WORKFLOW já existe. Sobrescrever?" || write_workflow=0
fi

if [[ -n "$org" ]]; then
  plan="$(gh api "orgs/$org" --jq .plan.name 2>/dev/null || echo desconhecido)"
  private="$(gh repo view --json isPrivate --jq .isPrivate 2>/dev/null || echo true)"
  if [[ "$plan" == "free" && "$private" == "true" ]]; then
    echo "AVISO: a organização '$org' está no plano gratuito e este repositório é privado."
    echo "No plano gratuito, secrets de organização NÃO chegam a repositórios privados."
    confirm "Continuar mesmo assim?" || { echo "Cancelado. Rode de novo sem --org."; exit 0; }
  fi
  echo "AVISO: com --org, TODOS os repositórios da organização '$org' (inclusive públicos) poderão usar a senha do registry."
  confirm "Continuar?" || { echo "Cancelado. Nada foi cadastrado."; exit 0; }
fi

printf 'Endereço do registry (ex: registry.empresa.com): '
IFS= read -r host || host=""
printf 'Usuário do registry: '
IFS= read -r user || user=""
printf 'Senha ou token do registry (não aparece na tela): '
IFS= read -rs pass || pass=""
echo
printf 'Link do webhook do Teams (opcional, Enter para pular): '
IFS= read -r hook || hook=""
printf 'Webhook do Portainer para deploy automático (opcional, Enter para pular): '
IFS= read -r portainer || portainer=""
[[ -n "${host//[[:space:]]/}" && -n "$user" && -n "$pass" ]] || die "Endereço, usuário e senha do registry são obrigatórios."

set_secret() {
  {
    if [[ -n "$org" ]]; then
      printf '%s' "$2" | gh secret set "$1" --org "$org" --visibility all
    else
      printf '%s' "$2" | gh secret set "$1" --repo "$repo"
    fi
  } >/dev/null || die "Não consegui cadastrar o secret $1. Você precisa ser admin do repositório (ou owner da organização com --org)."
  echo "  secret $1 cadastrado"
}
set_variable() {
  {
    if [[ -n "$org" ]]; then
      gh variable set "$1" --body "$2" --org "$org" --visibility all
    else
      gh variable set "$1" --body "$2" --repo "$repo"
    fi
  } >/dev/null || die "Não consegui cadastrar a variável $1. Você precisa ser admin do repositório (ou owner da organização com --org)."
  echo "  variável $1 cadastrada"
}
set_variable REGISTRY_URL "$host"
set_secret REGISTRY_USERNAME "$user"
set_secret REGISTRY_PASSWORD "$pass"
if [[ -n "$hook" ]]; then set_secret TEAMS_WEBHOOK_URL "$hook"; fi
if [[ -n "$portainer" ]]; then set_secret PORTAINER_WEBHOOK_URL "$portainer"; fi

if (( ! write_workflow )); then
  echo "$WORKFLOW mantido como estava. Secrets atualizados."
  exit 0
fi

# O template da action, só com a branch trocada pela branch padrão do repositório.
template="$(asset templates/docker.yml)"
mkdir -p "$(dirname "$WORKFLOW")"
shopt -u patsub_replacement 2>/dev/null || true  # bash 5.2+: "&" na branch não vira o texto encontrado
old='branches: [main]' new="branches: [$branch]"
template=${template//"$old"/$new}
old='na branch "main"' new="na branch \"$branch\""
printf '%s\n' "${template//"$old"/$new}" > "$WORKFLOW"
echo
echo "Pronto: $WORKFLOW criado."

if confirm "Vai usar uma IA (Claude Code, Copilot, Cursor...) para ajustar o workflow?"; then
  asset llms.txt > "$LLMS_COPY"
  echo "Dica: peça para a sua IA: \"Leia $LLMS_COPY e ajuste $WORKFLOW para os Dockerfiles deste repositório.\""
  echo "($LLMS_COPY é só para a IA; não precisa commitar.)"
fi

if [[ ! -f Dockerfile ]]; then
  echo
  echo "Não há Dockerfile na raiz. Dockerfiles neste repositório:"
  sed 's/^/  /' <<<"$dockerfiles"
  echo "Antes do commit, abra $WORKFLOW e preencha 'context' e 'dockerfile_path'"
  echo "(ou o bloco de várias imagens, já explicado no arquivo). Depois:"
  echo "  git add $WORKFLOW && git commit -m 'ci: add docker workflow' && git push"
  exit 0
fi

# Vazio = sem mudança (novo, alterado ou staged aparecem aqui).
if [[ -z "$(git status --porcelain -- "$WORKFLOW")" ]]; then
  echo "workflow sem mudanças: nada para commitar."
  exit 0
fi

if confirm "Fazer commit e push agora?"; then
  git add "$WORKFLOW"
  # Só o workflow entra no commit: outros arquivos já preparados (staged) ficam de fora.
  git commit -m "ci: add docker build and push workflow" -- "$WORKFLOW" ||
    die "Não consegui fazer o commit. Rode você mesmo: git add $WORKFLOW && git commit -m 'ci: add docker workflow' -- $WORKFLOW && git push"
  git push || {
    echo "AVISO: o commit foi feito, mas o push falhou (sem remote, sem permissão ou sem internet?)." >&2
    echo "Quando resolver, envie com: git push" >&2
    exit 1
  }
  echo "Enviado. Acompanhe a publicação em: https://github.com/$repo/actions"
else
  echo "Quando quiser: git add $WORKFLOW && git commit -m 'ci: add docker workflow' && git push"
  echo "Depois do push, acompanhe a publicação em: https://github.com/$repo/actions"
fi
