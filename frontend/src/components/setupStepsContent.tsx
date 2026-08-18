import { Check, Copy } from 'lucide-react';

import { defineSteps } from './setupSteps';
import type { StepContext } from './setupSteps';

/**
 * The steps themselves, one registration per provider.
 *
 * Separate from setupSteps.tsx on purpose: that file is the mechanism and does
 * not go stale, this one is content about somebody else's console and will.
 * Vendors reorganise their menus on their own schedule, so entries here are
 * expected to be corrected over time, and a provider with no entry is a normal
 * state -- the card falls back to its plain field list.
 *
 * The rule for writing one: every step either does something or produces
 * something. `check` is what tells a clerk they are in the right place, and it
 * only earns its line when the next thing they do is paste a value. Where a
 * value must be copied exactly -- callback URLs, key restrictions -- give them
 * the copy button rather than a string to retype, because one missing character
 * fails silently and looks like a wrong password.
 *
 * The `fields` on each step are checked against the real credential catalogs by
 * backend/tests/test_setup_steps_content.py. A key invented from memory here
 * would otherwise render a box that saves to nothing, which is the failure this
 * whole page exists to avoid.
 */

/** A copy-to-clipboard chip for a value that must be exact. */
function CopyValue({ ctx, id, value }: { ctx: StepContext; id: string; value: string }) {
    return (
        <span className="inline-flex items-center gap-1 align-middle">
            <code className="bg-black/30 px-1.5 py-0.5 rounded text-[11px] text-primary-200 break-all">{value}</code>
            <button
                type="button"
                onClick={() => ctx.copy(value, id)}
                aria-label="Copy to clipboard"
                className="inline-flex text-white/40 hover:text-white/80 transition-colors"
            >
                {ctx.copied === id ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
            </button>
        </span>
    );
}

const B = ({ children }: { children: React.ReactNode }) => (
    <strong className="text-white/90">{children}</strong>
);

/** A vendor console link. Always new-tab: a clerk mid-setup who navigates away
 *  loses everything typed into the boxes below. */
const L = ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href} target="_blank" rel="noopener noreferrer"
        className="text-primary-300 underline underline-offset-2">{children}</a>
);

/**
 * Where the deployment templates in `deploy/templates/` are published.
 *
 * A deploy button does not upload anything: it hands the cloud a URL and the
 * cloud fetches the template itself, in the town's own browser and the
 * provider's own console. So this has to resolve publicly, and until it does
 * the buttons below are inert. One constant on purpose -- publishing these to a
 * different host, a mirror, or a town's own copy is a one-line change here, and
 * nothing else in the file knows where they live.
 */
const TEMPLATE_BASE_URL = 'https://raw.githubusercontent.com/Pinpoint-311/Pinpoint-311/main/deploy/templates';

/** Azure's documented portal entry point for a template at a URL. */
const AZURE_DEPLOY_URL =
    `https://portal.azure.com/#create/Microsoft.Template/uri/${encodeURIComponent(`${TEMPLATE_BASE_URL}/azure/pinpoint-311.json`)}`;

/** CloudFormation's console entry point. Lands on the review screen, where a
 *  change set can be taken instead of a stack. */
const AWS_DEPLOY_URL =
    `https://console.aws.amazon.com/cloudformation/home#/stacks/create/review?templateURL=${encodeURIComponent(`${TEMPLATE_BASE_URL}/aws/pinpoint-311.yaml`)}&stackName=pinpoint-311`;

/** A link that opens the provider's own deployment form. Drawn as a button
 *  because that is what it behaves like, and new-tab like every other console
 *  link here: a clerk mid-setup who navigates away loses what they have typed. */
const DeployButton = ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href} target="_blank" rel="noopener noreferrer"
        className="mt-2 mb-1 inline-flex items-center gap-1.5 rounded-lg border border-primary-400/40 bg-primary-500/15 px-3 py-1.5 text-xs font-medium text-primary-100 hover:bg-primary-500/25 transition-colors">
        {children}
    </a>
);

/**
 * The claim a cautious IT department is actually testing.
 *
 * Every sentence is structural rather than a promise: the file is in front of
 * them, the provider renders it, and the provider has its own diff mechanism
 * that does not involve us. The last line matters most -- it is the answer to
 * "what could this do to our tenant", and the answer is bounded.
 */
const WHY_A_TEMPLATE_IS_SAFE = (
    <>
        It runs in <em>your</em> account, and the form you see is drawn by your cloud from a file you
        can read first. Nothing in it reaches us at deploy time — no script, no callback, no
        telemetry — and it emits no key or password as an output, because deployment history is
        readable by more people than the person deploying. Every permission it grants is scoped to
        the resources it just created.
    </>
);

const C = ({ children }: { children: React.ReactNode }) => (
    <code className="bg-black/30 px-1 rounded text-[11px] text-primary-200">{children}</code>
);

// ===========================================================================
// Staff sign-in
//
// All three OIDC providers redirect to the same place. The callback URL goes in
// before the credentials come out in every one of them, because a tenant
// missing the redirect accepts the password and then fails on the way back,
// which reads to everybody as a wrong secret.
// ===========================================================================

const CALLBACK = (origin: string) => `${origin}/api/auth/callback`;

// ---------------------------------------------------------------------------
// Auth0
// ---------------------------------------------------------------------------

defineSteps('identity', 'auth0', (ctx) => [
    {
        body: (
            <>
                Create the account at <L href="https://auth0.com">auth0.com</L>, using a{' '}
                <B>shared town email address</B> rather than a personal one — whoever replaces you will
                need to get in. When it asks for a region, pick one in the US if your town or state has a
                rule about where data is held; it cannot be changed afterwards.
            </>
        ),
        check: <>the Auth0 dashboard, with your town's name in the top-left corner.</>,
    },
    {
        body: (
            <>
                In the left menu open <B>Applications → Applications</B> and press <B>Create
                Application</B>. Choose <B>Regular Web Application</B> — not Single Page, not Machine to
                Machine. If it then offers to pick a technology, close that; it only shows sample code.
            </>
        ),
        check: <>a settings page with boxes labelled Domain, Client ID and Client Secret.</>,
    },
    {
        body: (
            <>
                Still on <B>Settings</B>, scroll to <B>Application URIs</B> and paste these in, using the
                copy buttons rather than retyping:
                <span className="mt-2 grid gap-1.5">
                    <span className="flex items-center gap-2 flex-wrap">
                        <span className="text-white/45 text-xs w-40 shrink-0">Allowed Callback URLs</span>
                        <CopyValue ctx={ctx} id="a0cb" value={CALLBACK(ctx.origin)} />
                    </span>
                    <span className="flex items-center gap-2 flex-wrap">
                        <span className="text-white/45 text-xs w-40 shrink-0">Allowed Logout URLs</span>
                        <CopyValue ctx={ctx} id="a0lo" value={ctx.origin} />
                    </span>
                    <span className="flex items-center gap-2 flex-wrap">
                        <span className="text-white/45 text-xs w-40 shrink-0">Allowed Web Origins</span>
                        <CopyValue ctx={ctx} id="a0wo" value={ctx.origin} />
                    </span>
                </span>
                Then press <B>Save Changes</B> at the bottom.
            </>
        ),
        note: <>Do this before the next step. A tenant missing the callback URL accepts the password and then fails on the redirect, which looks like a wrong secret.</>,
    },
    {
        body: <>Copy the three values from that same Settings page into these boxes.</>,
        fields: ['AUTH0_DOMAIN', 'AUTH0_CLIENT_ID', 'AUTH0_CLIENT_SECRET'],
    },
]);

