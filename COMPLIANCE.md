# Government Compliance & Security Posture

Pinpoint 311 is designed for self-hosted deployment within a municipal jurisdiction, prioritizing data ownership, administrative accountability, and support for public-records and privacy obligations.

> **Read this first.** This document describes features that are *designed to support* the requirements a jurisdiction may be subject to. It is not a certification, an audit result, or a guarantee of compliance with any standard. Whether a given deployment meets a given requirement (CJIS, NIST 800-53, state public-records law, WCAG, and so on) depends on how it is configured and operated, and remains the responsibility of the deploying jurisdiction. Where cloud providers are referenced, any certifications (SOC 2, ISO 27001, FedRAMP, etc.) belong to that provider and apply to their service, not to Pinpoint.

---

## 1. Government-Ready Core Features

### Data Ownership
- **Self-hosted deployment**: containerized with Docker for deployment on the town's own infrastructure or private cloud.
- **PII encryption, two modes**:
  - **Cloud KMS mode**: PII is protected with envelope encryption — a local data key encrypts the field, and that data key is wrapped by your cloud's key service (Google Cloud KMS, Azure Key Vault, or AWS KMS). Ciphertext is stored locally; only the data-key wrap/unwrap involves the cloud.
  - **Local mode**: PII is encrypted locally with Fernet (`SECRET_KEY`-derived), with no cloud dependency.
  - **Fail-loud option**: setting `REQUIRE_KMS` makes the platform refuse to store PII if the key service is unavailable, rather than silently falling back.
- **No third-party analytics**: no external tracking or analytics services; the application does not phone home. There is exactly one voluntary exception, documented below.

### Optional Deployment Registration (the one outbound exception)

The application makes no automatic calls to Pinpoint 311 servers. Because it is self-hosted, that also means there is no way to reach a town when a security fix ships. The admin console therefore includes an **optional, user-initiated registration form**, disclosed here rather than left to be discovered:

- **Nothing is transmitted unless a person fills in the form and presses Submit.** There is no background call, no scheduled call, and no call on install, upgrade or startup. Dismissing the prompt sends nothing.
- **Submission is browser-side.** The request is made by the admin's browser directly to `pinpoint311.org`, not by the application server. The server holds no connection to Pinpoint 311 infrastructure, before or after. A deployment that blocks outbound traffic simply sees the submission fail; the form then offers a link to the same form on the website, and nothing else changes.
- **The payload contains only typed values plus two consent flags.** Nothing is inferred, measured, or attached: no version string, no deployment fingerprint, no request counts, no configuration, no identifiers of any kind. The fields are: organization or municipality, contact name, contact email, contact role or title, deployment URL, state or country, how the deployment is being used (self-hosted for one municipality / hosting for multiple / evaluating), consent to receive security advisories and release notes, and consent to be listed publicly as a deployment. The first three are required; the rest are optional. A hidden anti-spam field is submitted empty.
- **Dismissal is honoured.** Closing the prompt by any route stops it reappearing in that browser. It remains reachable from a dismissible banner and from an entry on the setup page, and never blocks any part of the console.
- **The receiving endpoint is unauthenticated by design.** Distributing an API key with an open-source install would publish it; requiring one would break the zero-config install. The endpoint accepts requests from any origin, since every deployment is on a different domain, and is rate-limited by IP.

The implementation is `frontend/src/components/StayInformed.tsx`, which carries the same account of what it does and does not send.

A deployment may instead set `CONTACT_FORM_URL` to a registration form the operator hosts (a Microsoft Form, in practice). When it is unset — the default — the built-in form above is used unchanged. When it is set:

- **The console shows the operator's form rather than its own**, either framed inside the page (the default) or opened in a new tab if `CONTACT_FORM_EMBED=false`. It is labelled as hosted by Microsoft Forms in both cases. The application server transmits nothing either way; the traffic is between the administrator's browser and Microsoft.
- **The form is fetched only after somebody asks for it.** A framed third-party form is a request to Microsoft the moment it is rendered, so rendering it requires a click on a button that says so. The automatic first-run prompt shows an invitation and no frame, and dismissing it contacts nobody. Opening the form is the disclosed request; submitting it sends the answers to Microsoft Forms, where the operator reads them.
- **Pre-filled answers travel in the form's URL, and the operator chooses which.** `CONTACT_FORM_URL` may contain `{organization}`, `{deployment_url}`, `{version}`, `{contact_name}`, `{contact_email}` and `{contact_role}` placeholders, which the console substitutes. A placeholder that is not in the URL is a value that is never sent. `{contact_name}`, `{contact_email}` and `{contact_role}` are the signed-in administrator's own details — personal data — and are therefore opt-in by the same mechanism: they are transmitted only if the operator deliberately put them in the URL. Whatever is pre-filled is visible and editable in the form before it is submitted.
- **The frame is given no more than it needs.** It runs under a `sandbox` allowing only scripts, its own storage, form submission and pop-ups, and is sent with `referrer-policy: no-referrer` so the address of the town's console — often an internal hostname — is not disclosed to Microsoft. A link to open the form in a new tab is always offered alongside the frame, because a browser that blocks third-party storage will not render it.

