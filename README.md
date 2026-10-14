# Docker Push & Webhook

Build and push a Docker image to your registry with the native Docker CLI, then optionally call a webhook (for example, to trigger a deploy). No third-party actions, no extra dependencies.

**Fastest setup:** in the root of the repository that should get the CI, with the [GitHub CLI](https://cli.github.com) logged in, run:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/invicto-dev/docker-push-webhook-action/v2/install.sh)
```

It asks for the registry address, username and password (plus optional Teams and Portainer webhooks), stores them as GitHub Variable/Secrets and writes `.github/workflows/docker.yml` from [templates/docker.yml](templates/docker.yml) on your default branch. The template builds `./Dockerfile`; for other layouts, fill in `context`/`dockerfile_path` (or the matrix block). Answer yes to "Vai usar uma IA?" and it drops [llms.txt](llms.txt) next to the workflow for your assistant to do it. On Windows, run it in Git Bash. Step-by-step guide in Portuguese: [INSTALAR.md](INSTALAR.md). Using an AI assistant? Point it to [llms.txt](llms.txt).

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
          teams_webhook_url: ${{ secrets.TEAMS_WEBHOOK_URL }}
          webhook_url: ${{ secrets.PORTAINER_WEBHOOK_URL }}
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
| `webhook_url` | no | `''` | URLs to POST to after a successful push, one per line. Empty lines are ignored; empty skips the webhook. Accepts placeholders (see [Webhooks](#webhooks)). |
| `teams_webhook_url` | no | `''` | Microsoft Teams Workflows webhook URLs, one per line. Each gets an Adaptive Card (see [Microsoft Teams](#microsoft-teams)). |
| `webhook_headers` | no | `{}` | JSON object of string headers. Values are masked in logs. |
| `webhook_payload` | no | `''` | Request body, sent as given with placeholders filled in. Empty sends the default JSON (see [Webhooks](#webhooks)). `Content-Type` defaults to `application/json`. |
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

## Webhooks

`webhook_url` takes one URL per line. After a successful push, each one receives a `POST`, in order. Empty lines are ignored, so a secret that does not exist yet simply skips that destination:

```yaml
webhook_url: |
  ${{ secrets.PORTAINER_WEBHOOK_URL }}
  ${{ secrets.OTHER_WEBHOOK_URL }}
```

Without `webhook_payload`, every destination receives this JSON, built after the push:

```json
{
  "repository": "acme/app",
  "image": "registry.example.com/app",
  "version": "2026.10.14-1532Z-c14ae67",
  "tags": ["latest", "c14ae67b85586abfa9fa7fbed1998d35bb9719d8", "2026.10.14-1532Z-c14ae67"],
  "digest": "sha256:15b6ff5249a174ad0f7dc406b8fcd0950e323d1c316c12f08102a892091281fd",
  "commit": "c14ae67b85586abfa9fa7fbed1998d35bb9719d8",
  "commit_message": "fix: retry connector login",
  "author": "Ana Souza",
  "run_url": "https://github.com/acme/app/actions/runs/123456789"
}
```

`commit_message` and `author` are empty when there is no git history (no `actions/checkout`). `version` is empty when you set `tags`.

Placeholders, filled in every URL line and in a custom `webhook_payload`:

| Placeholder | Value |
|---|---|
| `{version}` | version tag (e.g. `2026.10.14-1532Z-c14ae67`) |
| `{digest}` | image digest |
| `{image}` | image without tag |
| `{sha}` | full commit SHA |

In URLs the value is URL-encoded. Unknown placeholders are left as they are. Example, a Portainer service webhook that deploys the exact version: store `https://portainer.example.com/api/webhooks/<id>?tag={version}` in `PORTAINER_WEBHOOK_URL`.

### Microsoft Teams

Teams channel webhooks (app **Workflows**, template "Post to a channel when a webhook request is received") only accept an Adaptive Card, not arbitrary JSON. Put those URLs in `teams_webhook_url` (one per line) instead of `webhook_url`:

```yaml
teams_webhook_url: ${{ secrets.TEAMS_WEBHOOK_URL }}
```

Each one receives a card titled "Imagem publicada: <name>" with the version (UTC, for rollback), the full image reference, the commit and its message, the author, the repository and a "Ver execução no GitHub" button. `webhook_headers` are never sent to Teams URLs. The log names them `Teams 1/1`, `Teams 2/2`, and so on.

### Custom Power Automate flows

For your own flow ("When an HTTP request is received") in `webhook_url`, use this request body schema to get every field as dynamic content:

```json
{
  "type": "object",
  "properties": {
    "repository": {"type": "string"},
    "image": {"type": "string"},
    "version": {"type": "string"},
    "tags": {"type": "array", "items": {"type": "string"}},
    "digest": {"type": "string"},
    "commit": {"type": "string"},
    "commit_message": {"type": "string"},
    "author": {"type": "string"},
    "run_url": {"type": "string"}
  }
}
```

Behavior:

- Always `POST`, 30-second timeout per destination. Redirects are not followed and count as failures.
- Every URL and header is validated before anything is sent; a configuration error sends nothing.
- A failing destination does not stop the others. The job fails at the end (or only warns with `webhook_fail_on_error: false`).
- The log names destinations only by position (`Webhook 2/2 falhou: HTTP 500`). URLs, headers, payload and response bodies are never printed.
- `webhook_url` must not embed credentials (`https://user:pass@host`); send them in `webhook_headers` instead. Headers go to every destination.
- Since v2.3.0, an empty `webhook_payload` sends the default JSON (it used to send no body).

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
