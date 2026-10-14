#!/usr/bin/env bash
# Instala o CI de build + push Docker no repositório atual.
# Rode na raiz do repositório:
#   bash install.sh             # secrets cadastrados neste repositório (padrão)
#   bash install.sh --org ORG   # secrets cadastrados na organização (veja INSTALAR.md)
# Nada é commitado nem enviado sem a sua confirmação.
# Ordem das perguntas: [sobrescrever o workflow existente?] [com --org, plano gratuito +
# repo privado: continuar?] [com --org: "Continuar?" (senha vale p/ TODOS os repos da org)]
# endereço, usuário, senha, webhook, [fazer commit e push?].
# Recusar a sobrescrita mantém o workflow, mas os secrets ainda são cadastrados.
set -euo pipefail

ACTION="invicto-dev/docker-push-webhook-action@v2"
WORKFLOW=".github/workflows/docker.yml"
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
printf 'URL do webhook do Teams (Power Automate) (opcional, Enter para pular): '
IFS= read -r hook || hook=""
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

# Image name from the Dockerfile folder: "config/docker" -> "config-docker".
# Always a valid Docker name (lowercase letters, digits, single dashes, starting
# with a letter/digit) that YAML reads as a string; "image" when nothing is left.
slug() {
  local s
  s="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9' '-' | tr -s '-')"
  s="${s#-}"
  s="${s%-}"
  [[ -n "$s" ]] || s="image"
  # YAML would read these as number/boolean/null, not text.
  case "$s" in
    [0-9]*|true|false|yes|no|on|off|null|y|n) s="img-$s" ;;
  esac
  printf '%s' "$s"
}
# Same for a context path: "./" keeps keyword-like folders (true, null, 1.0) as text.
yaml_path() {
  case "$1" in
    [0-9]*|true|false|yes|no|on|off|null|y|n|True|False|Null|TRUE|FALSE|NULL|Yes|No|YES|NO|On|Off|ON|OFF|~) printf './%s' "$1" ;;
    *) printf '%s' "$1" ;;
  esac
}
dir_of() { local d; d="$(dirname "$1")"; printf '%s' "$d"; }
# Unique image names across the matrix: a, a-2, a-3 ... (plain delimited string, Bash 3.2).
# Sets $img (no subshell, so $used persists).
used=" "
unique_name() {
  local base="$1" n=2
  img="$base"
  while [[ "$used" == *" $img "* ]]; do
    img="$base-$n"
    n=$((n + 1))
  done
  used="$used$img "
}

if (( ! write_workflow )); then
  echo "$WORKFLOW mantido como estava. Secrets atualizados."
  exit 0
fi

mkdir -p "$(dirname "$WORKFLOW")"
{
  cat <<'EOF'
# Publica (envia) a imagem Docker deste projeto para o registry da empresa.
# Gerado por install.sh. REGISTRY_URL fica em Settings > Secrets and variables >
# Actions > Variables (aceita também Secret); REGISTRY_USERNAME e REGISTRY_PASSWORD
# ficam na aba Secrets.

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
        unique_name "$(slug "${repo#*/}")"
        ctx="."
      else
        unique_name "$(slug "$d")"
        ctx="$(yaml_path "$d")"
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
          registry_url: \${{ vars.REGISTRY_URL || secrets.REGISTRY_URL }}
          registry_username: \${{ secrets.REGISTRY_USERNAME }}
          registry_password: \${{ secrets.REGISTRY_PASSWORD }}
          webhook_url: |
            \${{ secrets.TEAMS_WEBHOOK_URL }}
            \${{ secrets.PORTAINER_WEBHOOK_URL }}
EOF
  if (( count > 1 )); then
    echo '          name: ${{ matrix.name }}'
    echo '          context: ${{ matrix.context }}'
    echo '          dockerfile_path: ${{ matrix.dockerfile }}'
  else
    d="$(dir_of "${dockerfiles[0]}")"
    if [[ "$d" != "." ]]; then
      echo "          context: $(yaml_path "$d")   # se o build não achar arquivos, troque por ."
      echo "          dockerfile_path: ${dockerfiles[0]}"
    fi
  fi
} > "$WORKFLOW"

# Vazio = sem mudança (novo, alterado ou staged aparecem aqui).
if [[ -z "$(git status --porcelain -- "$WORKFLOW")" ]]; then
  echo "workflow sem mudanças: nada para commitar."
  exit 0
fi

echo
echo "Pronto: $WORKFLOW criado."
echo "Confira o campo 'context' de cada imagem no arquivo: se o build falhar procurando arquivos, troque por '.'."
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
else
  echo "Quando quiser: git add $WORKFLOW && git commit -m 'ci: add docker workflow' && git push"
fi
