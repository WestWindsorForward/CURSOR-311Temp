import type { Capability } from '../services/api';

/**
 * The setup guide, organised by the console you have to open.
 *
 * The guide used to have one section per capability, in a fixed order: staff
 * sign-in, then maps, then AI, then translation, then key management. For a
 * town on Azure that reads as Azure, Google, Azure, Azure, Azure -- five
 * sections, four of which are the same portal, each repeating "create a
 * resource in pinpoint311-rg" as though you had not just done it. A clerk
 * following it top to bottom opens the Azure portal, leaves for Google, and
 * comes back three times.
 *
 * So the unit is not a capability, it is a trip. Everything that lives behind
 * one login gets folded into one task: the foundation steps once, then each
 * thing to create while you are already in there. A town on Azure using Entra
 * for sign-in and Azure Maps has a single Azure task covering sign-in, maps,
 * AI, translation, Key Vault and Content Safety. A town on Azure using Google
 * Maps has two tasks, because that genuinely is two logins.
 *
 * This file is the arithmetic and nothing else -- no JSX, no fetching -- so the
 * grouping can be tested directly rather than through a rendered page.
 */

export type Cloud = 'google' | 'azure' | 'aws';
export type Idp = 'auth0' | 'entra' | 'okta' | 'oidc';
export type MapProvider = 'google' | 'esri' | 'azure' | 'apple';

/** Who you sign in to. Two capabilities share a vendor iff they share a login. */
export type Vendor =
    | 'google' | 'azure' | 'aws'
    | 'auth0' | 'okta' | 'oidc'
    | 'esri' | 'apple'
    | 'twilio' | 'smtp' | 'http'
    | 'sentry' | 'storage' | 'local';

export const VENDOR_LABEL: Record<Vendor, string> = {
    google: 'Google Cloud',
    azure: 'Microsoft Azure',
    aws: 'AWS',
    auth0: 'Auth0',
    okta: 'Okta',
    oidc: 'Your OIDC provider',
    esri: 'Esri / ArcGIS',
    apple: 'Apple',
    twilio: 'Twilio',
    smtp: 'Your mail server',
    http: 'Your SMS gateway',
    sentry: 'Sentry',
    storage: 'Your backup storage',
    local: 'This server',
};

/* Entra is administered in the Azure portal, so a town on Azure that uses it
 * for staff sign-in is not making a second trip. Getting this wrong in the
 * other direction would be worse than not grouping at all: it would promise one
 * login and then need two. */
const IDP_VENDOR: Record<Idp, Vendor> = {
    auth0: 'auth0', entra: 'azure', okta: 'okta', oidc: 'oidc',
};
const AI_VENDOR: Record<string, Vendor> = { vertex: 'google', azure: 'azure', bedrock: 'aws' };
const EMAIL_VENDOR: Record<string, Vendor> = { smtp: 'smtp', ses: 'aws', acs: 'azure' };
const SMS_VENDOR: Record<string, Vendor> = { twilio: 'twilio', sns: 'aws', acs: 'azure', http: 'http' };

/** One thing to set up, inside one task. */
export interface PlanItem {
    /** Stable id, used for the "done" lookup and as a React key. */
    id: string;
    title: string;
    /** What it does, in a sentence, for someone who did not pick it themselves. */
    blurb: string;
    /** The capability whose catalog holds its credentials, if it has one. */
    cap?: Capability;
    /** Which provider of that capability. */
    provider?: string;
    /** Settings with no capability card, entered as plain boxes. */
    secrets?: { key: string; label: string; secret?: boolean; help?: string }[];
    /* No `choices` here on purpose. Every provider decision is made once, in
     * the questionnaire at the top, so a task never asks again -- asking twice
     * is how a clerk ends up with a section configured for one provider and a
     * questionnaire that says another. */
    /** Required to take a report at all. */
    required?: boolean;
}