// ---------------------------------------------------------------------------
// Microsoft Entra ID
//
// The path most towns will actually take, because the staff already have
// Microsoft 365 accounts and Entra is the directory behind them. The one thing
// worth reading twice is the secret: Entra shows it once and then replaces it
// with dots forever, and the box beside it labelled "Secret ID" is not it.
// ---------------------------------------------------------------------------

defineSteps('identity', 'entra', (ctx) => [
    {
        body: (
            <>
                Sign in to the <L href="https://portal.azure.com">Azure portal</L> with an account that
                can administer your directory, open <B>Microsoft Entra ID</B>, and in the left pane choose{' '}
                <B>Manage → App registrations</B>, then <B>New registration</B>.
            </>
        ),
        check: <>a form headed "Register an application".</>,
    },
    {
        body: (
            <>
                Name it something a successor will recognise — <C>Pinpoint 311</C> is fine. Under{' '}
                <B>Redirect URI</B>, set the type to <B>Web</B> and paste{' '}
                <CopyValue ctx={ctx} id="enrd" value={CALLBACK(ctx.origin)} />. Leave the account-types
                option on the default (this directory only) unless your town genuinely shares staff with
                another tenant. Press <B>Register</B>.
            </>
        ),
        note: <>The redirect type matters. "Single-page application" is offered right below Web and looks equivalent; it is not, and staff sign-in will fail on the way back with an error about the flow.</>,
    },
    {
        body: (
            <>
                The overview page that appears shows <B>Application (client) ID</B> and <B>Directory
                (tenant) ID</B>. Copy both here. Leave the authority box empty unless you are on a
                sovereign cloud — it defaults to the normal commercial endpoint.
            </>
        ),
        check: <>two GUIDs on the overview page, both looking like <C>8f3c…-…-…</C>.</>,
        fields: ['ENTRA_TENANT_ID', 'ENTRA_CLIENT_ID', 'ENTRA_AUTHORITY'],
    },
    {
        body: (
            <>
                In the left pane choose <B>Manage → Certificates &amp; secrets</B>, then{' '}
                <B>Client secrets → New client secret</B>. Give it a description and an expiry, press{' '}
                <B>Add</B>, and copy the <B>Value</B> column into the box below.
            </>
        ),
        fields: ['ENTRA_CLIENT_SECRET'],
        trouble: <>Copy it now. Entra shows the Value once; leave the page and it is dots forever and you have to issue a new one. The <B>Secret ID</B> next to it is not the secret. Note the expiry date somewhere — staff sign-in stops working on that day, and it is a long way from here to that diagnosis.</>,
    },
]);

// ---------------------------------------------------------------------------
// Okta
//
// The trap here is that the issuer is not on the application page. It lives
// under a different menu entirely, and the value people reach for -- the org
// URL in the address bar -- is close enough to look right and wrong enough to
// fail.
// ---------------------------------------------------------------------------

defineSteps('identity', 'okta', (ctx) => [
    {
        body: (
            <>
                In the Okta <B>Admin Console</B> (the "Admin" button in the top-right of the normal
                dashboard), open <B>Applications → Applications</B> and press <B>Create App
                Integration</B>. Choose <B>OIDC – OpenID Connect</B> as the sign-in method and{' '}
                <B>Web Application</B> as the application type, then <B>Next</B>.
            </>
        ),
        trouble: <>Web Application, not Single-Page Application. A SPA integration issues no client secret, and the box below will have nothing to put in it.</>,
    },
    {
        body: (
            <>
                Name it, then fill the two URL fields:
                <span className="mt-2 grid gap-1.5">
                    <span className="flex items-center gap-2 flex-wrap">
                        <span className="text-white/45 text-xs w-44 shrink-0">Sign-in redirect URIs</span>
                        <CopyValue ctx={ctx} id="okrd" value={CALLBACK(ctx.origin)} />
                    </span>
                    <span className="flex items-center gap-2 flex-wrap">
                        <span className="text-white/45 text-xs w-44 shrink-0">Sign-out redirect URIs</span>
                        <CopyValue ctx={ctx} id="oklo" value={ctx.origin} />
                    </span>
                </span>
                Under <B>Assignments</B>, pick the groups whose members should be able to sign in to
                Pinpoint, then <B>Save</B>.
            </>
        ),
        check: <>the app's <B>General</B> tab, with a <B>Client Credentials</B> section on it.</>,
    },
    {
        body: (
            <>
                Copy <B>Client ID</B> from that section, and press <B>Show</B> beside <B>Client
                secret</B> to reveal and copy the other.
            </>
        ),
        fields: ['OKTA_CLIENT_ID', 'OKTA_CLIENT_SECRET'],
    },
    {
        body: (
            <>
                The issuer is not on the application page. Open <B>Security → API</B> in the left menu.
                You will see more than one authorization server, and <B>either can be correct</B> — what
                matters is that the one you enter here is the one your app is set to use:
                <span className="mt-2 grid gap-1.5">
                    <span className="text-white/50 text-xs">
                        <B>Org</B> — issuer is your plain Okta domain, <C>https://your-org.okta.com</C>.
                        This is Okta's recommendation for ordinary single sign-on, which is all Pinpoint
                        does.
                    </span>
                    <span className="text-white/50 text-xs">
                        <B>default</B> (a custom server) — issuer ends <C>/oauth2/default</C>. Use this if
                        your Okta administrator has put claims or policies on it.
                    </span>
                </span>
                If you are unsure, ask whoever administers Okta which one the app was assigned to.
            </>
        ),
        fields: ['OKTA_ISSUER'],
        note: <>You can check the value before saving: paste <C>{'<issuer>'}/.well-known/openid-configuration</C> into a browser. A page of JSON means it is right; a 404 or an error means it is not. That takes ten seconds and saves diagnosing a login loop.</>,
    },
]);

// ---------------------------------------------------------------------------
// Generic OIDC
//
// For a state-run identity provider, Keycloak, Shibboleth, or anything else
// standards-compliant. Written as questions to ask whoever runs it rather than
// as a menu path, because there is no menu to describe.
// ---------------------------------------------------------------------------

defineSteps('identity', 'oidc', (ctx) => [
    {
        body: (
            <>
                Ask whoever operates your identity provider to register a <B>confidential client</B>{' '}
                (sometimes called a web or server-side application) using the <B>authorization code</B>{' '}
                flow, with this redirect URI: <CopyValue ctx={ctx} id="oidcrd" value={CALLBACK(ctx.origin)} />.
                Ask for the scopes <C>openid</C>, <C>profile</C> and <C>email</C> — Pinpoint matches staff
                accounts by email address, so a provider that does not release it cannot be used.
            </>
        ),
        note: <>If they offer a "public client", that is the wrong kind: it issues no secret. Say confidential, or server-side.</>,
    },
    {
        body: (
            <>
                Ask for the <B>issuer URL</B> — the base address, not the full endpoint. You can check it
                yourself: <C>{'<issuer>'}/.well-known/openid-configuration</C> should return a page of
                JSON in a browser. If it 404s, the issuer is wrong.
            </>
        ),
        fields: ['OIDC_ISSUER'],
    },
    {
        body: <>Then the client's ID and secret.</>,
        fields: ['OIDC_CLIENT_ID', 'OIDC_CLIENT_SECRET'],
    },
]);

// ===========================================================================
// Maps
// ===========================================================================

// ---------------------------------------------------------------------------
// Google Maps
//
// Billing is step one rather than a footnote: without it Google issues a key
// that looks correct and the map shows a grey box, which is the single most
// common failure on this page.
// ---------------------------------------------------------------------------

