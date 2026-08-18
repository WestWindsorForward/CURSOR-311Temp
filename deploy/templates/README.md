# Deployment templates

One template per cloud that has a real one-click deployment mechanism, so a town
can create the resources Pinpoint 311 needs without clicking through a console
for an afternoon.

| Cloud  | Template | Rendered by |
| ------ | -------- | ----------- |
| Azure  | [`azure/pinpoint-311.json`](azure/pinpoint-311.json) — ARM | the Azure portal's Custom deployment form |
| AWS    | [`aws/pinpoint-311.yaml`](aws/pinpoint-311.yaml) — CloudFormation | the CloudFormation console, with change sets |
| Google | *none* | — |

**There is no Google template, deliberately.** Google has no equivalent of a
deploy button: Deployment Manager is on its way out, and Terraform — the honest
answer for Google — needs a local toolchain and a state file, which is not a
click. Offering a worse mechanism for the sake of symmetry would leave a town
worse off than the corrected console walk on the setup page. Google's manual path
is complete and maintained on the same footing as the other two.

## The property that matters

Each template is rendered by the cloud provider, in the town's own account,
signed in as them. The preview a town sees before pressing Create comes from
Microsoft or Amazon, not from us — and both clouds have an authoritative
change-preview mechanism that beats any description we could write:

* Azure: `az deployment group what-if --resource-group <rg> --template-file pinpoint-311.json`
* AWS: create a change set instead of a stack, and read the diff before executing it

Neither template references anything of ours at deploy time. There is no URL
fetched, no script run, no callback, no telemetry. Everything each one does is
visible in the file.

## No keys in outputs

Neither template emits an API key, a password or a secret as a deployment output,
and neither creates a static credential where the cloud offers an attached
identity instead.

This is not an oversight. Azure keeps deployment outputs in the resource group's
deployment history, where anyone who can read that history can read them — a
wider audience than the person who ran the deployment, and one that grows as
people join. CloudFormation stack outputs have the same property. So the
templates output names and endpoints, and the setup page says which blade each
key is copied from. One extra click, materially safer.

## Least privilege

Every permission either template grants is scoped to the resource it created:

* Azure role assignments are scoped to the key vault, never to the resource group
  or the subscription.
* The AWS key policy names one role and four actions on one key.
* The AWS IAM policy covers only the services Pinpoint calls. The two entries
  that are not resource-scoped — Translate and Rekognition — are API-only
  services with nothing to scope to.

## What they will not do

Identity objects are largely outside resource templates, and some things are
gated by the provider rather than by permissions:

| | Azure | AWS |
| --- | --- | --- |
| Credential Pinpoint signs in with | **Not created.** An Entra app registration is a directory object, outside ARM. Prefer a managed identity — then there is no credential at all. | **Not needed.** The template creates a role and instance profile; attach it and nothing is pasted into Pinpoint. |
| Provider gate in the way | Face API is behind Microsoft's Limited Access review. The endpoint exists as soon as the account does and answers 403 until the review clears. | Each Bedrock model needs per-account enablement in the Bedrock console. |
| Email and SMS | Not covered. Domain verification needs DNS the town controls. | Not covered. SES verification plus 10DLC carrier registration, measured in weeks. |

## If a name is already taken

* **AWS** fails the stack. The alias, role and instance profile are named
  resources and CloudFormation refuses to create one that exists. Nothing
  existing is modified.
* **Azure** is not so tidy, and it is better to say so than to imply a guarantee
  ARM does not give. A deployment is incremental: a key vault of the same name in
  the same resource group would be *updated in place* rather than left alone, and
  a key of the same name would gain a new version. The defaults are unique per
  resource group for that reason, and the portal shows every name on its form
  before you press Create. Use a name you have not used before.

## Removing what they created

* **AWS**: delete the stack. The role, policy, instance profile and alias go with
  it. The KMS key is retained on purpose — deleting a stack should never start a
  deletion countdown on every resident record in the database. Retire it
  deliberately afterwards if you mean to.
* **Azure**: delete the resource group, or the individual resources. The vault
  will sit in soft-delete for its retention period and, with purge protection on,
  cannot be wiped early. That is the setting working as intended.

## Publishing

A deploy button hands the cloud provider a public URL and the provider fetches
the template itself. Until these files are published somewhere publicly readable,
the buttons on the setup page do not resolve. The URL lives in a single constant,
`TEMPLATE_BASE_URL`, in `frontend/src/components/setupStepsContent.tsx`; moving
hosts is a one-line change there.
