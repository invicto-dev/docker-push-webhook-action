import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "prepare.sh"


def parse_outputs(text):
    """Parses a GITHUB_OUTPUT file (key=value and key<<DELIM blocks)."""
    outputs, lines, i = {}, text.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        if "<<" in line:
            key, delim = line.split("<<", 1)
            block = []
            i += 1
            while lines[i] != delim:
                block.append(lines[i])
                i += 1
            outputs[key] = "\n".join(block)
        elif "=" in line:
            key, value = line.split("=", 1)
            outputs[key] = value
        i += 1
    return outputs


def run_prepare(files=("Dockerfile",), **env_overrides):
    """Runs prepare.sh in a temp dir. Returns (returncode, stdout+stderr, outputs)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for name in files:
            path = tmp / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("FROM scratch\n")
        out_file = tmp / "github_output"
        out_file.touch()
        env = {
            "PATH": os.environ["PATH"],
            "RUNNER_TEMP": str(tmp),
            "GITHUB_OUTPUT": str(out_file),
            "GITHUB_REPOSITORY": "Acme/My-Repo",
            "GITHUB_SHA": "abc123",
            "REGISTRY_URL": "registry.example.com",
            "REGISTRY_USERNAME": "user",
            "REGISTRY_PASSWORD": "pass",
            "NAME": "",
            "TAGS": "",
            "BUILD_CONTEXT": ".",
            "DOCKERFILE_PATH": "",
        }
        env.update(env_overrides)
        proc = subprocess.run(
            ["bash", str(SCRIPT)], cwd=tmp, env=env, capture_output=True, text=True
        )
        return proc.returncode, proc.stdout + proc.stderr, parse_outputs(out_file.read_text())


class DefaultsTest(unittest.TestCase):
    def test_name_and_tags_default_to_repo_and_sha(self):
        rc, out, o = run_prepare()
        self.assertEqual(rc, 0, out)
        self.assertEqual(o["registry"], "registry.example.com")
        self.assertEqual(
            o["refs"],
            "registry.example.com/my-repo:latest\nregistry.example.com/my-repo:abc123",
        )
        self.assertEqual(o["dockerfile"], "./Dockerfile")
        self.assertTrue(o["docker_config"])


class RegistryTest(unittest.TestCase):
    def test_pasted_registry_is_normalized(self):
        rc, out, o = run_prepare(REGISTRY_URL="  HTTPS://Registry.Example.com//  ")
        self.assertEqual(rc, 0, out)
        self.assertEqual(o["registry"], "registry.example.com")

    def test_http_scheme_and_port_are_handled(self):
        rc, out, o = run_prepare(REGISTRY_URL="http://localhost:5000/")
        self.assertEqual(rc, 0, out)
        self.assertEqual(o["registry"], "localhost:5000")

    def test_namespace_is_kept_as_prefix(self):
        rc, out, o = run_prepare(REGISTRY_URL="registry.example.com/qriar", NAME="app")
        self.assertEqual(rc, 0, out)
        self.assertTrue(o["refs"].startswith("registry.example.com/qriar/app:latest"))

    def test_empty_registry_names_the_secret(self):
        for value in ("", "   "):
            rc, out, o = run_prepare(REGISTRY_URL=value)
            self.assertEqual(rc, 1, out)
            self.assertIn("REGISTRY_URL", out)
            self.assertIn("está vazio ou não existe", out)
            self.assertEqual(o, {})

    def test_scheme_only_registry_is_rejected(self):
        rc, out, _ = run_prepare(REGISTRY_URL="https://")
        self.assertEqual(rc, 1, out)
        self.assertIn("REGISTRY_URL", out)

    def test_registry_with_line_breaks_cannot_inject_outputs(self):
        rc, out, o = run_prepare(REGISTRY_URL="a.com\nEOF\nrefs<<EOF")
        self.assertEqual(rc, 1, out)
        self.assertEqual(o, {})


class CredentialsTest(unittest.TestCase):
    def test_empty_username_names_the_secret(self):
        rc, out, _ = run_prepare(REGISTRY_USERNAME="")
        self.assertEqual(rc, 1, out)
        self.assertIn("REGISTRY_USERNAME", out)

    def test_empty_password_names_the_secret(self):
        rc, out, _ = run_prepare(REGISTRY_PASSWORD="")
        self.assertEqual(rc, 1, out)
        self.assertIn("REGISTRY_PASSWORD", out)
        self.assertNotIn("pass", out.replace("REGISTRY_PASSWORD", ""))


class NameTest(unittest.TestCase):
    def test_uppercase_is_lowercased_with_notice(self):
        rc, out, o = run_prepare(NAME="Qriar/App")
        self.assertEqual(rc, 0, out)
        self.assertIn("::notice::", out)
        self.assertTrue(o["refs"].startswith("registry.example.com/qriar/app:latest"))

    def test_tag_or_digest_in_name_is_rejected(self):
        for value in ("app:v1", "app@sha256:abc"):
            rc, out, _ = run_prepare(NAME=value)
            self.assertEqual(rc, 1, out)
            self.assertIn("tags", out)

    def test_whitespace_and_line_breaks_are_rejected(self):
        for value in ("my app", "app\nEOF\nrefs<<EOF"):
            rc, out, o = run_prepare(NAME=value)
            self.assertEqual(rc, 1, out)
            self.assertEqual(o, {})


class TagsTest(unittest.TestCase):
    def test_commas_and_line_breaks_separate_tags(self):
        rc, out, o = run_prepare(NAME="app", TAGS="v1, v2\nv3")
        self.assertEqual(rc, 0, out)
        self.assertEqual(
            o["refs"].split("\n"),
            [f"registry.example.com/app:{t}" for t in ("v1", "v2", "v3")],
        )

    def test_invalid_tag_is_rejected(self):
        rc, out, _ = run_prepare(TAGS="bad tag!")
        self.assertEqual(rc, 1, out)
        self.assertIn("bad tag!", out)

    def test_only_separators_means_no_tags(self):
        rc, out, _ = run_prepare(TAGS=",")
        self.assertEqual(rc, 1, out)
        self.assertIn("Nenhuma tag", out)


class DockerfileTest(unittest.TestCase):
    def test_missing_dockerfile_shows_path(self):
        rc, out, _ = run_prepare(DOCKERFILE_PATH="missing/Dockerfile")
        self.assertEqual(rc, 1, out)
        self.assertIn("Dockerfile não encontrado: missing/Dockerfile", out)

    def test_directory_resolves_to_dockerfile_inside(self):
        rc, out, o = run_prepare(files=("docker/Dockerfile",), DOCKERFILE_PATH="docker")
        self.assertEqual(rc, 0, out)
        self.assertEqual(o["dockerfile"], "docker/Dockerfile")

    def test_context_is_used_when_no_dockerfile_path(self):
        rc, out, o = run_prepare(files=("app/Dockerfile",), BUILD_CONTEXT="app")
        self.assertEqual(rc, 0, out)
        self.assertEqual(o["dockerfile"], "app/Dockerfile")

    def test_line_break_in_path_is_rejected(self):
        rc, out, o = run_prepare(DOCKERFILE_PATH="a\nb")
        self.assertEqual(rc, 1, out)
        self.assertEqual(o, {})


if __name__ == "__main__":
    unittest.main()