### Audit Logging
- **Comprehensive trail**: every lifecycle event is recorded in the `request_audit_logs` table (submission, assignment, status changes, comments, edits).
- **Tamper-evident, not immutable**: entries are hash-chained, and the chain head is anchored on a schedule. The data lives in a normal database table — the chaining lets tampering be *detected*, it does not make the records physically immutable.

### Role-Based Access Control (RBAC)

| Role | Access level |
|------|--------------|
| **Resident** | Public submission; track own requests by ID; no PII visibility |
| **Staff** | Department-scoped or global request management; internal comments |
| **Researcher** | Read-only access to sanitized data exports and analytics |
| **Admin** | System-wide control: users, departments, branding, providers, keys |

### Open311
- **GeoReport v2**: standards-based JSON API for interoperability.
- **Service discovery**: JSON endpoint at `/api/open311/v2/services.json`.

---

## 2. Security Posture

### Security Layers (provider-pluggable)

Each layer works with the provider the town chooses. Where nothing external is configured, the platform falls back to encrypted database storage.

| Layer | Purpose | Options |
|-------|---------|---------|
| **Identity** | Staff SSO with MFA and passkeys | Auth0 by default; Microsoft Entra ID, Okta, or generic OIDC |
| **Secret storage** | API keys and connection credentials | Your cloud's secret store, with an encrypted database fallback |
| **PII encryption** | Resident PII at rest | Envelope encryption with your cloud's key service, or a local key |
| **Content moderation** | Screening public inputs | Built-in text scan, plus your cloud's moderation service (optional) |
| **Auto-update** | Optional container image updates | Self-hosted, off by default |

### Staff Authentication

Staff sign in through the configured identity provider (Auth0 by default). Passwords are never stored by Pinpoint — authentication is delegated to the provider.

| Feature | Implementation |
|---------|----------------|
| Authentication | OIDC with JWT bearer tokens |
| Multi-factor | Provider-supported TOTP, passkeys, biometrics |
| Session | JWT with an 8-hour expiration |
| Passwordless | Provider-supported WebAuthn / passkeys |
| Password storage | None — delegated to the identity provider |

### Secrets Management (system of record)

When an external secret store is configured, integration and provider credentials are written **there**, and the application database keeps only a reference — the raw secret does not live in the app database. When no external store is configured, credentials are held in an encrypted database table (Fernet) as a fallback.

| Property | Value |
|----------|-------|
| Store | Your cloud's secret store (Secret Manager / Key Vault / AWS Secrets Manager), or encrypted DB fallback |
| In the app database | A reference to the secret, not the secret itself (when an external store is used) |
| Bootstrap keys | A minimal set of keys needed to *reach* the store remain in the encrypted local table |

### PII Encryption (envelope)

| Property | Value |
|----------|-------|
| Scheme | AES-256-GCM data key per value, wrapped by the configured cloud key service |
| Portability | A value stays decryptable under whichever key service wrapped it (tagged wrap), so switching clouds does not orphan existing data |
| Protected fields | Email, phone, name, address |
| Local fallback | Fernet (AES-128-CBC + HMAC-SHA256) derived from `SECRET_KEY` |
| Provider guarantees | HSM backing, rotation, and certifications are the cloud provider's, not Pinpoint's — verify them against your requirements |

### Content Moderation (public inputs)

Resident-submitted text is screened as it is received:
- **Always-on text scan** using an open-source profanity library plus a small explicit/abusive term gate. Explicit or abusive descriptions and public comments are **blocked at submission** (HTTP 400); ordinary profanity is allowed through and flagged for staff, so a legitimate but angry report is not rejected.
- **Optional cloud layer**: when a moderation provider is configured, text is additionally screened by the cloud's moderation service (which can catch contextual toxicity a wordlist cannot), and images are screened for explicit content.
- **Graceful**: if no cloud provider is configured, text still uses the built-in scan and images fall back to the AI vision assessment. Moderation never blocks intake on its own error (fails open on failure).

