import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_docs import check_workflow

ROOT = Path(__file__).resolve().parent
BASH = shutil.which("bash")  # absolute path, resolved with the parent PATH
INSTALL = ROOT / "install.sh"

FAKE_GH = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_GH_LOG"
case "$1 $2" in
  "auth status") exit 0 ;;
  "--version "*) echo "gh version ${FAKE_GH_VERSION:-2.60.0}" ;;
  "variable --help") [ -z "$FAKE_GH_OLD" ] ;;
  "repo view")
    case "$*" in
      *isPrivate*) echo "${FAKE_GH_PRIVATE:-true}" ;;
      *defaultBranchRef*) echo "${FAKE_GH_BRANCH:-main}" ;;
      *) echo "${FAKE_GH_REPO:-acme/app}" ;;
    esac ;;
  "secret set") [ -n "$FAKE_GH_FAIL_SECRET" ] && { echo "HTTP 403: Resource not accessible" >&2; exit 1; }
    printf '%s=%s\\n' "$3" "$(cat)" >> "$FAKE_GH_SECRETS" ;;
  "variable set") printf '%s=%s\\n' "$3" "$5" >> "$FAKE_GH_VARS" ;;
  api*) echo "${FAKE_GH_PLAN:-free}" ;;
  *) exit 1 ;;
esac
"""

# Stands in for curl when install.sh runs alone (bash <(curl ...)): serves the files of this checkout.
FAKE_CURL = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_CURL_LOG"
url="${@: -1}"
cat "$ACTION_ROOT/${url#*/v2/}"
"""

PASSWORD = "p@ss \"w$rd'!"
TEMPLATE = (ROOT / "templates/docker.yml").read_text()
LLMS_COPY = ".github/docker-push-webhook-action.llms.txt"


class Sandbox:
    def __init__(self, files=("Dockerfile",), existing_workflow=None, env=None, repo_name="repo"):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / repo_name
        self.repo.mkdir()
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        curl = bin_dir / "curl"
        curl.write_text(FAKE_CURL)
        curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
        self.curl_log = self.tmp / "curl.log"
        self.curl_log.touch()
        self.log = self.tmp / "gh.log"
        self.secrets = self.tmp / "secrets"
        self.log.touch()
        self.secrets.touch()
        self.variables = self.tmp / "variables"
        self.variables.touch()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        for name in files:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("FROM scratch\n")
        if existing_workflow is not None:
            wf = self.repo / ".github/workflows/docker.yml"
            wf.parent.mkdir(parents=True)
            wf.write_text(existing_workflow)
        self.env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_CURL_LOG": str(self.curl_log),
            "ACTION_ROOT": str(ROOT),
            "HOME": str(self.tmp),
            "FAKE_GH_LOG": str(self.log),
            "FAKE_GH_SECRETS": str(self.secrets),
            "FAKE_GH_VARS": str(self.variables),
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }
        self.env.update(env or {})

    def run(self, answers, *args, script=INSTALL):
        proc = subprocess.run(
            [BASH, str(script), *args],
            cwd=self.repo,
            env=self.env,
            input=answers,
            capture_output=True,
            text=True,
        )
        self.stdout = proc.stdout + proc.stderr
        return proc.returncode

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, env={**os.environ, **self.env},
            capture_output=True, text=True, check=True,
        ).stdout

    def with_origin(self):
        """Local bare repo as origin, with upstream set, so `git push` works."""
        bare = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        self.git("remote", "add", "origin", str(bare))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "init")
        self.git("push", "-q", "-u", "origin", "HEAD")

    @property
    def workflow(self):
        return (self.repo / ".github/workflows/docker.yml").read_text()

    def recorded_variables(self):
        return dict(l.split("=", 1) for l in self.variables.read_text().splitlines())

    def recorded_secrets(self):
        return dict(l.split("=", 1) for l in self.secrets.read_text().splitlines())

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def answers(host="registry.acme.com", user="ci", password=PASSWORD, hook="", portainer="", ai="n", push="n"):
    return f"{host}\n{user}\n{password}\n{hook}\n{portainer}\n{ai}\n{push}\n"


