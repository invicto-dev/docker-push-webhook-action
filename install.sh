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
        unique_name "$(slug "$(basename "$root")")"
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
      echo "          context: $(yaml_path "$d")   # se o build não achar arquivos, troque por ."
      echo "          dockerfile_path: ${dockerfiles[0]}"
    fi
  fi
} > "$WORKFLOW"

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
