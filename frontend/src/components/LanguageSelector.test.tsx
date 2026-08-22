// @vitest-environment jsdom
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

/**
 * The language menu is the first control a resident who does not read English
 * uses, and it is a hundred-odd entries deep. Escape already handed focus back
 * to the trigger; picking a language dropped it to <body>, so the next Tab
 * restarted from the top of the page -- in a language they had just changed.
 */

let currentLanguage = 'en';
const setLanguage = vi.fn((code: string) => { currentLanguage = code; });
vi.mock('../context/TranslationContext', () => ({
    useTranslation: () => ({ language: currentLanguage, setLanguage }),
}));

import LanguageSelector from './LanguageSelector';

beforeEach(() => {
    currentLanguage = 'en';
    setLanguage.mockClear();
    // A language change reloads the page 100ms later; jsdom cannot navigate.
    Object.defineProperty(window, 'location', {
        configurable: true,
        value: { ...window.location, reload: vi.fn() },
    });
});
afterEach(cleanup);

const openMenu = async (user: ReturnType<typeof userEvent.setup>) => {
    const trigger = screen.getByRole('button', { name: /select language/i });
    await user.click(trigger);
    return trigger;
};

describe('LanguageSelector', () => {
    it('hands focus back to the trigger after a language is picked', async () => {
        const user = userEvent.setup();
        render(<LanguageSelector />);
        const trigger = await openMenu(user);

        await user.click(screen.getByRole('button', { name: /Español/ }));

        expect(setLanguage).toHaveBeenCalledWith('es');
        expect(document.activeElement).toBe(trigger);
        expect(trigger.getAttribute('aria-expanded')).toBe('false');
    });

    it('hands focus back even when the picked language is the current one', async () => {
        const user = userEvent.setup();
        render(<LanguageSelector />);
        const trigger = await openMenu(user);

        await user.click(screen.getByRole('button', { name: /^English$/ }));

        expect(setLanguage).not.toHaveBeenCalled();
        expect(document.activeElement).toBe(trigger);
    });

    it('still hands focus back on Escape', async () => {
        const user = userEvent.setup();
        render(<LanguageSelector />);
        const trigger = await openMenu(user);
        screen.getByRole('textbox', { name: /search languages/i }).focus();

        await user.keyboard('{Escape}');

        expect(document.activeElement).toBe(trigger);
        expect(trigger.getAttribute('aria-expanded')).toBe('false');
    });

    it('claims only the popup it actually has, and marks the current language', async () => {
        // The popup is a search box plus ordinary buttons, so announcing a
        // listbox promised options that a screen reader would then fail to
        // find. aria-current is what carries the state the check mark shows.
        const user = userEvent.setup();
        render(<LanguageSelector />);
        const trigger = screen.getByRole('button', { name: /select language/i });
        expect(trigger.getAttribute('aria-haspopup')).toBe('true');
        expect(screen.queryByRole('listbox')).toBeNull();

        await user.click(trigger);
        expect(screen.queryByRole('option')).toBeNull();

        const english = screen.getByRole('button', { name: /^English$/ });
        expect(english.getAttribute('aria-current')).toBe('true');
        const spanish = screen.getByRole('button', { name: /Español/ });
        expect(spanish.hasAttribute('aria-current')).toBe(false);
    });

    it('filters the list by the search box without losing the current marker', async () => {
        const user = userEvent.setup();
        render(<LanguageSelector />);
        await openMenu(user);

        await user.type(screen.getByRole('textbox', { name: /search languages/i }), 'span');
        const results = screen.getAllByRole('button', { name: /Español/ });
        expect(results).toHaveLength(1);
        expect(within(results[0]).getByText('Spanish')).toBeTruthy();
    });
});
