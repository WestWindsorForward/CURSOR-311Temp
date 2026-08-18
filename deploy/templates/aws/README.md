# AWS — `pinpoint-311.yaml`

A CloudFormation template. Create it as a **change set** rather than a stack if
you want an authoritative diff first — that diff comes from Amazon.

## What it creates

| Resource | Purpose | Fills these Pinpoint boxes |
| --- | --- | --- |
| KMS key + alias, rotation on | Wraps the key that encrypts resident personal information | AWS Region, Key ID or ARN |
| IAM role + instance profile | The identity Pinpoint signs in as | **none — that is the point** |
| IAM policy on that role | Secrets Manager, Translate, Rekognition, Bedrock — each behind its own toggle | — |

## The best outcome available: no credential at all

The template creates a **role**, not a user, and no access keys. Attach the
instance profile to the EC2 instance running Pinpoint, or name the role as the
task role on ECS, and the application signs in with a token AWS issues minutes at
a time and rotates. Nothing to paste, nothing to leak, nothing to expire.
Pinpoint detects this and greys out the credential boxes.

If Pinpoint does not run on AWS compute, this stack is still the right one:
create an IAM user by hand afterwards and attach the same policy to it.

## What it does not cover

* **Bedrock model access.** Each model is enabled per account in the Bedrock
  console. The policy grants the call; the enablement is a provider gate.
* **Email and SMS.** SES domain verification needs DNS records only the town can
  publish, and 10DLC phone registration is carrier paperwork measured in weeks.
* **Nothing is needed for Translate or Rekognition** beyond the permissions here.
  Both are API-only; there is no resource to create.

## The one irreversible-feeling choice

`PreventAccidentalKeyDeletion` (default **Yes**) adds a policy statement denying
everyone the ability to schedule the key for deletion or disable it. A KMS key
stops working the moment deletion is *scheduled*, not when the waiting period
ends, so an accident breaks resident data immediately rather than in thirty days.
An explicit deny beats every allow, including the account root. To retire the key
deliberately later, an administrator removes that statement first.

The key also carries `DeletionPolicy: Retain`. Deleting the stack leaves the key
in place, because deleting a stack should never be the thing that starts a
countdown on every resident record in the database.

## If a name is taken

The stack fails and nothing existing is modified. The alias, role and instance
profile are all named resources and CloudFormation refuses to create one that
already exists — which is also what stops a second run quietly creating a second
KMS key you keep paying for.

## Removing it

Delete the stack: the role, policy, instance profile and alias go with it. The
KMS key is retained, by design. Schedule its deletion deliberately if you mean to
retire it, after removing the deny statement.
