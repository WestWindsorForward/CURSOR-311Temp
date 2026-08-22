// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import React from 'react';
import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

/**
 * Making a card clickable must not make it move.
 *
 * Card used to route every card with an onClick through its motion branch, so
 * giving the request list a keyboard handler also gave every request card a
 * hover lift and a click squash. framer-motion writes those as inline
 * transforms, which the prefers-reduced-motion rules in index.css cannot
 * switch off -- a resident who asked their OS for less motion got more of it,
 * as a side effect of an accessibility fix. Motion is `hover`'s job now, and
 * these tests hold the two apart.
 */

// Motion components are recorded rather than animated: what matters is which
// branch Card took and which motion props it asked for, and jsdom animates
// nothing anyway.
const motionRenders: { tag: string; whileHover: unknown; whileTap: unknown }[] = [];
vi.mock('framer-motion', () => {
    const make = (tag: string) =>
        React.forwardRef<HTMLElement, Record<string, any>>((props, ref) => {
            const { whileHover, whileTap, initial, animate, exit, transition, children, ...rest } = props;
            motionRenders.push({ tag, whileHover, whileTap });
            return React.createElement(tag, { ...rest, ref, 'data-motion': 'true' }, children);
        });
    const motion = new Proxy({} as Record<string, unknown>, {
        get: (target, tag: string) => {
            if (!target[tag]) target[tag] = make(tag);
            return target[tag];
        },
    });
    return { motion, AnimatePresence: ({ children }: { children?: React.ReactNode }) => children };
});

import { Card } from './Card';

afterEach(() => {
    motionRenders.length = 0;
    cleanup();
});

describe('Card motion', () => {
    it('does not animate a card that is merely clickable', () => {
        render(<Card onClick={() => { }} aria-label="Pothole, status Open">body</Card>);
        const card = screen.getByRole('button', { name: 'Pothole, status Open' });
        expect(card.getAttribute('data-motion')).toBeNull();
        expect(motionRenders).toHaveLength(0);
    });

    it('still animates a card that asked for hover', () => {
        render(<Card hover onClick={() => { }} aria-label="Report a pothole">body</Card>);
        const card = screen.getByRole('button', { name: 'Report a pothole' });
        expect(card.getAttribute('data-motion')).toBe('true');
        expect(motionRenders).toHaveLength(1);
        expect(motionRenders[0].whileHover).toEqual({ scale: 1.02, y: -4 });
        expect(motionRenders[0].whileTap).toEqual({ scale: 0.98 });
    });

    it('renders a plain, unfocusable div when it is neither clickable nor hoverable', () => {
        const { container } = render(<Card>body</Card>);
        const card = container.firstElementChild as HTMLElement;
        expect(card.getAttribute('role')).toBeNull();
        expect(card.hasAttribute('tabindex')).toBe(false);
        expect(card.getAttribute('data-motion')).toBeNull();
        expect(card.className).not.toContain('cursor-pointer');
    });
});

describe('Card keyboard access', () => {
    it('is a focusable button that Enter and Space activate', async () => {
        const user = userEvent.setup();
        const onClick = vi.fn();
        render(<Card onClick={onClick} aria-label="Open request">body</Card>);

        await user.tab();
        const card = screen.getByRole('button', { name: 'Open request' });
        expect(document.activeElement).toBe(card);
        expect(card.tabIndex).toBe(0);

        await user.keyboard('{Enter}');
        expect(onClick).toHaveBeenCalledTimes(1);
        await user.keyboard(' ');
        expect(onClick).toHaveBeenCalledTimes(2);
    });

    it('leaves keypresses on a nested control alone', async () => {
        const user = userEvent.setup();
        const onClick = vi.fn();
        const onNested = vi.fn();
        render(
            <Card onClick={onClick} aria-label="Open request">
                <button onClick={onNested}>Share</button>
            </Card>,
        );

        screen.getByRole('button', { name: 'Share' }).focus();
        await user.keyboard('{Enter}');
        expect(onNested).toHaveBeenCalledTimes(1);
        // Card's key handler ignores events it did not receive directly, so
        // it adds no activation of its own on top of the click the nested
        // button already bubbles.
        expect(onClick.mock.calls.length).toBeLessThanOrEqual(1);
    });
});
