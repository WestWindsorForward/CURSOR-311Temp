import { defineConfig } from 'vitest/config';

/**
 * The project had no frontend test runner at all, which left two modules
 * holding real logic completely unverified: maps/geo.ts (ray-casting, bounds,
 * line-fraction maths) and maps/popup.ts, which is the escaping boundary
 * between resident-supplied text and the DOM.
 *
 * jsdom rather than a browser because that is enough to assert what actually
 * matters — that untrusted values become text nodes and never markup.
 */
export default defineConfig({
    test: {
        environment: 'jsdom',
        include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
        /* Vitest's default is 5s, which the setup-page tests exceed whenever the
         * machine is busy: each one mounts the whole integrations page --
         * hundreds of nodes, a dozen mocked fetches, framer-motion measuring
         * keyframes -- and several of them run at once. They were passing on
         * margin, so adding any test file anywhere in the suite tipped them
         * over, and the failure reads as a broken page rather than as a slow
         * one. The page mounts take single-digit seconds when they work; a real
         * hang still fails, twenty seconds later. */
        testTimeout: 20000,
    },
});