defineSteps('maps', 'google', (ctx) => [
    {
        body: (
            <>
                In <L href="https://console.cloud.google.com">Google Cloud</L>, open{' '}
                <B>APIs &amp; Services → Library</B> and enable all three of <C>Maps JavaScript API</C>,{' '}
                <C>Geocoding API</C> and <C>Places API (New)</C>. Then open <B>Billing</B> and attach a
                payment method.
            </>
        ),
        check: <>an "API Enabled" banner on each of the three, and a billing account listed under Billing.</>,
        trouble: (
            <>
                Two things catch people here. The payment method is not optional and it is the step
                people skip — Google will issue a key without it, the key will look correct, and the map
                will show a grey box saying "this page can't load Google Maps correctly". Second, the
                Library lists both <C>Places API (New)</C> and an older <C>Places API</C>; they are
                separate products and enabling the old one does not enable the new one. Pinpoint's
                address box uses <C>Places API (New)</C>, so if you enable the wrong one the map draws
                and geocoding works while typing an address offers no suggestions at all.
            </>
        ),
    },
    {
        body: (
            <>
                Go to <B>APIs &amp; Services → Credentials → Create Credentials → API key</B>. Then open
                the key and restrict it: under <B>Application restrictions</B> choose <B>Websites</B> and
                add <CopyValue ctx={ctx} id="gmref" value={`${ctx.origin}/*`} /> — the <C>/*</C> matters.
                Under <B>API restrictions</B> choose <B>Restrict key</B> and tick the same three APIs,
                taking care to tick <C>Places API (New)</C> and not the older <C>Places API</C>.
            </>
        ),
        check: <>your site under Website restrictions, and only those three APIs ticked — with <C>Places API (New)</C> among them.</>,
        trouble: (
            <>
                An unrestricted key can be lifted off your site and run up a bill on the town's card. A
                website-restricted key cannot be checked from the server, so the Test button here will
                say so rather than claim a failure — confirm it by opening the report form.
            </>
        ),
    },
    {
        body: <>Paste the key here. The Map ID is optional and only changes how the map looks — leave it empty.</>,
        fields: ['GOOGLE_MAPS_API_KEY', 'GOOGLE_MAPS_MAP_ID'],
        note: <>Changes to a Google key can take up to five minutes. If the map is still grey straight after saving, wait before changing anything else.</>,
    },
]);

// ---------------------------------------------------------------------------
// Esri / ArcGIS
//
// The one most likely to already be in place: a town with a GIS department has
// an ArcGIS organisation, and often its own address locator, which is better
// than any national geocoder at finding a local address.
// ---------------------------------------------------------------------------

defineSteps('maps', 'esri', () => [
    {
        body: (
            <>
                Before anything else, ask your GIS department whether the town already has an ArcGIS
                organisation. If it does, the key should be created there rather than on a new personal
                developer account — otherwise it belongs to whoever signed up.
            </>
        ),
    },
    {
        body: (
            <>
                Sign in at <L href="https://location.arcgis.com">location.arcgis.com</L>, open the
                developer dashboard, and create an <B>API key</B> credential. Scope it to the services
                you need: <B>Basemap styles</B> and <B>Geocoding</B> at minimum. Set an expiry you will
                remember — ArcGIS API keys are valid for up to a year and then simply stop.
            </>
        ),
        check: <>a key string starting <C>AAPK</C>. (<C>AAPT</C> is Esri's prefix for short-lived access tokens — if that is what you have, it is not the right credential.)</>,
        note: <>Write the expiry date somewhere the town will see it. Keys expire at 00:00:00 GMT on the date you set, and when one lapses the map goes blank with no warning and nothing in the console saying why.</>,
    },
    {
        body: (
            <>
                Paste the key. Both other boxes are optional: the basemap is an Esri style id (for
                example <C>arcgis/navigation</C>) if you want something other than the default, and the
                locator is the URL of your own address locator service if the GIS department publishes
                one.
            </>
        ),
        fields: ['ARCGIS_API_KEY', 'ARCGIS_BASEMAP_ID', 'ARCGIS_LOCATOR_URL'],
        note: <>A town locator is worth asking for. It knows your street names, your address ranges and your recent subdivisions, which a national geocoder often does not.</>,
    },
]);

// ---------------------------------------------------------------------------
// Azure Maps
// ---------------------------------------------------------------------------

defineSteps('maps', 'azure', () => [
    {
        body: (
            <>
                In the <L href="https://portal.azure.com">Azure portal</L>, press <B>Create a
                resource</B>, search for <B>Azure Maps</B>, and create an account in the same
                subscription and region as the rest of your Azure services.
            </>
        ),
        check: <>the new Maps account's overview page.</>,
    },
    {
        body: (
            <>
                Open <B>Settings → Authentication</B> on that account and copy the <B>Primary Key</B>{' '}
                under Shared Key Authentication.
            </>
        ),
        fields: ['AZURE_MAPS_KEY'],
        note: <>Take the primary key and leave the secondary alone. The pair exists so a key can be rotated without downtime — using both at once removes the point of having two.</>,
    },
]);

// ---------------------------------------------------------------------------
// Apple Maps
//
// The most involved of the four and the only one where a downloaded file is the
// credential. It also requires a paid Apple Developer membership, which is
// worth saying at the top rather than discovering at step three.
// ---------------------------------------------------------------------------

defineSteps('maps', 'apple', () => [
    {
        body: (
            <>
                This needs a paid <L href="https://developer.apple.com/programs/">Apple Developer
                Program</L> membership held by the town, not by a person. If you do not
                have one, one of the other map providers will be much less work.
            </>
        ),
    },
    {
        body: (
            <>
                At <L href="https://developer.apple.com/account">developer.apple.com/account</L> open{' '}
                <B>Certificates, Identifiers &amp; Profiles → Identifiers</B>, press <B>+</B>, and create
                an identifier of type <B>Maps IDs</B>.
            </>
        ),
        check: <>your new Maps ID in the identifiers list.</>,
    },
    {
        body: (
            <>
                Then open <B>Keys</B> in the same sidebar, press <B>+</B>, tick <B>MapKit JS</B>, and
                configure it to use the Maps ID you just made. Create the key and press <B>Download</B>.
                You get a file named <C>AuthKey_XXXXXXXXXX.p8</C>.
            </>
        ),
        trouble: <>Download it now and keep it. Apple removes the file from their servers after the one download; if you lose it the only remedy is revoking the key and starting again.</>,
    },
    {
        body: (
            <>
                The <B>Key ID</B> is on that key's page. The <B>Team ID</B> is in{' '}
                <B>Membership details</B> at the top of your account. For the private key, open the{' '}
                <C>.p8</C> file in a plain text editor and paste the whole contents — including the{' '}
                <C>-----BEGIN PRIVATE KEY-----</C> and <C>-----END PRIVATE KEY-----</C> lines.
            </>
        ),
        fields: ['APPLE_MAPKIT_TEAM_ID', 'APPLE_MAPKIT_KEY_ID', 'APPLE_MAPKIT_PRIVATE_KEY'],
        note: <>Open it with a text editor, not Word — a word processor will add formatting that makes the key unreadable. The header and footer lines are part of the key, not decoration.</>,
    },
]);

// ===========================================================================
// AI analysis
// ===========================================================================

defineSteps('ai', 'vertex', () => [
    {
        body: (
            <>
                In <L href="https://console.cloud.google.com">Google Cloud</L>, select or create the
                project you want to bill this to, then open <B>APIs &amp; Services → Library</B> and
                enable <C>Vertex AI API</C>. Attach a billing account if the project has none.
            </>
        ),
        check: <>"API Enabled" on the Vertex AI API page.</>,
    },
    {
        body: (
            <>
                Open <B>IAM &amp; Admin → Service Accounts</B> and create one — a name like{' '}
                <C>pinpoint-311</C> is fine. Grant it the <B>Vertex AI User</B> role. Do not grant Owner
                or Editor: this account only ever needs to ask the model a question.
            </>
        ),
        note: <>The broad roles are one click away and it is tempting to take them to avoid a permissions problem later. A leaked key with Vertex AI User can run up a model bill; the same key with Editor can delete the project.</>,
    },
    {
        body: (
            <>
                On that service account open <B>Keys → Add Key → Create new key → JSON</B>. A file
                downloads. Open it in a plain text editor and paste the whole contents below, along with
                your project ID (it is in the file, as <C>project_id</C>).
            </>
        ),
        fields: ['VERTEX_AI_PROJECT', 'VERTEX_AI_SERVICE_ACCOUNT_KEY'],
        trouble: <>Delete the downloaded file once it is saved here. It is a password to your cloud account sitting in the Downloads folder of whichever machine did the setup.</>,
    },
]);

defineSteps('ai', 'azure', () => [
    {
        body: (
            <>
                In the <L href="https://portal.azure.com">Azure portal</L>, create an <B>Azure OpenAI</B>{' '}
                resource in a region your town is allowed to use. No application or approval is needed —
                every Azure subscription is eligible for the standard models.
            </>
        ),
        note: <>Older write-ups describe a registration form and a wait of a day or two. Microsoft removed that for general access; a form is now only required for specific restricted features, none of which Pinpoint uses. If someone tells you to apply and wait, they are working from stale instructions.</>,
    },
    {
        body: (
            <>
                Open the resource in <L href="https://ai.azure.com">Microsoft Foundry</L> and deploy a
                model — <B>Deployments → Deploy model</B>. The <B>deployment name</B> you choose is what
                goes in the box below, and it is not necessarily the model name: you can call a GPT-4o
                deployment anything you like, and Pinpoint asks for the deployment.
            </>
        ),
        check: <>your deployment listed with status Succeeded.</>,
    },
    {
        body: (
            <>
                Back on the Azure resource, open <B>Resource Management → Keys and Endpoint</B> and copy{' '}
                <B>KEY 1</B> and the <B>Endpoint</B>. Leave the API version box empty unless Microsoft
                has told you to pin one — it defaults to a current version.
            </>
        ),
        fields: ['AZURE_OPENAI_ENDPOINT', 'AZURE_OPENAI_API_KEY', 'AZURE_OPENAI_DEPLOYMENT', 'AZURE_OPENAI_API_VERSION'],
        note: <>The endpoint is the base address ending in a slash, not the full chat-completions URL from the sample code.</>,
    },
]);

defineSteps('ai', 'bedrock', () => [
    {
        body: (
            <>
                In the <L href="https://console.aws.amazon.com/bedrock">Bedrock console</L>, set the
                region selector (top right) to the region you intend to use — model availability differs
                by region and this choice is load-bearing for everything below.
            </>
        ),
    },
    {
        body: (
            <>
                In the left pane under <B>Bedrock configurations</B> choose <B>Model access</B>, then{' '}
                <B>Modify model access</B>. Select the models you want and submit the use-case form.
                Access usually appears within a few minutes.
            </>
        ),
        check: <>the model showing <B>Access granted</B> rather than Available to request.</>,
        note: <>If submitting fails, the account may not have AWS Marketplace subscription permission — that lives under <B>Billing → Marketplace settings</B> and usually needs whoever owns the AWS account.</>,
    },
    {
        body: (
            <>
                Create an IAM user for Pinpoint with permission to invoke models —{' '}
                <C>bedrock:InvokeModel</C> and <C>bedrock:InvokeModelWithResponseStream</C> — and nothing
                else. Take an access key from <B>Security credentials</B> and paste it here with the same
                region you set above.
            </>
        ),
        fields: ['AWS_REGION', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY'],
        trouble: <>The secret access key is shown once, on the screen where you create it. There is no way to see it again — only to issue a new one.</>,
    },
]);

// ===========================================================================
// Translation
// ===========================================================================

defineSteps('translation', 'google', () => [
    {
        body: (
            <>
                In <L href="https://console.cloud.google.com">Google Cloud</L>, open{' '}
                <B>APIs &amp; Services → Library</B> and enable <C>Cloud Translation API</C> in the same
                project you are using elsewhere.
            </>
        ),
        check: <>"API Enabled" on the Cloud Translation API page.</>,
    },
    {
        body: (
            <>
                That is the whole job: translation authenticates with the Google service account
                entered at Setup, so there is nothing to type here — no separate key, and no
                project ID, which the translator never reads.
            </>
        ),
        note: <>If this card says the service account is missing, it is entered at Setup rather than here — it is the credential that makes the rest of the secret store work, so it is deliberately not editable from a provider card.</>,
    },
]);

defineSteps('translation', 'azure', () => [
    {
        body: (
            <>
                In the <L href="https://portal.azure.com">Azure portal</L>, press <B>Create a
                resource</B> and create a <B>Translator</B> resource. Pick the tier that suits a
                small town's volume; note the <B>region</B> you pick, because it is part of the
                credentials.
            </>
        ),
    },
    {
        body: (
            <>
                Open <B>Resource Management → Keys and Endpoint</B> and copy <B>KEY 1</B> and the{' '}
                <B>Location/Region</B> value. Leave the endpoint box empty unless you were given a
                custom one — the default global endpoint is correct for almost everyone.
            </>
        ),
        fields: ['AZURE_TRANSLATOR_KEY', 'AZURE_TRANSLATOR_REGION', 'AZURE_TRANSLATOR_ENDPOINT'],
        note: <>The region must be the short form shown on that page, like <C>eastus</C>, not "East US". A mismatched region fails authentication and reports it as a bad key.</>,
    },
]);

defineSteps('translation', 'aws', () => [
    {
        body: (
            <>
                Amazon Translate needs no setup in its own console — it is on by default. Create an IAM
                user for Pinpoint with the <C>TranslateReadOnly</C> managed policy, which despite the
                name is what permits translating text.
            </>
        ),
        note: <>If you already made an IAM user for Bedrock or SES, add this policy to that one rather than creating another set of keys to keep track of.</>,
    },
    {
        body: <>Take an access key from that user's <B>Security credentials</B> tab and enter it with your region.</>,
        fields: ['AWS_REGION', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY'],
    },
]);

// ===========================================================================
// Email
// ===========================================================================

defineSteps('email', 'smtp', () => [
    {
        body: (
            <>
                Ask whoever runs the town's email for the SMTP server address and port, and for a
                dedicated account to send from. A separate account matters: when residents reply to a
                status update, the replies land wherever this sends from.
            </>
        ),
        note: <>Microsoft 365 and Google Workspace both restrict plain SMTP by default — on Microsoft it is switched off unless an administrator turns it back on. Worse, Microsoft is removing password-based SMTP from Exchange Online at the end of December 2026: existing tenants have it disabled by default from then, and tenants created afterwards cannot use it at all. If your town is on Microsoft 365, choosing SMTP today buys you a few months. Amazon SES or Azure Communication Services is the durable answer.</>,
    },
    {
        body: (
            <>
                Fill in the server details. Port <C>587</C> with STARTTLS is the usual answer; <C>465</C>{' '}
                is the older implicit-TLS port and also works — with the TLS box set to{' '}
                <C>false</C>, because implicit TLS and STARTTLS are different handshakes.
            </>
        ),
        fields: ['SMTP_HOST', 'SMTP_PORT', 'SMTP_USER', 'SMTP_PASSWORD', 'SMTP_USE_TLS'],
    },
    {
        body: (
            <>
                Then what residents see in their inbox. Use a monitored address —{' '}
                <C>311@yourtown.gov</C> rather than <C>noreply@</C>, because people reply to these and a
                reply that goes nowhere is a complaint you never hear.
            </>
        ),
        fields: ['SMTP_FROM_EMAIL', 'SMTP_FROM_NAME'],
    },
]);

defineSteps('email', 'ses', () => [
    {
        body: (
            <>
                In the <L href="https://console.aws.amazon.com/ses">SES console</L>, set your region,
                then open <B>Identities → Create identity</B> and verify your <B>domain</B> — not just a
                single address. Verifying the domain lets you send from any address on it, and it is what
                makes DKIM available.
            </>
        ),
        check: <>the identity showing <B>Verified</B>. It requires DNS records, so whoever manages the town's DNS needs to be involved.</>,
    },
    {
        body: (
            <>
                New SES accounts are in the <B>sandbox</B> and can only send to addresses you have also
                verified — which means residents get nothing. Open <B>Account dashboard</B> and press{' '}
                <B>Request production access</B>. Approval usually takes about a day.
            </>
        ),
        trouble: <>This is the step that quietly breaks a launch. In the sandbox everything looks like it works: SES accepts the message and returns success, and the resident never receives it.</>,
    },
    {
        body: (
            <>
                Create an IAM user for Pinpoint with the <C>AmazonSESFullAccess</C> policy (or just{' '}
                <C>ses:SendEmail</C> and <C>ses:SendRawEmail</C> if you would rather be precise), then
                fill these in. The from address must be on the domain you verified.
            </>
        ),
        fields: ['AWS_REGION', 'SES_FROM_EMAIL', 'SMTP_FROM_NAME', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY'],
        note: <>The region must be the one where you verified the domain. SES identities do not exist across regions, and a mismatch reports as an unverified sender.</>,
    },
]);

defineSteps('email', 'acs', () => [
    {
        body: (
            <>
                In the <L href="https://portal.azure.com">Azure portal</L> create two resources:{' '}
                <B>Communication Services</B>, and <B>Email Communication Services</B>. Both are needed,
                and they must be in the <B>same geography</B> — you cannot connect them otherwise.
            </>
        ),
        note: <>Two resources with near-identical names is genuinely confusing. The Email one owns the domain; the plain Communication Services one owns the endpoint and key.</>,
    },
    {
        body: (
            <>
                On the Email resource, add a domain. <B>Add a free Azure subdomain</B> is one click and
                works immediately, but sends from an <C>azurecomm.net</C> address, which residents will
                not recognise as the town. <B>Set up a custom domain</B> takes DNS records (TXT, SPF and
                DKIM) and is worth it for anything public-facing.
            </>
        ),
        check: <>the domain listed with verification complete, and a MailFrom address shown on its page.</>,
    },
    {
        body: (
            <>
                Open the Communication Services resource, go to <B>Email → Domains</B> and press{' '}
                <B>Connect domain</B> to link the one you just made.
            </>
        ),
    },
    {
        body: (
            <>
                Then on that same resource open <B>Settings → Keys</B> and copy the <B>Endpoint</B> and{' '}
                <B>Primary key</B>. The from address is the MailFrom value on the domain page.
            </>
        ),
        fields: ['ACS_ENDPOINT', 'ACS_ACCESS_KEY', 'ACS_FROM_EMAIL', 'SMTP_FROM_NAME'],
    },
]);

// ===========================================================================
// Text messages
// ===========================================================================

defineSteps('sms', 'twilio', () => [
    {
        body: (
            <>
                Sign in at <L href="https://console.twilio.com">console.twilio.com</L>. The{' '}
                <B>Account SID</B> and <B>Auth Token</B> are on the dashboard home page.
            </>
        ),
        fields: ['TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN'],
    },
    {
        body: <>Buy or select a number under <B>Phone Numbers → Manage → Active numbers</B>, and enter it in <C>+1XXXXXXXXXX</C> form.</>,
        fields: ['TWILIO_PHONE_NUMBER'],
        note: <>It has to be a number you own in Twilio. A trial account can only text numbers you have verified.</>,
    },
]);

defineSteps('sms', 'sns', () => [
    {
        body: (
            <>
                In the <L href="https://console.aws.amazon.com/sns">SNS console</L>, open <B>Text
                messaging (SMS)</B> in the left pane and check the account status at the top. A new
                account is in the <B>SMS sandbox</B> and can only text numbers you have verified there.
            </>
        ),
        trouble: <>Leave the sandbox before launch, from that same page. Inside it, messages to residents are rejected — and the rejection is in a log, not on the page where somebody would see it.</>,
    },
    {
        body: (
            <>
                Still on that page, set a <B>monthly spending limit</B> you are comfortable with, then
                register an origination identity. For a US municipality that normally means a{' '}
                <B>10DLC</B> number, which requires registering the town as a brand; the carriers filter
                unregistered traffic heavily.
            </>
        ),
        note: <>Start the 10DLC registration early. It is a carrier process rather than an AWS one, and it is not immediate.</>,
    },
    {
        body: (
            <>
                Create an IAM user for Pinpoint with permission for <C>sns:Publish</C>, and enter its
                access key with your region. The sender ID box is optional and only has an effect in
                countries that support alphabetic senders — it is ignored for US numbers.
            </>
        ),
        fields: ['AWS_REGION', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'SMS_SENDER_ID'],
    },
]);

defineSteps('sms', 'acs', () => [
    {
        body: (
            <>
                <B>Register before you try to buy a number.</B> On the Communication Services resource,
                open <B>Telephony and SMS → Regulatory Documents</B> and complete the 10DLC{' '}
                <B>brand</B> and <B>campaign</B> registration for the town. In the US, both have to be
                approved before you can acquire or SMS-enable a number at all — this is not a step you
                can come back to.
            </>
        ),
        note: <>You also need a paid Azure subscription. Phone numbers cannot be acquired on a trial or with free credits, and availability depends on your subscription's billing country.</>,
    },
    {
        body: (
            <>
                Once registration is approved, go to <B>Telephony and SMS → Phone numbers</B> and acquire
                a number with SMS among its capabilities. Use the same Communication Services resource as
                email if you already made one.
            </>
        ),
        check: <>the number listed with SMS in its capabilities.</>,
    },
    {
        body: (
            <>
                Then <B>Settings → Keys</B> on that resource for the endpoint and key, and the number you
                just acquired in <C>+1XXXXXXXXXX</C> form.
            </>
        ),
        fields: ['ACS_ENDPOINT', 'ACS_ACCESS_KEY', 'SMS_FROM_NUMBER'],
    },
]);

defineSteps('sms', 'http', () => [
    {
        body: (
            <>
                For a gateway not listed here — a regional carrier, a state contract, an existing
                notification system. Pinpoint POSTs JSON with the number and the message, and treats
                any 2xx as delivered.
            </>
        ),
    },
    {
        body: (
            <>
                Enter the endpoint URL your provider gave you, and the key they issued. The key is sent
                as a bearer token. If your gateway needs a sender number, add it too; most set the
                sender themselves, in which case leave it blank.
            </>
        ),
        fields: ['SMS_HTTP_API_URL', 'SMS_HTTP_API_KEY', 'SMS_FROM_NUMBER'],
        note: <>If their API expects a different shape or a different header, this will fail on every send. Check with them before relying on it, and send yourself a test message from the button below.</>,
    },
    {
        body: (
            <>
                If your gateway publishes a URL that reports your balance, quota or key status,
                paste it here. Everything else on this page can be checked without doing anything
                a resident would notice; a gateway that only accepts sends cannot, so without this
                the card can only ever say it does not know.
            </>
        ),
        fields: ['SMS_HTTP_TEST_URL'],
        note: <>Optional, and it is only ever fetched — nothing is sent to it. Leave it blank and the card will say plainly that it cannot check this one, rather than guessing.</>,
    },
]);

/**
 * The risk none of the key-level controls cover.
 *
 * Denying deletion, purge protection and liens all stop somebody deleting the
 * *key*. None of them survives the loss of the *account* that holds it, and for
 * a municipality that is the likelier way this ends: a card that expires
 * between budget cycles, a subscription opened on a departing employee's
 * personal account, a purchasing gap nobody notices until the disable notice.
 *
 * Verified timelines, all of which start quietly: an Azure subscription is
 * deleted 90 days after cancellation and its resources go with it; an AWS
 * account's resources are deleted 90 days after closure; a deleted Google
 * project takes its key ring, and Google commits to erasing the key material
 * within 45 days. The key survives none of those, and no setting inside the key
 * changes it.
 */
const ACCOUNT_SURVIVES = (
    <>
        <B>The boring one that matters most.</B> Everything above protects the key. Nothing protects
        the <em>account</em>, and a closed account takes the key with it. So put the cloud account in
        the town's name on a town payment method, make more than one person an administrator, and send
        billing alerts to a shared address. This, not a mis-click, is how towns actually lose the
        data — a lapsed card between budget cycles.
    </>
);

// ===========================================================================
// Key management for resident data
//
// These wrap the key that encrypts resident names, emails and phone numbers.
// The recurring warning is the same in all three: this key is not something you
// can lose and re-create. Losing it means the data it protected is gone.
// ===========================================================================

/**
 * The identity a deployment running *on* the cloud already has.
 *
 * Every cloud attaches one to the compute, `cloud_identity.py` detects it, and
 * the card greys out the boxes it replaces. It belongs in the walk as well,
 * because by the time a clerk reads the card they have usually already created
 * the credential it would have saved them -- and on Azure that credential is a
 * client secret with an expiry date nobody records.
 */
const ATTACHED_IDENTITY = (
    <>
        If this server runs on the same cloud, skip creating a credential: grant the role to the
        machine's own identity instead. Nothing to copy, nothing to expire.
    </>
);

defineSteps('kms', 'google', () => [
    {
        body: (
            <>
                In <L href="https://console.cloud.google.com">Google Cloud</L>, enable{' '}
                <C>Cloud KMS API</C>, then under <B>Security → Key Management</B> create a{' '}
                <B>key ring</B> and a <B>key</B> inside it. Purpose{' '}
                <B>Symmetric encrypt/decrypt</B>, rotation 90 days. Set the <B>destroy scheduled
                duration</B> to the longest offered while you are on that form — it defaults to 24
                hours, goes to 120 days, and is only offered at creation.
            </>
        ),
        check: <>your key listed inside your key ring.</>,
        note: <>Needs Cloud KMS Admin or Owner. Project Editor cannot create keys — worth knowing now rather than three screens in. Azure and AWS have a one-click template on their cards and Google has none, because Google has no equivalent: the honest alternative is Terraform, which needs a toolchain and a state file rather than a click. This walk is maintained on the same footing as the other two.</>,
    },
    {
        body: (
            <>
                Grant the service account this deployment uses the role <B>Cloud KMS CryptoKey
                Encrypter/Decrypter</B> from the key's own permissions panel — on the key, not on the
                project. {ATTACHED_IDENTITY}
            </>
        ),
        check: <>the service account listed in the key's permissions, not only in the project's.</>,
        trouble: <>Without this the key exists, the settings save, and resident data is quietly encrypted with the application key instead. The health dashboard will say so — it is the one place that reports it.</>,
    },
    {
        body: (
            <>
                Exactly as the console shows them. The location is the short form, like{' '}
                <C>us-central1</C>. The project and service account JSON go in Setup, not here.
            </>
        ),
        fields: ['KMS_LOCATION', 'KMS_KEY_RING', 'KMS_KEY_ID'],
    },
    {
        body: (
            <>
                <B>Check who can destroy a version.</B> Keys and key rings cannot be deleted, but a key{' '}
                <em>version</em> can be, which loses the data just as well. Check no everyday account
                holds <C>cloudkms.cryptoKeyVersions.destroy</C>.
            </>
        ),
        check: <>only administrators holding destroy on this key.</>,
        trouble: <>Rotating is safe — old versions stay and keep decrypting old rows. <B>Destroying</B> a version is the fatal one, and the two sit next to each other in the console. Google cannot recover a destroyed version for anyone.</>,
    },
    {
        body: (
            <>
                <B>Protect the project too.</B> Deleting the project takes the key ring with it, whatever
                is set on the key. A <B>lien</B> blocks that until somebody removes the lien:
                <span className="mt-2 block"><C>gcloud alpha resource-manager liens create --project=YOUR_PROJECT --restrictions=resourcemanager.projects.delete --reason="Holds the Pinpoint 311 PII encryption key"</C></span>
            </>
        ),
        check: <>the lien returned by <C>liens list</C>.</>,
        trouble: <>No command line? Ask whoever manages your Google Cloud billing to run it. It is free, and it is the difference between a mis-click being an inconvenience and being permanent.</>,
    },
    {
        body: <>{ACCOUNT_SURVIVES}</>,
    },
]);

defineSteps('kms', 'azure', () => [
    {
        body: (
            <>
                <B>One form, instead of six screens.</B> This opens Azure's own Custom deployment page
                with our template loaded: the vault, purge protection and the RSA key, and optionally
                the Azure OpenAI, Translator and Vision resources the other cards
                need. {WHY_A_TEMPLATE_IS_SAFE}
                <DeployButton href={AZURE_DEPLOY_URL}>Deploy to Azure</DeployButton>
            </>
        ),
        check: <>Azure's Custom deployment form, listing every name before you press Create.</>,
        note: <>Template and README: <C>deploy/templates/azure/</C>. For a diff from Azure rather than a description from us, run <C>az deployment group what-if</C> against it. It cannot create the app registration in step 5 — directory objects are outside the reach of resource templates.</>,
    },
    {
        body: (
            <>
                <B>Or build it by hand</B> — equally supported, and complete on its own. In the{' '}
                <L href="https://portal.azure.com">Azure portal</L>, create a <B>Key Vault</B>. Before
                creating it, open the <B>Access configuration</B> tab: it sets how permissions on this
                vault work, and governs every step below. Leave it on <B>Azure role-based access
                control</B>, the default these steps assume. Turn on <B>purge protection</B> beside
                the retention period.
            </>
        ),
        check: <>the vault's overview, showing a URL ending <C>.vault.azure.net</C>.</>,
        trouble: <>Take purge protection now. On some configurations it cannot be turned on later, and it is the only setting that makes an accidental deletion survivable. Nor can it be turned off again — the point of it.</>,
    },
    {
        body: (
            <>
                <B>Give yourself permission to use the vault.</B> Creating a vault grants its creator
                no access to the keys inside it — a separate grant, whose absence is why the key screen
                refuses to work for whoever just built the vault. On the vault's <B>Access control
                (IAM)</B>, assign yourself <B>Key Vault Crypto Officer</B>.
            </>
        ),
        check: <>your account on the vault's role assignments as Key Vault Crypto Officer.</>,
        note: <>Give it a minute. On the older access policy model, add yourself under the vault's access policies instead.</>,
    },
    {
        body: <>Open the vault's <B>Objects → Keys</B> and use <B>Generate/Import</B> to create an <B>RSA</B> key. 2048 or 4096, either is fine. Note its name.</>,
        check: <>the key listed, status Enabled.</>,
        trouble: <>If this screen says you are not authorised to view the contents, the role above has not arrived yet, or was assigned somewhere other than the vault itself.</>,
    },
    {
        body: (
            <>
                Now the identity Pinpoint signs in as. {ATTACHED_IDENTITY} Otherwise register an app
                under <B>Microsoft Entra ID → App registrations</B>, open its{' '}
                <B>Certificates &amp; secrets</B> and add a client secret. Copy its <B>Value</B> at once.
            </>
        ),
        check: <>an Application (client) ID and Directory (tenant) ID on the app's overview, and a copied Value.</>,
        trouble: <>Entra shows the secret once and then replaces it with dots forever. The box beside it labelled <B>Secret ID</B> is not the secret.</>,
    },
    {
        body: (
            <>
                Back on the vault's <B>Access control (IAM)</B>, assign that identity <B>Key Vault
                Crypto User</B>: it wraps and unwraps with this key and does nothing else. On the
                older model, grant <B>Get</B>, <B>Wrap Key</B> and <B>Unwrap Key</B> instead.
            </>
        ),
        check: <>that identity on the vault's role assignments as Key Vault Crypto User.</>,
        trouble: <>On the access policy model, Wrap and Unwrap are the two people miss. Get on its own is not enough, and the failure is silent.</>,
    },
    {
        body: <>Fill these in. The vault URL is the <C>https://yourvault.vault.azure.net/</C> address on the overview.</>,
        fields: ['AZURE_KEYVAULT_URL', 'AZURE_KEYVAULT_KEY', 'AZURE_TENANT_ID', 'AZURE_KEYVAULT_CLIENT_ID', 'AZURE_KEYVAULT_CLIENT_SECRET'],
        note: <>On a managed identity the last three stay empty. With a client secret, put its expiry in the town's calendar a month ahead — when it lapses, resident data stops decrypting and nothing about that failure points at a calendar.</>,
    },
    {
        body: (
            <>
                Lock it down. On <B>Properties</B>, confirm soft delete and purge protection are both
                enabled, then under <B>Settings → Locks</B> add a <B>Delete</B> lock, which stops
                anyone deleting the vault even with permission to. Drop your own Crypto Officer role
                now if you like — the key exists, and Pinpoint never uses it.
            </>
        ),
        check: <>Soft delete: Enabled, Purge protection: Enabled, and a Delete lock on the vault.</>,
        note: <>Check too that everyday accounts hold neither Delete nor Purge on keys.</>,
    },
    {
        body: <>{ACCOUNT_SURVIVES}</>,
    },
]);

defineSteps('kms', 'aws', () => [
    {
        body: (
            <>
                <B>One stack, instead of the console.</B> This opens CloudFormation with our template
                loaded: the key, its alias and its policy, plus one role carrying exactly the
                permissions Pinpoint needs. No access keys, so a server on EC2 or ECS pastes no
                credential at all. {WHY_A_TEMPLATE_IS_SAFE}
                <DeployButton href={AWS_DEPLOY_URL}>Launch CloudFormation stack</DeployButton>
            </>
        ),
        check: <>CloudFormation's review page, listing every resource before anything is created.</>,
        note: <>Template and README: <C>deploy/templates/aws/</C>. Take a <B>change set</B> rather than a stack for a diff from Amazon instead of a description from us. It fails rather than touching anything if the alias or role name is taken.</>,
    },
    {
        body: (
            <>
                <B>Or build it by hand</B> — equally supported, and complete on its own. In the{' '}
                <L href="https://console.aws.amazon.com/kms">KMS console</L> set your region, choose{' '}
                <B>Customer managed keys</B>, then <B>Create key</B>. Key type <B>Symmetric</B>, usage{' '}
                <B>Encrypt and decrypt</B> — both are the defaults. Give it an alias like{' '}
                <C>pinpoint-311-pii</C> and turn on automatic rotation.
            </>
        ),
        check: <>the key under Customer managed keys, status Enabled, your alias beside it.</>,
    },
    {
        body: (
            <>
                The wizard then asks who administers the key and who may use it. Add the identity this
                deployment signs in to AWS as under <B>key users</B>. {ATTACHED_IDENTITY} On EC2 or
                ECS that is the instance or task role, and it is the better answer: nothing
                long-lived exists to leak or expire.
            </>
        ),
        check: <>your role or user listed as a key user on this key and no other.</>,
        trouble: <>Keep your own administrators in that policy. A KMS key whose policy locks out the account that owns it cannot be repaired afterwards by anybody, including AWS support.</>,
    },
    {
        body: <>Enter the region and the key ID — the alias works, but the ID is unambiguous.</>,
        fields: ['AWS_REGION', 'AWS_KMS_KEY_ID'],
        note: <>No secret to enter. Pinpoint reaches this key with the identity added above, which is why an instance role beats an access key.</>,
    },
    {
        body: (
            <>
                <B>Make deletion impossible, not just inadvisable.</B> AWS has no switch for this, so
                do it in the key policy: add a statement that <B>denies</B>{' '}
                <C>kms:ScheduleKeyDeletion</C> and <C>kms:DisableKey</C> to everyone. An explicit deny
                beats every allow, including the account root, so nobody can start a deletion. To
                retire the key later, an administrator edits the policy first — one deliberate step
                where there was none.
            </>
        ),
        check: <>a Deny statement naming both actions in the key policy.</>,
        trouble: <>The key stops working the moment deletion is <em>scheduled</em>, not when the 30 days end — so resident data breaks at the start of the window. It can be cancelled inside those 30 days. After that the data is <B>unrecoverable</B>, by you and by AWS.</>,
    },
    {
        body: <>{ACCOUNT_SURVIVES}</>,
    },
]);

// ===========================================================================
// Secret storage
//
// These providers collect nothing: the credentials are the ones already entered
// on the PII Encryption card or in Setup. What each needs is a second
// permission on that same identity, and on all three clouds the grant that lets
// Pinpoint use the encryption key grants nothing over secrets. Azure is the
// sharp one -- a vault using Azure roles gives nobody access to its secrets by
// default, including whoever created the vault, which is the same trap that
// stops the key screen working on a brand-new vault.
// ===========================================================================

defineSteps('secrets', 'azure', () => [
    {
        body: (
            <>
                This is the vault from the PII Encryption card, so there is nothing to create and
                nothing to enter below — only one more role. On the vault's <B>Access control
                (IAM)</B>, add a role assignment of <B>Key Vault Secrets Officer</B> to the identity
                you gave Crypto User there. Keys and secrets are separate permissions on the same
                vault, so the key role grants nothing here.
            </>
        ),
        check: <>that identity listed twice on the vault's role assignments: Key Vault Crypto User, and Key Vault Secrets Officer.</>,
        trouble: <>Officer, not User. Pinpoint writes credentials into this vault as well as reading them, and Key Vault Secrets User can only read — which fails on the first save rather than on the connection test.</>,
    },
    {
        body: <>Creating the vault did not give <em>you</em> access to its secrets either, for the same reason it did not give you access to its keys.</>,
        note: <>If you want to read these credentials yourself in the portal, grant your own account Key Vault Secrets Officer on the vault as well.</>,
    },
]);

defineSteps('secrets', 'google', () => [
    {
        body: (
            <>
                This uses the service account already entered in Setup, so there is nothing to enter
                below. Enable <C>Secret Manager API</C> on the project, then grant that service account{' '}
                <B>Secret Manager Admin</B>:
                <span className="mt-2 block"><C>gcloud projects add-iam-policy-binding YOUR_PROJECT --member=serviceAccount:YOUR_SERVICE_ACCOUNT --role=roles/secretmanager.admin</C></span>
            </>
        ),
        check: <>the service account listed with Secret Manager Admin on the project's IAM page.</>,
        trouble: <>Secret Manager Secret Accessor is not enough. Pinpoint creates and updates secrets here as well as reading them, so an accessor-only grant saves the choice and then fails on the first credential written.</>,
    },
]);

defineSteps('secrets', 'aws', () => [
    {
        body: (
            <>
                This uses the identity your server already signs in to AWS as — the instance role,
                ideally — so there is nothing to enter below. Attach a policy allowing{' '}
                <C>secretsmanager:CreateSecret</C>, <C>secretsmanager:PutSecretValue</C>,{' '}
                <C>secretsmanager:GetSecretValue</C>, <C>secretsmanager:DescribeSecret</C> and{' '}
                <C>secretsmanager:ListSecrets</C> in the region entered on the other AWS cards.
            </>
        ),
        check: <>those five actions on the role or user's attached policy.</>,
        note: <>Pinpoint names each secret after the credential it holds, so none of them exists until the first save — which is why the create permission is needed, and why a policy scoped to an existing secret's ARN will not work on a fresh install.</>,
    },
]);

// ===========================================================================
// Photo redaction
//
// Three cloud detectors and one that runs here. The steps are short because the
// work is a permission, not a console tour -- and the settings below are what a
// town is actually deciding, which is what to blur.
// ===========================================================================

/**
 * The two warnings every redaction path needs.
 *
 * Redaction is the one capability whose failure is invisible from inside the
 * product. A misconfigured email provider bounces and somebody notices; a
 * detector that finds nothing looks exactly like a photo with nobody in it. The
 * blurring either happened or it did not, and the only way to know is to look
 * at a photo.
 */
const UNCONFIGURED_DETECTOR = (
    <>
        Wrong credentials fail <em>quietly</em>: the detector finds nothing, nothing is blurred, the
        photo is stored, and the card still reads as on — "found nobody" and "could not ask" look
        identical from here. Check the Photo Redaction line on the health dashboard, then do the test
        below.
    </>
);

const VERIFY_WITH_A_PHOTO = (
    <>
        <B>Test it once, with a real photo.</B> File a test report from the resident portal with a
        recognisable face in the picture, then look at the stored image in the staff dashboard. It is
        the only thing that proves this works. Delete the test report afterwards.
    </>
);

const REDACTION_CHOICE = (
    <>
        Faces and licence plates are both on by default — residents photograph potholes with cars and
        neighbours in shot, and none of those people asked to be in a public record.
    </>
);

defineSteps('redaction', 'google', () => [
    {
        body: (
            <>
                In <L href="https://console.cloud.google.com">Google Cloud</L>, open{' '}
                <B>APIs &amp; Services → Library</B> and enable <C>Cloud Vision API</C>. The service
                account this deployment already uses needs no extra role beyond being able to call it.
            </>
        ),
        check: <>"API Enabled" on the Cloud Vision API page.</>,
        note: <>{UNCONFIGURED_DETECTOR}</>,
    },
    { body: REDACTION_CHOICE, fields: ['REDACT_FACES', 'REDACT_PLATES'], trouble: VERIFY_WITH_A_PHOTO },
]);

defineSteps('redaction', 'aws', () => [
    {
        body: (
            <>
                Amazon Rekognition needs nothing enabled in its console. Add{' '}
                <C>rekognition:DetectFaces</C> and <C>rekognition:DetectText</C> to the IAM user this
                deployment uses — plate detection works by reading text in the image, so leaving
                DetectText off silently disables plates while faces keep working.
            </>
        ),
        check: <>both actions listed on the IAM user's policy.</>,
        note: <>{UNCONFIGURED_DETECTOR}</>,
    },
    { body: REDACTION_CHOICE, fields: ['REDACT_FACES', 'REDACT_PLATES'], trouble: VERIFY_WITH_A_PHOTO },
]);

// Azure is the only redaction backend with credentials of its own. Google and
// AWS reuse what was entered elsewhere; Azure needs two separate resources,
// because Microsoft splits face detection and text reading across them.

defineSteps('redaction', 'azure', () => [
    {
        body: (
            <>
                <B>Read this before choosing Azure.</B> Faces and plates come from two different Azure
                services, so you create two resources, and the face one is gated: Microsoft keeps the{' '}
                <B>Face API</B> behind a Limited Access review under its Responsible AI Standard.
                Detection — returning rectangles, which is all Pinpoint does — is the least restricted
                use, but the subscription still has to be approved before it will answer. If you only
                want plates, you can skip the face resource entirely.
            </>
        ),
        note: <>If your town cannot get through that review, pick a different detector. Google and Amazon have no equivalent gate, and the on-server option has none at all.</>,
    },
    {
        body: (
            <>
                For faces: in the <L href="https://portal.azure.com">Azure portal</L> create a <B>Face</B>{' '}
                resource (under Azure AI services), then open{' '}
                <B>Resource Management → Keys and Endpoint</B> and copy <B>KEY 1</B> and the endpoint.
            </>
        ),
        fields: ['AZURE_FACE_ENDPOINT', 'AZURE_FACE_KEY'],
    },
    {
        body: (
            <>
                For plates: create a <B>Computer Vision</B> resource — Microsoft also lists this as{' '}
                <B>Azure AI Vision</B>, and the two names refer to the same thing. Copy its key and
                endpoint from the same place. Plates are found by reading the text in the photo, which
                is why this is a separate service from the one that finds faces.
            </>
        ),
        fields: ['AZURE_VISION_ENDPOINT', 'AZURE_VISION_KEY'],
        note: <>A multi-service Azure AI resource covers the vision half, but not Face — that one is always its own resource.</>,
    },
    { body: REDACTION_CHOICE, fields: ['REDACT_FACES', 'REDACT_PLATES'] },
]);

defineSteps('redaction', 'local', () => [
    {
        body: (
            <>
                <B>This is what you get if you choose nothing.</B> Detection runs on this server using
                OpenCV, with no account and no key — and no resident photo ever leaves the
                building, which is the real reason to keep it.
            </>
        ),
        note: <>It is the default because the alternative was worse. A deployment with no cloud credentials used to blur nothing at all while the page displayed Google Cloud Vision as the provider. Imperfect blurring beats none; that is the whole argument, and it is worth knowing which one you are relying on.</>,
    },
    {
        body: (
            <>
                <B>What it misses.</B> It finds faces that are roughly front-on and reasonably large.
                Small faces, faces in profile, faces in shadow or behind glass are often missed, and
                plate detection is weaker still. The cloud detectors are meaningfully better at all of
                those. If your town publishes photos to a public map and a missed face is a serious
                problem, one of them is worth the setup.
            </>
        ),
        note: <>The safest arrangement for a town with no cloud account is to keep this on <em>and</em> have a person look at photos before they are published, rather than treating either one as sufficient on its own.</>,
    },
    { body: REDACTION_CHOICE, fields: ['REDACT_FACES', 'REDACT_PLATES'], trouble: VERIFY_WITH_A_PHOTO },
]);