class InstallTest(unittest.TestCase):
    def sandbox(self, **kw):
        sb = Sandbox(**kw)
        self.addCleanup(sb.cleanup)
        return sb

    def test_single_root_dockerfile(self):
        sb = self.sandbox()
        self.assertEqual(sb.run(answers()), 0, sb.stdout)
        check_workflow(self, sb.workflow, "workflow gerado")
        self.assertEqual(sb.recorded_variables(), {"REGISTRY_URL": "registry.acme.com"})
        self.assertEqual(
            sb.recorded_secrets(),
            {
                "REGISTRY_USERNAME": "ci",
                "REGISTRY_PASSWORD": PASSWORD,
            },
        )

    def test_password_with_special_characters_never_leaks(self):
        sb = self.sandbox()
        self.assertEqual(sb.run(answers()), 0, sb.stdout)
        self.assertEqual(sb.recorded_secrets()["REGISTRY_PASSWORD"], PASSWORD)
        self.assertNotIn(PASSWORD, sb.log.read_text())
        self.assertNotIn(PASSWORD, sb.stdout)

    def test_webhook_secret_only_when_given(self):
        sb = self.sandbox()
        self.assertEqual(sb.run(answers(hook="https://hook.acme.com/x")), 0, sb.stdout)
        self.assertEqual(sb.recorded_secrets()["TEAMS_WEBHOOK_URL"], "https://hook.acme.com/x")
        self.assertIn("Teams", sb.stdout)
        self.assertNotIn("PORTAINER_WEBHOOK_URL", sb.recorded_secrets())

    def test_portainer_secret_only_when_given(self):
        sb = self.sandbox()
        url = "https://portainer.acme.com/api/stacks/webhooks/x?tag={version}"
        self.assertEqual(sb.run(answers(portainer=url)), 0, sb.stdout)
        self.assertEqual(sb.recorded_secrets()["PORTAINER_WEBHOOK_URL"], url)
        self.assertIn("Portainer", sb.stdout)
        self.assertNotIn("TEAMS_WEBHOOK_URL", sb.recorded_secrets())

    def test_ends_with_link_to_the_actions_tab(self):
        sb = self.sandbox()
        self.assertEqual(sb.run(answers()), 0, sb.stdout)
        self.assertIn("https://github.com/acme/app/actions", sb.stdout)

    def test_workflow_is_the_template(self):
        sb = self.sandbox()
        self.assertEqual(sb.run(answers()), 0, sb.stdout)
        self.assertEqual(sb.workflow, TEMPLATE)

    def test_workflow_runs_on_the_default_branch(self):
        sb = self.sandbox(env={"FAKE_GH_BRANCH": "master"})
        self.assertEqual(sb.run(answers()), 0, sb.stdout)
        self.assertIn("branches: [master]", sb.workflow)
        self.assertNotIn("[main]", sb.workflow)
        self.assertEqual(sb.workflow.replace("master", "main"), TEMPLATE)

    def test_alone_downloads_the_template_from_v2(self):
        # bash <(curl .../install.sh) has no checkout next to it
        sb = self.sandbox()
        solo = sb.tmp / "solo"
        solo.mkdir()
        shutil.copy(INSTALL, solo / "install.sh")
        self.assertEqual(sb.run(answers(ai="s"), script=solo / "install.sh"), 0, sb.stdout)
        self.assertEqual(sb.workflow, TEMPLATE)
        self.assertEqual((sb.repo / LLMS_COPY).read_text(), (ROOT / "llms.txt").read_text())
        log = sb.curl_log.read_text()
        self.assertIn("raw.githubusercontent.com/invicto-dev/docker-push-webhook-action/v2/templates/docker.yml", log)
        self.assertIn("raw.githubusercontent.com/invicto-dev/docker-push-webhook-action/v2/llms.txt", log)

    def test_ai_question_is_always_asked(self):
        sb = self.sandbox()
        self.assertEqual(sb.run(answers()), 0, sb.stdout)
        self.assertIn("IA", sb.stdout)
        self.assertFalse((sb.repo / LLMS_COPY).exists())

    def test_ai_yes_brings_llms_txt_and_a_tip(self):
        sb = self.sandbox()
        self.assertEqual(sb.run(answers(ai="s")), 0, sb.stdout)
        self.assertEqual((sb.repo / LLMS_COPY).read_text(), (ROOT / "llms.txt").read_text())
        self.assertIn(LLMS_COPY, sb.stdout)
        self.assertIn(".github/workflows/docker.yml", sb.stdout)

    def test_without_root_dockerfile_lists_them_and_does_not_offer_commit(self):
        sb = self.sandbox(files=("config/docker/Dockerfile", "front/Dockerfile"))
        sb.with_origin()
        self.assertEqual(sb.run(answers(push="s")), 0, sb.stdout)
        self.assertIn("config/docker/Dockerfile", sb.stdout)
        self.assertIn("front/Dockerfile", sb.stdout)
        self.assertIn("context", sb.stdout)
        self.assertNotIn("commit e push agora", sb.stdout)
        self.assertEqual(sb.git("rev-list", "--count", "HEAD").strip(), "1")

    def test_dockerfile_variants_are_found(self):
        sb = self.sandbox(files=("Dockerfile.prod", "api.Dockerfile"))
        self.assertEqual(sb.run(answers()), 0, sb.stdout)
        self.assertIn("Dockerfile.prod", sb.stdout)
        self.assertIn("api.Dockerfile", sb.stdout)

    def test_old_gh_explains_how_to_update(self):
        sb = self.sandbox(env={"FAKE_GH_OLD": "1", "FAKE_GH_VERSION": "2.4.0"})
        self.assertNotEqual(sb.run(answers()), 0)
        self.assertIn("2.4.0", sb.stdout)
        self.assertIn("https://cli.github.com", sb.stdout)
        self.assertEqual(sb.recorded_secrets(), {})

    def test_existing_workflow_is_kept_unless_confirmed(self):
        sb = self.sandbox(existing_workflow="ORIGINAL\n")
        self.assertEqual(sb.run("n\n" + answers(push="s")), 0, sb.stdout)
        self.assertEqual(sb.workflow, "ORIGINAL\n")
        # secrets are still set (rerunning install.sh must be able to rotate them) but nothing is committed
        self.assertEqual(sb.recorded_secrets()["REGISTRY_USERNAME"], "ci")
        self.assertEqual(sb.git("rev-list", "--all", "--count").strip(), "0")
        self.assertNotIn("commit e push", sb.stdout)

    def test_only_yes_answers_confirm(self):
        for ans in ("sair", "nao", "no", "x"):
            sb = self.sandbox(existing_workflow="ORIGINAL\n")
            self.assertEqual(sb.run(f"{ans}\n" + answers()), 0, sb.stdout)
            self.assertEqual(sb.workflow, "ORIGINAL\n", ans)
        for ans in ("s", "S", "sim", "Y", "yes"):
            sb = self.sandbox(existing_workflow="ORIGINAL\n")
            self.assertEqual(sb.run(f"{ans}\n" + answers()), 0, sb.stdout)
            self.assertNotEqual(sb.workflow, "ORIGINAL\n", ans)

    def test_identical_workflow_is_not_committed_again(self):
        sb = self.sandbox()
        self.assertEqual(sb.run(answers()), 0, sb.stdout)
        sb.git("add", "-A")
        sb.git("commit", "-q", "-m", "init")
        self.assertEqual(sb.run("s\n" + answers(push="s")), 0, sb.stdout)
        self.assertIn("workflow sem mudanças", sb.stdout)
        self.assertEqual(sb.git("rev-list", "--count", "HEAD").strip(), "1")

    def test_failed_secret_set_explains_permissions(self):
        sb = self.sandbox(env={"FAKE_GH_FAIL_SECRET": "1"})
        self.assertNotEqual(sb.run(answers()), 0)
        self.assertIn("Não consegui cadastrar o secret REGISTRY_USERNAME", sb.stdout)
        self.assertIn("admin", sb.stdout)

    def test_leading_and_trailing_spaces_in_password_are_kept(self):
        sb = self.sandbox()
        self.assertEqual(sb.run(answers(password="  secret  ")), 0, sb.stdout)
        self.assertEqual(sb.recorded_secrets()["REGISTRY_PASSWORD"], "  secret  ")

    def test_existing_workflow_can_be_overwritten(self):
        sb = self.sandbox(existing_workflow="ORIGINAL\n")
        self.assertEqual(sb.run("s\n" + answers()), 0, sb.stdout)
        self.assertNotEqual(sb.workflow, "ORIGINAL\n")

    def test_no_dockerfile_fails_with_message(self):
        sb = self.sandbox(files=())
        self.assertNotEqual(sb.run(answers()), 0)
        self.assertIn("Nenhum Dockerfile", sb.stdout)

    def test_missing_gh_fails_with_instructions(self):
        sb = self.sandbox()
        empty = sb.tmp / "empty"  # hermetic: no system dirs, so no real gh can leak in
        empty.mkdir()
        sb.env["PATH"] = str(empty)
        self.assertNotEqual(sb.run(answers()), 0)
        for hint in ("brew install gh", "winget install", "cli.github.com"):
            self.assertIn(hint, sb.stdout)

    def test_empty_credentials_are_rejected(self):
        sb = self.sandbox()
        self.assertNotEqual(sb.run(answers(user="")), 0)
        self.assertEqual(sb.recorded_secrets(), {})

    def test_org_with_free_plan_and_private_repo_warns_and_can_cancel(self):
        sb = self.sandbox(env={"FAKE_GH_PLAN": "free", "FAKE_GH_PRIVATE": "true"})
        self.assertEqual(sb.run("n\n", "--org", "acme"), 0, sb.stdout)
        self.assertIn("plano gratuito", sb.stdout)
        self.assertEqual(sb.recorded_secrets(), {})

    # Prompt order with --org: [overwrite workflow?] [free plan + private: continue?]
    # "Continuar?" (every repo in the org) host user password teams portainer ai [commit and push?]
    def test_org_with_paid_plan_sets_org_secrets(self):
        sb = self.sandbox(env={"FAKE_GH_PLAN": "team", "FAKE_GH_PRIVATE": "true"})
        self.assertEqual(sb.run("s\n" + answers(), "--org", "acme"), 0, sb.stdout)
        self.assertIn("variable set REGISTRY_URL --body registry.acme.com --org acme", sb.log.read_text())
        self.assertIn("secret set REGISTRY_USERNAME --org acme", sb.log.read_text())
        self.assertNotIn("--repo", sb.log.read_text())

    def test_org_warns_about_all_repositories_and_can_decline(self):
        sb = self.sandbox(env={"FAKE_GH_PLAN": "team", "FAKE_GH_PRIVATE": "true"})
        self.assertEqual(sb.run("n\n" + answers(), "--org", "acme"), 0, sb.stdout)
        self.assertIn("TODOS os repositórios da organização 'acme'", sb.stdout)
        self.assertEqual(sb.recorded_secrets(), {})
        self.assertNotIn("secret set", sb.log.read_text())

    def test_commit_contains_only_the_workflow(self):
        sb = self.sandbox()
        sb.with_origin()
        (sb.repo / "unrelated.txt").write_text("x\n")
        sb.git("add", "unrelated.txt")
        self.assertEqual(sb.run(answers(push="s")), 0, sb.stdout)
        self.assertEqual(sb.git("show", "--name-only", "--format=", "HEAD").split(), [".github/workflows/docker.yml"])
        self.assertEqual(sb.git("status", "--porcelain").strip(), "A  unrelated.txt")

    def test_failed_push_says_commit_was_made(self):
        sb = self.sandbox()
        sb.git("add", "-A")
        sb.git("commit", "-q", "-m", "init")
        self.assertNotEqual(sb.run(answers(push="s")), 0)
        self.assertIn("commit foi feito", sb.stdout)
        self.assertIn("git push", sb.stdout)
        self.assertEqual(sb.git("show", "--name-only", "--format=", "HEAD").split(), [".github/workflows/docker.yml"])

    @unittest.skipUnless(shutil.which("actionlint"), "actionlint não instalado")
    def test_generated_workflows_pass_actionlint(self):
        for branch in ("main", "master", "release/1.x"):
            sb = self.sandbox(env={"FAKE_GH_BRANCH": branch})
            self.assertEqual(sb.run(answers()), 0, sb.stdout)
            lint = subprocess.run(["actionlint", ".github/workflows/docker.yml"], cwd=sb.repo, capture_output=True, text=True)
            self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)

if __name__ == "__main__":
    unittest.main()
