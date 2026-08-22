import { describe, it, expect } from 'vitest';
// `?raw` rather than node:fs — the frontend tsconfig has no node types, and
// vite/client already declares this form.
import adminConsoleSource from '../pages/AdminConsole.tsx?raw';
import residentPortalSource from '../pages/ResidentPortal.tsx?raw';
import staffDashboardSource from '../pages/StaffDashboard.tsx?raw';

/**
 * Where the module is wired in, asserted against the source.
 *
 * These are three separate promises made to a town, and each is made in a
 * different file, so each is checked where it is made:
 *
 *   AdminConsole      the toggle's own description says what the module puts
 *                     into the town's records and where longer feedback lands.
 *                     A clerk answering a public-records question should not
 *                     have to read the backend to find out.
 *   ResidentPortal    the entry point is gated on the module flag, and it lives
 *                     in the footer -- not in a modal over the report form.
 *   StaffDashboard    the statistics panel is gated on the same flag, so a town
 *                     with the module off has no panel rather than an empty one.
 *
 * Source-reading rather than mounting: all three files are thousand-line pages
 * with map providers, websockets and a dozen fetches, and the thing under test
 * is one line of wiring in each.
 */

describe('the admin toggle', () => {
    const console = adminConsoleSource;
    const entry = (() => {
        const start = console.indexOf("key: 'platform_feedback' as const");
        return console.slice(start, console.indexOf('icon:', start));
    })();

    it('exists in the module list', () => {
        expect(entry).toContain('Platform Feedback');
    });

    it('says what lands in the town’s database', () => {
        const lower = entry.toLowerCase();
        expect(lower).toContain('anonymous');
        expect(lower).toContain('no comments');
        expect(lower).toMatch(/no name/);
    });

    it('says where longer feedback goes', () => {
        expect(entry.toLowerCase()).toContain('email');
    });

    it('defaults the module off in the console’s own state', () => {
        expect(console).toContain('platform_feedback: false');
    });

    it('offers the address as a setting rather than baking one in', () => {
        expect(console).toContain('platformFeedbackEmail');
        // No default address anywhere in the block that owns the field. The
        // operator's own address is a per-deployment value they type in, not
        // something the product ships pointed at.
        const block = console.slice(
            console.indexOf("mod.key === 'platform_feedback'"),
            console.indexOf("{/* Per-pack export switches"),
        );
        expect(block).toContain('platform-feedback-email');
        expect(block).toContain('value={platformFeedbackEmail}');
        // The only address-shaped string in the block is the greyed-out
        // placeholder. Anything else would be a value the product ships
        // pointed somewhere, which is how a town emails the wrong mailbox.
        const addresses = block
            .split('\n')
            .filter((line: string) => /@[a-z0-9.-]+\.(org|com|gov|net)/.test(line));
        expect(addresses).toHaveLength(1);
        expect(addresses[0]).toContain('placeholder=');
    });

    it('starts the address empty rather than prefilled', () => {
        expect(console).toContain("useState<string>('')");
    });
});

describe('the resident entry point', () => {
    const portal = residentPortalSource;

    it('is gated on the module flag', () => {
        expect(portal).toContain('enabled={settings?.modules?.platform_feedback}');
    });

    it('takes its address from settings, with no hardcoded fallback', () => {
        expect(portal).toContain('feedbackEmail={settings?.platform_feedback_email}');
    });

    it('sits in the footer and does not interrupt filing a report', () => {
        const footer = portal.slice(portal.indexOf('{/* Footer */}'));
        expect(footer).toContain('<PlatformFeedback');
        // The component is mounted exactly once, in the footer -- not over the
        // report form, and not on the success screen as a second interruption.
        expect(portal.match(/<PlatformFeedback\b/g)).toHaveLength(1);
    });
});

describe('the statistics panel', () => {
    const dashboard = staffDashboardSource;

    it('is gated on the module flag', () => {
        expect(dashboard).toContain('enabled={settings?.modules?.platform_feedback}');
    });

    it('does not call the endpoint at all when the module is off', () => {
        // The endpoint 404s by design; requesting it anyway would put a
        // "Platform feedback: Not found" line in the statistics error banner of
        // every town that left the module off.
        const effect = dashboard.slice(
            dashboard.indexOf('api.getPlatformFeedbackStatistics') - 800,
            dashboard.indexOf('api.getPlatformFeedbackStatistics'),
        );
        expect(effect).toContain('if (!settings?.modules?.platform_feedback) return;');
    });
});
