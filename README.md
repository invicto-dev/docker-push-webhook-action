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
    registry_url: ${{ vars.REGISTRY_URL || secrets.REGISTRY_URL }}
    registry_username: ${{ secrets.REGISTRY_USERNAME }}
    registry_password: ${{ secrets.REGISTRY_PASSWORD }}
```

Pushes `<registry_url>/<repository name>` with three tags: `latest`, the full commit SHA and a version tag such as `2026.10.14-1532Z-c14ae67` (see [Tags and rollback](#tags-and-rollback)).

`REGISTRY_URL` is not a secret: store it as a repository **Variable** so the registry host shows up in logs and in the job summary (a Secret works too, but GitHub masks it as `***`). The `vars.REGISTRY_URL || secrets.REGISTRY_URL` expression accepts either. Keep the username and password as Secrets.

### Docker Hub

The image name must include your user (`name: myuser/myapp`); otherwise validation fails.

```yaml
- uses: actions/checkout@v4
- uses: invicto-dev/docker-push-webhook-action@v2
  with:
    registry_url: docker.io
    registry_username: ${{ secrets.REGISTRY_USERNAME }}
    registry_password: ${{ secrets.REGISTRY_PASSWORD }}
    name: myuser/myapp
```

### GitHub Container Registry

Add `permissions: packages: write` to the job when pushing to GHCR with `GITHUB_TOKEN`.

```yaml
jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - uses: invicto-dev/docker-push-webhook-action@v2
        with:
          registry_url: ghcr.io
          registry_username: ${{ github.actor }}
          registry_password: ${{ secrets.GITHUB_TOKEN }}
          name: ${{ github.repository }}
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
          registry_url: ${{ vars.REGISTRY_URL || secrets.REGISTRY_URL }}
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

## Tags and rollback

With the default `tags`, every image gets three tags:

| Tag | Example | Use it for |
|---|---|---|
| `latest` | `latest` | "the newest build". It moves on every build, so never roll back to it. |
| full commit SHA | `c14ae67b85586abfa9fa7fbed1998d35bb9719d8` | exact traceability and deploy tools that pin by SHA. |
| version | `2026.10.14-1532Z-c14ae67` | **rollback**. Pick the version right before the broken one. |

The version tag is `YYYY.MM.DD-HHMMZ-<short SHA>`:

- The date and time are the **commit date in UTC** (the `Z` means UTC; Brasília is UTC-3, so `1532Z` is 12:32 in Brasília).
- Alphabetical order is chronological order, so the registry list is already sorted by time.
- It comes from the commit, not from the build, so every image of the same commit (matrix jobs, re-runs) gets the same version tag.
- Without git history for the commit (no `actions/checkout`), the build time in UTC is used and the log says so.

The job summary shows the version of each run. Custom `tags` replace all three defaults (the `version` output is then empty).

## Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `registry_url` | yes | | Registry host (e.g. `registry.example.com`, `ghcr.io`, `docker.io`). Scheme and trailing slash are removed. |
| `registry_username` | yes | | Registry username. |
| `registry_password` | yes | | Registry password or token. Sent via stdin. |
| `name` | no | repository name | Short image name without registry or tag (e.g. `my-app`, `team/my-app`). Lowercased automatically. |
| `tags` | no | `latest`, the commit SHA and the version tag | Tags separated by commas and/or newlines. Setting it replaces all three defaults. |
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
| `version` | Version tag, e.g. `2026.10.14-1532Z-c14ae67`. Empty when `tags` is set. |
| `digest` | Digest of the pushed image (`sha256:...`). |

A summary with the pushed references and the digest is also added to the job summary.

## Webhook behavior

- Always `POST`, 30-second timeout. Redirects are not followed and count as failures.
- The webhook runs only after a successful push and cannot reference this action's own outputs. Image and the `latest`/SHA tags are already known to the caller; the version tag and the digest are not available inside the payload (read them from the `version`/`digest` outputs in a later step).
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
