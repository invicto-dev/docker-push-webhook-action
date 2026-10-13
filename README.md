# Docker Push & Webhook

Build and push a Docker image with the native Docker CLI, then optionally call a webhook (for example, to trigger a deploy). No third-party actions, no extra dependencies.

## Requirements

A Linux runner with Docker and Python 3, such as `ubuntu-latest`. GitHub-hosted macOS and Windows runners do not ship Docker.

## Usage

### Docker Hub

```yaml
- uses: actions/checkout@v4
- uses: invicto-dev/docker-push-webhook-action@v1
  with:
    registry_username: ${{ secrets.DOCKERHUB_USERNAME }}
    registry_password: ${{ secrets.DOCKERHUB_TOKEN }}
    image: myuser/myapp
    tags: latest, ${{ github.sha }}
```

### GHCR with a deploy webhook

```yaml
- uses: actions/checkout@v4
- uses: invicto-dev/docker-push-webhook-action@v1
  with:
    registry_url: ghcr.io
    registry_username: ${{ github.actor }}
    registry_password: ${{ secrets.GITHUB_TOKEN }}
    image: ghcr.io/${{ github.repository }}
    tags: |
      latest
      ${{ github.sha }}
    context: .
    dockerfile_path: ./apps/api
    build_args: |
      APP_VERSION=${{ github.sha }}
    webhook_url: ${{ secrets.DEPLOY_WEBHOOK_URL }}
    webhook_headers: '{"Authorization": "Bearer ${{ secrets.DEPLOY_TOKEN }}"}'
    webhook_payload: '{"image": "ghcr.io/${{ github.repository }}:${{ github.sha }}"}'
```

Add `permissions: packages: write` to the job when pushing to GHCR with `GITHUB_TOKEN`.

## Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `registry_url` | no | `''` | Registry host (e.g. `ghcr.io`). Empty means Docker Hub. |
| `registry_username` | yes | | Registry username. |
| `registry_password` | yes | | Registry password or token. Sent via stdin. |
| `image` | yes | | Full image name without tag (e.g. `ghcr.io/org/app`). |
| `tags` | no | `latest` | Tags separated by commas and/or newlines. |
| `context` | no | `.` | Build context. |
| `dockerfile_path` | no | `''` | Dockerfile path, or a directory containing one. Empty means `<context>/Dockerfile`. |
| `build_args` | no | `''` | One `KEY=VALUE` per line. |
| `webhook_url` | no | `''` | URL to POST to after a successful push. Empty skips the webhook. |
| `webhook_headers` | no | `{}` | JSON object of string headers. Values are masked in logs. |
| `webhook_payload` | no | `''` | Request body, sent exactly as given. `Content-Type` defaults to `application/json`. |
| `webhook_fail_on_error` | no | `true` | Fail the job on network errors, timeouts or non-2xx responses. Invalid `webhook_url`/`webhook_headers` always fail. |

## Outputs

| Output | Description |
|---|---|
| `image` | First pushed reference (`image:tag`). |
| `tags` | All pushed references, one per line. |
| `digest` | Digest of the pushed image (`sha256:...`). |

## Webhook behavior

- Always `POST`, 30-second timeout. Redirects are not followed and count as failures.
- The webhook runs only after a successful push and cannot reference this action's own outputs. Image and tags are already known to the caller. The digest is not available inside the payload.
- URL, headers, payload and response body are never printed in the logs.

## Security notes

- **Do not pass secrets in `build_args`.** Build args are stored in the image history.
- Registry credentials are kept in a temporary Docker config that is deleted at the end of the action, even on failure. Your job's default Docker config is never touched.
- Inputs are passed to scripts as environment variables, never interpolated into shell code.

## License

MIT