export interface PlanTask {
    id: string;
    vendor: Vendor;
    title: string;
    /** Why this task exists, shown under the title. */
    blurb: string;
    /** Whether this task needs the cloud foundation walk (project, resource
     *  group, IAM) before its items. Only the three clouds do. */
    foundation: Cloud | null;
    items: PlanItem[];
    required: boolean;
}

export interface PlanInput {
    cloud: Cloud;
    idp: Idp;
    maps: MapProvider;
    aiProvider: string;
    emailProvider: string;
    smsProvider: string;
    redactionProvider: string;
    /** Feature ids ticked in the questionnaire. */
    wanted: ReadonlySet<string>;
    /* Item ids to leave out even though the questionnaire cannot hide them.
     *
     * Sign-in and maps are added unconditionally below, because no town can
     * take a report without both -- there is no tick for either, and there
     * should not be. That is a statement about a town, and it is not true of
     * every console that renders this plan: a hosting panel walks an operator
     * through the credentials the HOST pays for, and a host that has decided
     * its towns bring their own map key has no map credential to enter and no
     * task to be shown.
     *
     * Optional, and absent everywhere in this application: nothing here passes
     * it, so every plan built by a town's own console is unchanged. It is a
     * hole left deliberately rather than a feature in use, so that the console
     * with the extra question does not have to fork this arithmetic to ask it.
     */
    exclude?: ReadonlySet<string>;
}

/* Order is the order a town should work in, not the order the code was
 * written. Sign-in and maps are required, so whichever task carries them comes
 * first; the cloud task is next because most optional features hang off it. */
const VENDOR_ORDER: Vendor[] = [
    'auth0', 'okta', 'oidc',            // sign-in, when it is its own vendor
    'azure', 'google', 'aws',           // the clouds
    'esri', 'apple',                    // maps, when it is its own vendor
    'smtp', 'twilio', 'http',           // delivery, when it is its own vendor
    'local', 'storage', 'sentry',
];

/**
 * The two settings that belong to no provider, defined once.
 *
 * They were written twice: here, for the guide, and again by hand in the
 * "Other settings" block on the same page. Two copies of a credential form is
 * how the guide and the cards drifted last time -- one said Okta's issuer was
 * the org URL and the other said the opposite -- so these are exported and
 * both surfaces render the same array.
 */
export const BACKUP_SECRETS = [
    { key: 'BACKUP_S3_BUCKET', label: 'Bucket name' },
    { key: 'BACKUP_S3_ENDPOINT', label: 'Endpoint URL', help: 'Leave blank for AWS S3. Set it for Oracle, MinIO, Backblaze and the rest.' },
    { key: 'BACKUP_S3_REGION', label: 'Region' },
    { key: 'BACKUP_S3_ACCESS_KEY', label: 'Access key ID', secret: true },
    { key: 'BACKUP_S3_SECRET_KEY', label: 'Secret access key', secret: true },
];

export const SENTRY_SECRETS = [
    { key: 'SENTRY_DSN', label: 'Sentry DSN', secret: true, help: 'Project Settings → Client Keys (DSN).' },
];

