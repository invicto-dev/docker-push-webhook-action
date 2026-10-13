import os
import re
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
  "repo view")
    case "$*" in
      *isPrivate*) echo "${FAKE_GH_PRIVATE:-true}" ;;
      *) echo "acme/app" ;;
    esac ;;
  "secret set") printf '%s=%s\\n' "$3" "$(cat)" >> "$FAKE_GH_SECRETS" ;;
  api*) echo "${FAKE_GH_PLAN:-free}" ;;
  *) exit 1 ;;
esac
"""

PASSWORD = "p@ss \"w$rd'!"


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
        self.log = self.tmp / "gh.log"
        self.secrets = self.tmp / "secrets"
        self.log.touch()
        self.secrets.touch()
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
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(self.tmp),
            "FAKE_GH_LOG": str(self.log),
            "FAKE_GH_SECRETS": str(self.secrets),
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }
        self.env.update(env or {})

    def run(self, answers, *args):
        proc = subprocess.run(
            [BASH, str(INSTALL), *args],
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

    def recorded_secrets(self):
        return dict(l.split("=", 1) for l in self.secrets.read_text().splitlines())

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def answers(host="registry.acme.com", user="ci", password=PASSWORD, hook="", push="n"):
    return f"{host}\n{user}\n{password}\n{hook}\n{push}\n"


class InstallTest(unittest.TestCase):
    def sandbox(self, **kw):
        sb = Sandbox(**kw)
        self.addCleanup(sb.cleanup)
        return sb

    def test_single_root_dockerfile(self):
        sb = self.sandbox()
        self.assertEqual(sb.run(answers()), 0, sb.stdout)
        check_workflow(self, sb.workflow, "workflow gerado")
        self.assertNotIn("matrix", sb.workflow)
        self.assertEqual(
            sb.recorded_secrets(),
            {
                "REGISTRY_URL": "registry.acme.com",
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
        self.assertEqual(sb.recorded_secrets()["DEPLOY_WEBHOOK_URL"], "https://hook.acme.com/x")

    def test_several_dockerfiles_generate_a_matrix_with_unique_names(self):
        sb = self.sandbox(files=("config/docker/Dockerfile", "front/Dockerfile"))
        self.assertEqual(sb.run(answers()), 0, sb.stdout)
        check_workflow(self, sb.workflow, "workflow com matrix")
        self.assertIn("matrix:", sb.workflow)
        self.assertIn("name: config-docker", sb.workflow)
        self.assertIn("name: front", sb.workflow)
        self.assertIn("dockerfile: front/Dockerfile", sb.workflow)
        self.assertIn("name: ${{ matrix.name }}", sb.workflow)

    def test_single_dockerfile_in_subfolder(self):
        sb = self.sandbox(files=("api/Dockerfile",))
        self.assertEqual(sb.run(answers()), 0, sb.stdout)
        check_workflow(self, sb.workflow, "workflow subpasta")
        self.assertIn("context: api", sb.workflow)
        self.assertIn("dockerfile_path: api/Dockerfile", sb.workflow)
        self.assertNotIn("matrix:", sb.workflow)

    def test_existing_workflow_is_kept_unless_confirmed(self):
        sb = self.sandbox(existing_workflow="ORIGINAL\n")
        self.assertEqual(sb.run("n\n"), 0, sb.stdout)
        self.assertEqual(sb.workflow, "ORIGINAL\n")
        self.assertEqual(sb.recorded_secrets(), {})

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
        self.assertIn("brew install gh", sb.stdout)

    def test_empty_credentials_are_rejected(self):
        sb = self.sandbox()
        self.assertNotEqual(sb.run(answers(user="")), 0)
        self.assertEqual(sb.recorded_secrets(), {})

    def test_org_with_free_plan_and_private_repo_warns_and_can_cancel(self):
        sb = self.sandbox(env={"FAKE_GH_PLAN": "free", "FAKE_GH_PRIVATE": "true"})
        self.assertEqual(sb.run("n\n", "--org", "acme"), 0, sb.stdout)
        self.assertIn("plano gratuito", sb.stdout)
        self.assertEqual(sb.recorded_secrets(), {})

    def test_org_with_paid_plan_sets_org_secrets(self):
        sb = self.sandbox(env={"FAKE_GH_PLAN": "team", "FAKE_GH_PRIVATE": "true"})
        self.assertEqual(sb.run(answers(), "--org", "acme"), 0, sb.stdout)
        self.assertIn("secret set REGISTRY_URL --org acme", sb.log.read_text())
        self.assertNotIn("--repo", sb.log.read_text())

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

    def matrix_names(self, files, repo_name="repo"):
        sb = self.sandbox(files=files, repo_name=repo_name)
        self.assertEqual(sb.run(answers()), 0, sb.stdout)
        check_workflow(self, sb.workflow, "workflow com matrix")
        names = re.findall(r"^\s+- name: (.*)$", sb.workflow, re.M)
        self.assertEqual(len(names), len(files), sb.workflow)
        self.assertEqual(len(set(names)), len(names), names)
        for n in names:
            self.assertRegex(n, r"^[a-z0-9]+(-[a-z0-9]+)*$")
            self.assertNotRegex(n, r"^([0-9].*|true|false|yes|no|on|off|null|y|n)$")
        if shutil.which("actionlint"):
            lint = subprocess.run(["actionlint", ".github/workflows/docker.yml"], cwd=sb.repo, capture_output=True, text=True)
            self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
        return sb, names

    def test_colliding_slugs_get_unique_names(self):
        _, names = self.matrix_names(("a/b/Dockerfile", "a-b/Dockerfile"))
        self.assertEqual(sorted(names), ["a-b", "a-b-2"])

    def test_root_dockerfile_in_repo_named_like_a_folder(self):
        sb, names = self.matrix_names(("Dockerfile", "front/Dockerfile"), repo_name="front")
        self.assertEqual(sorted(names), ["front", "front-2"])

    def test_leading_dot_and_uppercase_folders(self):
        _, names = self.matrix_names((".hidden/Dockerfile", "A-B/Dockerfile", "a/b/Dockerfile"))
        self.assertEqual(sorted(names), ["a-b", "a-b-2", "hidden"])

    def test_names_that_yaml_would_not_read_as_text(self):
        sb, names = self.matrix_names(("true/Dockerfile", "1.0/Dockerfile", "null/Dockerfile"))
        self.assertEqual(sorted(names), ["img-1-0", "img-null", "img-true"])
        self.assertIn("context: ./true", sb.workflow)
        self.assertIn("context: ./1.0", sb.workflow)

    @unittest.skipUnless(shutil.which("actionlint"), "actionlint não instalado")
    def test_generated_workflows_pass_actionlint(self):
        for files in (("Dockerfile",), ("api/Dockerfile",), ("a/Dockerfile", "b/Dockerfile")):
            sb = self.sandbox(files=files)
            self.assertEqual(sb.run(answers()), 0, sb.stdout)
            lint = subprocess.run(
                ["actionlint", ".github/workflows/docker.yml"],
                cwd=sb.repo,
                capture_output=True,
                text=True,
            )
            self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)


if __name__ == "__main__":
    unittest.main()
