# Azure — `pinpoint-311.json`

An ARM template. The Azure portal reads it and draws its own Custom deployment
form, so the preview a town sees before pressing **Create** comes from Microsoft.

## What it creates

Everything is optional and off or on via a checkbox on the form.

| Toggle | Creates | Fills these Pinpoint boxes |
| --- | --- | --- |
| `deployKeyVault` (on) | Key vault with Azure-role permissions, soft delete and **purge protection**; one RSA key with `wrapKey`/`unwrapKey` only | Key Vault URL, Key name |
| `pinpointPrincipalObjectId` (blank) | Key Vault Crypto User **and** Key Vault Secrets Officer for that identity, scoped to this vault | — |
| `deployAzureOpenAI` (off) | Azure OpenAI account and one model deployment | Azure OpenAI Endpoint, Deployment name |
| `deployCognitiveServices` (off) | One **multi-service** AI Services account | Vision endpoint, Face endpoint, Translator Region |

The multi-service account is why three cards can share one key: its endpoint
serves both the image-reading path Pinpoint calls for licence plates and the
face-detection path it calls for faces, and the same key authenticates Translator
against the global translator endpoint when the Region box names this account's
region. Azure OpenAI is a different kind of account and cannot be folded in.

## What it does not create

* **The Entra app registration and client secret.** A directory object, outside
  ARM's scope entirely. Where the server runs on Azure, use a managed identity
  instead and there is no credential to create, copy or renew — put its object id
  in `pinpointPrincipalObjectId` and the template grants it the roles.
* **An AI Face resource.** Microsoft gates Face behind a Limited Access review.
  The multi-service account serves the Face endpoint the moment it exists and
  returns 403 until the review clears. That is a provider gate, not a fault here.
* **Email and SMS.** Domain verification needs DNS the town controls.

## Two things it does that cannot be undone

Both are called out in the parameter descriptions, so Azure's own form shows them:

* **Purge protection** is enabled and Azure does not allow it to be disabled. It
  is what stops anyone wiping the vault — and every resident record encrypted
  under it — inside the recovery window.
* A **deployed model** costs money for as long as it exists. Delete the
  deployment, not just the account, if you change your mind.

## Preview before applying

```
az deployment group what-if --resource-group <rg> --template-file pinpoint-311.json
```

That diff comes from Azure. Note that role assignments and the key are the parts
`what-if` reasons about least confidently; the resource list is reliable.

## If a name is taken

ARM deployments are incremental. A vault of the same name **in the same resource
group** is updated in place rather than left alone, and a key of the same name
gains a new version. Vault names are also globally unique across Azure, so a name
taken in another tenant fails the deployment outright. The defaults are unique
per resource group for this reason — use a name you have not used before, and
read the names on the form before pressing Create.

## Roles it assigns

Both are Microsoft built-in roles, scoped to the vault and nothing wider. Their
definition ids are parameters rather than hard-coded so they can be corrected
without editing the file; check them against the role list on the vault's Access
control (IAM) if a deployment is rejected for an unknown role definition.

* **Key Vault Crypto User** — wrap and unwrap with the key. Cannot read, export,
  change or delete it.
* **Key Vault Secrets Officer** — only needed if this vault is also Pinpoint's
  secret store. Officer rather than User because Pinpoint writes credentials here
  as well as reading them.