### Resilience and Optional Providers

Every advanced provider is optional. If one is unconfigured or unreachable, that feature is skipped and a warning is surfaced in the Admin Console (`/health/`), while the rest of the platform continues to run. Core data safety is the exception: the database is treated as critical (its failure is loud), and PII encryption fails loudly when `REQUIRE_KMS` is set rather than storing plaintext.

### Safe Version Deployment (Admin Console)

The Version Switcher performs a backup-first, health-checked deployment with automatic rollback:

| Step | Action |
|------|--------|
| 1 | Timestamped database backup (`pg_dump`) |
| 2 | Checkout the target version (local changes stashed) |
| 3 | Forward-only Alembic migrations (non-destructive) |
| 4 | Rebuild backend/frontend images |
| 5 | Health-check verification with timeout |
| 6 | Audit-log the outcome |

If any step fails, the system reverts to the original commit, restarts with the original code, and logs the rollback. In managed (state-hosted) mode this self-update is locked — the control plane owns rollouts.

### Rate Limiting

| Limit | Value |
|-------|-------|
| Default | 500 requests/minute per IP |
| Sensitive endpoints | Tighter per-endpoint limits (e.g. 10/min submit, 5/min public comment) |
| Implementation | slowapi middleware |
| Response on exceeded | HTTP 429 with `Retry-After` |

### Security Headers

| Header | Value | Purpose |
|--------|-------|---------|
| Strict-Transport-Security | max-age=31536000; includeSubDomains | Enforce HTTPS |
| X-Frame-Options | DENY | Prevent clickjacking |
| X-Content-Type-Options | nosniff | Prevent MIME sniffing |
| Referrer-Policy | strict-origin-when-cross-origin | Control referrer leakage |
| Content-Security-Policy | frame-ancestors 'none' | Prevent framing |
| Permissions-Policy | geolocation=(self), camera=(), microphone=(), payment=(), usb=() | Restrict browser features |
| Cache-Control | no-store (API routes) | Prevent caching sensitive data |

### Input Validation
- Pydantic schema validation on all API inputs.
- Parameterized queries via the SQLAlchemy ORM (no string-built SQL).
- Output escaping through React's built-in rendering.

### AI Provider Security

AI analysis runs on whichever provider the town configures. Data-residency, retention, training, and certification guarantees depend on that provider — verify them against your jurisdiction's requirements. What Pinpoint controls:

| Aspect | What Pinpoint does |
|--------|--------------------|
| Data sent | Only request text and up to three photos; PII is redacted from the analysis output |
| Transport | Requests go directly to your configured provider over TLS |
| Human-in-the-loop | AI priority/category suggestions require explicit staff acceptance |
| Optional | AI can be disabled entirely; the platform runs without it |

#### Human-in-the-Loop Priority Scoring

| Stage | Behavior |
|-------|----------|
| **AI analysis** | The model produces a priority score (1–10), stored only in the `ai_analysis` JSON field |
| **Not applied** | The score is **never written to the request's priority** |
| **Acceptance** | The priority changes only when staff click "Accept AI Priority" |
| **Audit** | Acceptance is recorded in the audit log |
| **Override** | Staff can set priority manually at any time |

Nothing is routed, assigned, closed, or prioritized automatically on the basis of an AI assessment. Routing and assignment are handled by the rules an administrator configures, not by AI.

---

## 3. Testing & Security Automation

Verification happens on three fronts: an automated test suite that runs on every change, a set of CI/CD security scanners, and external vulnerability scanning of the public deployments.

### Automated Test Suite

The backend ships with a pytest suite that exercises the security- and correctness-sensitive paths directly, so the behaviors this document describes are checked in code, not just asserted in prose. Coverage includes:

| Area | What the tests check |
|------|----------------------|
| Credential handling | Secrets are written as references, resolved back at build time, and unresolvable references are omitted rather than leaked |
| PII encryption / KMS | Envelope wrap/unwrap round-trips, cross-provider portability, and fail-loud behavior under `REQUIRE_KMS` |
| Provider dispatch | AI, translation, secret, KMS, email, SMS, and moderation providers route to the right backend and degrade cleanly when unconfigured |
| Content moderation | Explicit/abusive terms are blocked at submission, ordinary profanity is flagged but allowed through, and moderation fails open on provider error |
| Live model discovery | Provider model lists merge with the curated catalog, availability checks, and cache staleness handling |
| Health classification | The database is treated as critical (loud failure) while optional providers surface as warnings |
| Data retention | Retention-policy application and override/clear semantics |
| Connectors | The generic connector round-trips through the standard save/build/verify path |

