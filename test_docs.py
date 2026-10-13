import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ACTION_REF = "docker-push-webhook-action@v2"
REGISTRY_URL_EXPR = "${{ vars.REGISTRY_URL || secrets.REGISTRY_URL }}"
SECRETS = {
    "REGISTRY_URL",
    "REGISTRY_USERNAME",
    "REGISTRY_PASSWORD",
    "DEPLOY_WEBHOOK_URL",
    "DEPLOY_TOKEN",
    "GITHUB_TOKEN",
}
# Messages that prepare.sh prints and the docs explain; they must match.
SHARED_MESSAGES = ["está vazio ou não existe", "Dockerfile não encontrado"]
# Errors from outside the action that the docs also explain.
DOC_ERRORS = ["unauthorized", "COPY failed"]


def action_inputs(action_yml):
    """Returns {input name: required?} from the top-level `inputs:` block."""
    block = action_yml.split("\ninputs:\n", 1)[1].split("\noutputs:\n", 1)[0] + "\n"
    inputs = {}
    for m in re.finditer(r"^  ([a-z_]+):\n((?:    .*\n)+)", block, re.M):
        inputs[m.group(1)] = "required: true" in m.group(2)
    return inputs


def step_inputs(workflow_text):
    """Input names passed to the action. The action step must be the last step."""
    tail = workflow_text.split("docker-push-webhook-action@", 1)[1]
    after_with = tail.split("with:\n", 1)[1]
    return set(re.findall(r"^\s+([a-z_]+):", after_with, re.M))


def check_workflow(test, text, where):
    inputs = action_inputs((ROOT / "action.yml").read_text())
    used = step_inputs(text)
    unknown = used - set(inputs)
    test.assertFalse(unknown, f"{where}: inputs que não existem no action.yml: {unknown}")
    required = {k for k, v in inputs.items() if v}
    test.assertFalse(required - used, f"{where}: faltam inputs obrigatórios: {required - used}")
    secrets = set(re.findall(r"secrets\.([A-Z_]+)", text))
    test.assertFalse(secrets - SECRETS, f"{where}: secrets desconhecidos: {secrets - SECRETS}")
    test.assertIn(ACTION_REF, text, where)
    variables = set(re.findall(r"vars\.([A-Z_]+)", text))
    test.assertFalse(variables - {"REGISTRY_URL"}, f"{where}: só REGISTRY_URL pode vir de vars.*: {variables}")
    # a literal host (docker.io) is fine; a secret/variable reference must read both places
    value = re.search(r"^\s*registry_url:\s*(.+?)\s*$", text, re.M).group(1)
    test.assertTrue(
        value == REGISTRY_URL_EXPR or not value.startswith("${{"),
        f"{where}: registry_url deve aceitar Variable ou Secret, achei: {value}",
    )


def yaml_blocks_using_action(markdown):
    blocks = re.findall(r"```yaml\n(.*?)```", markdown, re.S)
    return [b for b in blocks if "docker-push-webhook-action@" in b]


class DocsConsistencyTest(unittest.TestCase):
    def test_template(self):
        check_workflow(self, (ROOT / "templates/docker.yml").read_text(), "templates/docker.yml")

    def test_docker_login_uses_only_the_registry_host(self):
        # a REGISTRY_URL with a namespace (docker.io/user) must not reach `docker login`
        self.assertNotIn("outputs.registry }}", (ROOT / "action.yml").read_text())

    def test_markdown_examples(self):
        for name in ("llms.txt", "README.md"):
            blocks = yaml_blocks_using_action((ROOT / name).read_text())
            self.assertTrue(blocks, f"{name}: nenhum exemplo yaml da action")
            for i, block in enumerate(blocks):
                check_workflow(self, block, f"{name} bloco {i + 1}")

    def test_error_tables_match_the_script(self):
        script = (ROOT / "prepare.sh").read_text()
        for message in SHARED_MESSAGES:
            self.assertIn(message, script, f"prepare.sh não imprime mais: {message}")
        for name in ("INSTALAR.md", "llms.txt"):
            text = (ROOT / name).read_text()
            for message in SHARED_MESSAGES + DOC_ERRORS:
                self.assertIn(message, text, f"{name} não explica o erro: {message}")

    def test_docs_state_secret_scope_rule(self):
        for name in ("INSTALAR.md", "llms.txt"):
            text = (ROOT / name).read_text()
            self.assertIn("por repositório", text, name)
            self.assertIn("organização", text, name)


if __name__ == "__main__":
    unittest.main()