export function buildPlan(input: PlanInput): PlanTask[] {
    const { cloud, idp, maps, wanted } = input;
    const want = (f: string) => wanted.has(f);
    const byVendor = new Map<Vendor, PlanItem[]>();
    /* Filtered on the way in rather than on the way out, so a task whose only
     * item was excluded is never assembled at all. Dropping items afterwards
     * would leave an empty vendor task with a title and a foundation walk and
     * nothing to do inside it -- which is exactly the dead end the exclusion
     * exists to remove. */
    const add = (vendor: Vendor, item: PlanItem) => {
        if (input.exclude?.has(item.id)) return;
        const list = byVendor.get(vendor) ?? [];
        list.push(item);
        byVendor.set(vendor, list);
    };

    // --- Required ---------------------------------------------------------

    add(IDP_VENDOR[idp], {
        id: 'identity',
        title: 'Staff sign-in',
        blurb: 'How your staff log in. Residents never sign in — only the people who work the reports.',
        cap: 'identity', provider: idp, required: true,
    });

    add(maps as Vendor, {
        id: 'maps',
        title: 'Maps and address lookup',
        blurb: 'The map residents drop a pin on, and the address box that finds their street.',
        cap: 'maps', provider: maps, required: true,
    });

    // --- Optional, folded into whichever console owns them ----------------

    if (want('ai')) {
        add(AI_VENDOR[input.aiProvider] ?? cloud, {
            id: 'ai',
            title: 'AI triage',
            blurb: 'Suggests a category and a department for each new report. A clerk still decides.',
            cap: 'ai', provider: input.aiProvider,
        });
    }

    if (want('translation')) {
        add(cloud, {
            id: 'translation',
            title: 'Translation',
            blurb: 'Lets residents file in their own language. Your staff carry on in English.',
            cap: 'translation', provider: cloud,
        });
    }

    if (want('kms')) {
        add(cloud, {
            id: 'kms',
            /* Key management, and only key management. The title claimed secret
             * storage too, while the item's `cap` is `kms` and its done-check
             * therefore asks only about the encryption key -- so a town could
             * tick this off with its credentials sitting in the database. The
             * secret store is a capability of its own now, with its own card and
             * its own check; this task stops speaking for it. */
            title: 'Key management',
            blurb: 'Keeps resident names, emails and phone numbers encrypted, using a key your cloud looks after.',
            cap: 'kms', provider: cloud,
        });
    }

    /* Screening and blurring, together.
     *
     * They were two sections and they should not have been. Both answer "what
     * is safe to publish", both are configured in the same place, and a clerk
     * reading two rose-coloured panels about photos three sections apart has to
     * work out that one is about words and the other about faces. */
    if (want('safety')) {
        add((input.redactionProvider as Vendor) || cloud, {
            id: 'safety',
            title: 'Screening and blurring',
            blurb: 'Screens what residents write, and blurs faces and number plates in the photos they send. Abusive text is always screened, with or without this.',
            cap: 'redaction', provider: input.redactionProvider,
            secrets: cloud === 'azure' ? [
                { key: 'AZURE_CONTENT_SAFETY_ENDPOINT', label: 'Content Safety endpoint', help: 'Keys and Endpoint blade of an Azure AI Content Safety resource.' },
                { key: 'AZURE_CONTENT_SAFETY_KEY', label: 'Content Safety key', secret: true, help: 'Either KEY 1 or KEY 2 — they are interchangeable.' },
            ] : undefined,
        });
    }

    if (want('email')) {
        add(EMAIL_VENDOR[input.emailProvider] ?? 'smtp', {
            id: 'email',
            title: 'Email notifications',
            blurb: 'Sends residents a confirmation when they file, and an update when the job is done.',
            cap: 'email', provider: input.emailProvider,
        });
    }

    if (want('sms')) {
        add(SMS_VENDOR[input.smsProvider] ?? 'twilio', {
            id: 'sms',
            title: 'Text message notifications',
            blurb: 'The same updates by text, for residents who give a mobile number. Email on its own is fine too.',
            cap: 'sms', provider: input.smsProvider,
        });
    }

    if (want('backups')) {
        add('storage', {
            id: 'backups',
            title: 'Automatic backups',
            blurb: 'A nightly copy of everything, kept somewhere other than this server.',
            secrets: BACKUP_SECRETS,
        });
    }

    if (want('errors')) {
        add('sentry', {
            id: 'errors',
            title: 'Crash reporting',
            blurb: 'Sends crash reports somewhere off this server, so they survive a restart.',
            secrets: SENTRY_SECRETS,
        });
    }

    // --- Assemble ---------------------------------------------------------

    const tasks: PlanTask[] = [];
    for (const vendor of VENDOR_ORDER) {
        const items = byVendor.get(vendor);
        if (!items?.length) continue;
        const isCloud = vendor === 'google' || vendor === 'azure' || vendor === 'aws';
        tasks.push({
            id: vendor,
            vendor,
            title: VENDOR_LABEL[vendor],
            blurb: isCloud
                ? `Everything here is in one place. Set the account up once, then add each piece while you are already signed in.`
                : items.length > 1
                    ? 'Both of these use the same login.'
                    : items[0].blurb,
            foundation: isCloud ? (vendor as Cloud) : null,
            /* Key management first inside its task (sort is stable, so nothing
             * else moves). A credential saved before the key service and the
             * secret store are reachable falls back to the encrypted database
             * -- reported, but quietly second-best -- so the guide asks for
             * the thing every later save depends on before the things that
             * depend on it. */
            items: [...items].sort((a, b) => Number(b.id === 'kms') - Number(a.id === 'kms')),
            required: items.some(i => i.required),
        });
    }

    /* The task carrying key management goes first, then whatever carries a
     * required item, then the rest.
     *
     * Required-first used to win outright, which put Auth0 sign-in ahead of
     * the cloud task on a town that keeps its secrets in that cloud -- so the
     * very first credential the guide asked for was saved before the store it
     * belongs in was reachable. The cloud task's foundation walk (project,
     * service account) is what makes the store reachable, and it travels with
     * the kms item; doing it first means everything after it lands where the
     * town said credentials go. */
    const rank = (t: PlanTask) =>
        t.items.some(i => i.id === 'kms') ? 0 : t.required ? 1 : 2;
    return tasks.sort((a, b) => rank(a) - rank(b));
}

