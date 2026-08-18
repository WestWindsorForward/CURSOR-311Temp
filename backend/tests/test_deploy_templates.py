"""The one-click deployment templates, and the promises made about them.

`deploy/templates/` holds an ARM template and a CloudFormation template that a
town deploys into their own cloud account from a button on the setup page. Two
things about them are load-bearing and neither is visible from reading the setup
copy:

  * they must not emit a key, password or secret as a deployment output. Both
    clouds retain outputs -- Azure in the resource group's deployment history,
    AWS in the stack -- where they are readable by a wider audience than the
    person who deployed. The setup page says which blade each key is copied
    from instead, and that is a deliberate trade rather than an oversight, so it
    needs something to hold it in place.

  * the values they *do* emit are labelled with the names of the boxes on our
    own cards, so a clerk copies across without translating. Those labels come
    from the credential catalogs, which move; an output naming a label that no
    longer exists sends somebody looking for a box that is not there.

Deliberately stdlib-only. CI installs five packages and none of them is a YAML
parser, so the CloudFormation file is checked as text -- which is also how a
reviewer reads it.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "deploy/templates"
ARM = TEMPLATES / "azure/pinpoint-311.json"
CFN = TEMPLATES / "aws/pinpoint-311.yaml"
CONTENT = ROOT / "frontend/src/components/setupStepsContent.tsx"


def _skip_without(path: Path):
    if not path.exists():
        pytest.skip(f"{path.name} not present in this checkout")


def _arm():
    _skip_without(ARM)
    return json.loads(ARM.read_text())


# ---------------------------------------------------------------------------
# Nothing secret comes back out
# ---------------------------------------------------------------------------

def test_the_arm_template_emits_no_secret_as_an_output():
    """`listKeys` is how an ARM template reaches a Cognitive Services or storage
    key, and putting one in an output writes it into deployment history for
    everyone who can read that history -- which is more people than deployed it,
    and more of them every time somebody joins."""
    template = _arm()
    rendered = json.dumps(template["outputs"])
    for forbidden in ("listKeys", "listSecrets", "listAccountSas", "adminPassword"):
        assert forbidden not in rendered, (
            f"the ARM template's outputs call {forbidden}, which puts a secret into "
            "deployment history. Outputs are names and endpoints; the setup page "
            "tells the operator which blade to copy each key from."
        )


def test_the_cloudformation_template_creates_no_access_key():
    """An AWS::IAM::AccessKey would put a long-lived secret in the stack, and the
    whole point of the role this template creates is that there is not one."""
    _skip_without(CFN)
    source = CFN.read_text()
    assert "AWS::IAM::AccessKey" not in source, (
        "the CloudFormation template creates an IAM access key. It creates a role "
        "and an instance profile precisely so that no long-lived credential exists."
    )
    outputs = source.split("\nOutputs:", 1)
    assert len(outputs) == 2, "the CloudFormation template has no Outputs section"
    lowered = outputs[1].lower()
    for forbidden in ("secretaccesskey", "password"):
        assert forbidden not in lowered, f"a secret ({forbidden}) is emitted as a stack output"


# ---------------------------------------------------------------------------
# What they do emit is named the way our boxes are named
# ---------------------------------------------------------------------------

def _catalog_labels():
    """Every credential label our cards render, across the importable catalogs.

    Same tolerance as test_setup_steps_content: CI cannot import all of them, so
    each is tried on its own and the check runs against whatever loaded.
    """
    labels = set()
    sources = [
        ("app.services.ai.registry", "AI_CATALOG"),
        ("app.services.translation_providers", "TRANSLATION_CATALOG"),
        ("app.services.map_provider", "MAP_CATALOG"),
        ("app.services.identity", "IDENTITY_CATALOG"),
    ]
    catalogs = []
    for module, name in sources:
        try:
            catalogs.append(getattr(__import__(module, fromlist=[name]), name))
        except Exception:
            continue
    try:
        from app.services.delivery_providers import _CATALOGS
        catalogs.extend(_CATALOGS.values())
    except Exception:
        pass
    for catalog in catalogs:
        for entry in catalog.values():
            for field in entry.get("credential_fields", []):
                labels.add(field["label"])
    return labels


def test_every_arm_output_that_claims_a_box_names_a_real_one():
    """The outputs say "Pinpoint box: Key Vault URL". If that label stops
    existing -- renamed, or the field dropped -- the template is directing an
    operator at a box that is not on the card, and nothing else would say so."""
    template = _arm()
    labels = _catalog_labels()
    if not labels:
        pytest.skip("no provider catalog could be imported here")

    claimed = []
    for output in template["outputs"].values():
        description = output.get("metadata", {}).get("description", "")
        for match in re.findall(r"Pinpoint box(?:es)?:\s*([^.]+)", description):
            for label in re.split(r"\s+and\s+|,", match):
                label = label.strip().rstrip(".")
                # Trailing prose after the label, e.g. "- the same value in both".
                label = re.split(r"\s+[-–—]\s+", label)[0].strip()
                if label:
                    claimed.append(label)

    assert claimed, "no ARM output claims a Pinpoint box; the labelling convention moved"
    unknown = [c for c in claimed if c not in labels]
    assert not unknown, (
        f"ARM outputs name boxes that no catalog has: {unknown}. "
        f"Known labels include {sorted(labels)[:8]}..."
    )


# ---------------------------------------------------------------------------
# Least privilege, and scoped to what was created
# ---------------------------------------------------------------------------

def test_arm_role_assignments_are_scoped_to_the_vault():
    """A role assignment with no `scope` lands on the resource group, which is
    the difference between an identity that can use one key and one that can use
    everything the town ever puts beside it."""
    template = _arm()
    assignments = [r for r in template["resources"]
                   if r["type"] == "Microsoft.Authorization/roleAssignments"]
    assert assignments, "the ARM template grants nothing; the vault would be unusable"
    for assignment in assignments:
        scope = assignment.get("scope", "")
        assert "Microsoft.KeyVault/vaults/" in scope, (
            f"role assignment {assignment['name']} is scoped to {scope or 'the resource group'} "
            "rather than to the vault"
        )


def test_the_kms_key_survives_stack_deletion():
    """Deleting a stack must never be the thing that starts a deletion countdown
    on every resident record in the database."""
    _skip_without(CFN)
    source = CFN.read_text()
    key = source.split("  PiiKey:", 1)
    assert len(key) == 2, "the KMS key resource was renamed; re-point this test"
    head = key[1][:400]
    assert "DeletionPolicy: Retain" in head, "the encryption key is not retained on stack deletion"


# ---------------------------------------------------------------------------
# The buttons and the files agree
# ---------------------------------------------------------------------------

def test_the_deploy_buttons_point_at_templates_that_exist():
    """The setup page builds both button URLs from one constant. A path typo
    there produces a button that opens the provider's console with a fetch
    failure, which reads as the cloud being broken rather than as our link."""
    if not CONTENT.exists():
        pytest.skip("frontend not present in this checkout")
    source = CONTENT.read_text()

    assert "TEMPLATE_BASE_URL" in source, "the single publishing constant is gone"
    paths = re.findall(r"\$\{TEMPLATE_BASE_URL\}(/[A-Za-z0-9._/-]+)", source)
    assert paths, "no template path is built from TEMPLATE_BASE_URL"
    for path in paths:
        assert (TEMPLATES / path.lstrip("/")).exists(), (
            f"a deploy button points at deploy/templates{path}, which is not in the repository"
        )


def test_each_template_directory_explains_itself():
    """A town's IT reviewer opens the directory before the file. A template with
    no README is a file to be reverse-engineered."""
    if not TEMPLATES.exists():
        pytest.skip("deploy/templates not present in this checkout")
    for readme in (TEMPLATES / "README.md",
                   TEMPLATES / "azure/README.md",
                   TEMPLATES / "aws/README.md"):
        assert readme.exists(), f"{readme.relative_to(ROOT)} is missing"
