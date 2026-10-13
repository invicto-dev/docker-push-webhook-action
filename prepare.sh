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
  echo "dockerfile=$dockerfile"
  echo "docker_config=$docker_config"
  echo "refs<<EOF"
  printf '%s\n' "${refs[@]}"
  echo "EOF"
} >> "$GITHUB_OUTPUT"