/** The first task not yet finished, which is where the wizard should be. */
export function nextIncomplete(tasks: PlanTask[], done: (t: PlanTask) => boolean): PlanTask | undefined {
    return tasks.find(t => !done(t));
}


/**
 * What is still outstanding, split by *why*.
 *
 * The collapsed "Setup Instructions" panel gave no sign that anything the town
 * had ticked was unfinished, so a half-configured deployment looked identical
 * to a finished one until somebody opened it.
 *
 * The split is the point. "Not set up" and "not working" are different
 * problems with different fixes -- one needs a credential entered, the other
 * needs a credential replaced -- and a single "3 issues" badge that merges them
 * sends a clerk to the wrong place. There is a third case that is neither:
 * something configured that cannot be checked from here at all, which is not
 * outstanding work and is deliberately not counted.
 *
 * Pure, and fed the same tasks the wizard renders, so the badge cannot claim
 * something different from the list underneath it.
 */
export interface PlanSummary {
    notSetUp: PlanItem[];
    notWorking: PlanItem[];
    total: number;
}

export function summarise(
    tasks: PlanTask[],
    opts: {
        /** Whether this item's credentials are stored. */
        isDone: (item: PlanItem) => boolean;
        /** 'failing' when a live check failed. Anything else is not a fault:
         *  unknown means nobody has looked, and unverifiable means nobody can. */
        stateOf?: (item: PlanItem) => string | null | undefined;
    },
): PlanSummary {
    const notSetUp: PlanItem[] = [];
    const notWorking: PlanItem[] = [];
    const seen = new Set<string>();
    for (const task of tasks) {
        for (const item of task.items) {
            // Grouping by vendor can list the same capability under one task
            // only, but guard anyway -- a double count is a badge that says 4
            // above a list of 3.
            if (seen.has(item.id)) continue;
            seen.add(item.id);
            if (!opts.isDone(item)) {
                notSetUp.push(item);
            } else if (opts.stateOf?.(item) === 'failing') {
                notWorking.push(item);
            }
        }
    }
    return { notSetUp, notWorking, total: seen.size };
}

/** "Maps", "Maps and AI triage", "Maps, AI triage and 2 more". */
export function nameList(items: PlanItem[], max = 2): string {
    const titles = items.map(i => i.title);
    if (titles.length === 0) return '';
    if (titles.length === 1) return titles[0];
    if (titles.length <= max) return `${titles.slice(0, -1).join(', ')} and ${titles[titles.length - 1]}`;
    return `${titles.slice(0, max).join(', ')} and ${titles.length - max} more`;
}