The suite is designed to run without external cloud credentials — provider-dependent tests skip when a library or backend is absent rather than failing, so the core suite stays green in any environment.

### CI/CD Security Scanning (GitLab Ultimate)

Security scanning runs through the GitLab Ultimate application-security suite. Each scanner runs on merge requests and on the default branch, and findings flow into the project's Security Dashboard and Vulnerability Report where they are triaged and tracked to resolution.

| Scanner | Type | Purpose |
|---------|------|---------|
| Advanced SAST | Static | Cross-file, taint-aware static analysis of application code |
| IaC Scanning | Static | Misconfiguration checks on Docker/Compose and infrastructure definitions |
| Secret Detection | Static | Detects credentials committed to the repository history |
| Dependency Scanning | Composition | Known-vulnerability checks on dependencies, with a CycloneDX SBOM |
| Container Scanning | Composition | Vulnerability scan of the built container images |
| DAST | Dynamic | Runtime scan of the running application |
| API Fuzzing | Dynamic | Fault-injection against the API surface |
| Coverage-guided Fuzzing | Dynamic | Coverage-driven fuzzing of targeted code paths |

Merge requests surface newly introduced vulnerabilities inline, so regressions are caught before they merge. Application health and uptime are monitored separately, and optional error tracking is available via Sentry (`SENTRY_DSN`), with PII sending disabled by default.

### External Vulnerability Scanning (CISA)

The public reference deployments are enrolled in CISA's free **Cyber Hygiene** vulnerability scanning service (available at no cost to state, local, tribal, and territorial governments and to public-sector-adjacent projects). CISA scans the internet-facing hosts on a recurring basis and reports findings back for remediation.

To be clear about what this is and is not:
- It **is** ongoing, independent external scanning of the live public endpoints by a neutral third party, with findings tracked to resolution.
- It is **not** a certification, endorsement, or approval — CISA does not certify software, and enrollment says nothing about any individual jurisdiction's own deployment. Each town's instance is scanned only if that town enrolls it.

A jurisdiction running its own instance can enroll its deployment in the same free program directly with CISA.

---

## 4. Accessibility

Built **toward WCAG 2.1 Level AA**. This describes the design target, not an independent audit or certification.

| Guideline | Status |
|-----------|--------|
| 4.1.2 Name, Role, Value | aria-labels on interactive elements |
| 1.4.3 Contrast | 4.5:1 contrast target |
| 2.1.1 Keyboard | Full keyboard navigation |
| 2.4.4 Link Purpose | Descriptive link text |

Not yet done: independent screen-reader testing and a third-party audit.

---

## 5. Centralized Hosting (isolation notes)

Self-hosting is the default; centralized hosting is a separate, optional deployment model (see the README and the `centralizedhosting` repository). When used:
- Each town is a fully isolated instance — its own database, storage, key, and secrets. There are no shared tables and no cross-town data.
- The control plane handles infrastructure, platform secrets, version rollout, and aggregate metadata only. It never accesses resident data.
- Managed mode is opt-in (`MANAGED_MODE`); with it off, behavior is identical to a standalone deployment.

---

## 6. Setup & Configuration

All integrations are configured through **Admin Console → Setup & Integration** with step-by-step guidance filtered to the cloud and features you choose. No CLI tools are required.

| Integration | Configuration |
|-------------|---------------|
| Cloud providers (AI, translation, secrets, KMS, email, SMS, moderation) | Credentials entered in the Admin Console; one "cloud environment" choice can set them together |
| Identity (SSO) | Provider credentials entered in the Admin Console |
| Maps | A Google Maps API key (the one fixed external dependency) |
| Town-system connectors | Endpoint + key entered per connector; verified with a built-in connection check |

### Prerequisites
- An identity provider account for staff SSO (a free tier is available with the default).
- A Google Maps API key.
- A cloud account only if you enable AI, translation, encryption, or cloud moderation — all optional.

---

## 7. Contact & Resources

- **Repository**: [GitHub](https://github.com/Pinpoint-311/Pinpoint-311)
- **API documentation**: `/api/docs` (Swagger UI)
- **Security issues**: report privately via GitHub Security Advisories

---

*Document version: 2.0 | Last updated: July 2026*
