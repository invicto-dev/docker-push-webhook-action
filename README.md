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
