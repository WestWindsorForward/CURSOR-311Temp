// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import React from 'react';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import PhotoUpload from './PhotoUpload';

/**
 * The keyboard-only review found this control invisible to Tab: the picker
 * was a styled <label> over a display:none input, so a keyboard user went
 * from the map straight to the contact fields with no way to attach a photo.
 * These tests pin down the repaired contract -- the trigger is a real button
 * that Tab reaches, Enter/Space opens the picker, and a screen reader hears
 * a name plus the "optional" hint -- so a refactor back to a click-only
 * dropzone fails loudly.
 */

afterEach(cleanup);

const noop = () => { };

/**
 * Deliver a multi-file selection the way the real flow does: the resident
 * activates the trigger, the trigger clicks the hidden input for them, and
 * focus is still on the trigger when the files arrive. user.upload() would
 * focus the input element itself, which no resident ever does.
 */
const chooseFiles = (input: HTMLInputElement, files: File[]) => {
    Object.defineProperty(input, 'files', { value: files, configurable: true, writable: true });
    fireEvent.change(input);
};

describe('PhotoUpload keyboard access', () => {
    it('puts the add-photos trigger in the tab order', async () => {
        const user = userEvent.setup();
        render(<PhotoUpload previewUrls={[]} onAdd={noop} onRemove={noop} />);

        await user.tab();
        expect(document.activeElement).toBe(screen.getByRole('button', { name: /add photos/i }));
    });

    it('opens the file picker on Enter and on Space', async () => {
        const user = userEvent.setup();
        const { container } = render(<PhotoUpload previewUrls={[]} onAdd={noop} onRemove={noop} />);

        const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
        const openPicker = vi.spyOn(input, 'click');

        await user.tab();
        await user.keyboard('{Enter}');
        expect(openPicker).toHaveBeenCalledTimes(1);

        await user.keyboard(' ');
        expect(openPicker).toHaveBeenCalledTimes(2);
    });

    it('keeps the hidden file input itself out of the tab order', () => {
        const { container } = render(<PhotoUpload previewUrls={[]} onAdd={noop} onRemove={noop} />);
        const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
        expect(input.tabIndex).toBe(-1);
        expect(input.getAttribute('aria-hidden')).toBe('true');
    });

    it('passes chosen files up and clears the input for re-selection', async () => {
        const user = userEvent.setup();
        const onAdd = vi.fn();
        const { container } = render(<PhotoUpload previewUrls={[]} onAdd={onAdd} onRemove={noop} />);

        const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
        const file = new File(['x'], 'pothole.png', { type: 'image/png' });
        await user.upload(input, file);

        expect(onAdd).toHaveBeenCalledTimes(1);
        expect(onAdd.mock.calls[0][0][0]).toBe(file);
        expect(input.value).toBe('');
    });

    it('announces the optional hint via aria-describedby', () => {
        render(<PhotoUpload previewUrls={[]} onAdd={noop} onRemove={noop} />);
        const trigger = screen.getByRole('button', { name: /add photos/i });
        const hintId = trigger.getAttribute('aria-describedby')!;
        expect(hintId).toBeTruthy();
        expect(document.getElementById(hintId)!.textContent).toMatch(/optional/i);
    });

    it('makes remove buttons reachable and hides the trigger at the photo cap', () => {
        render(
            <PhotoUpload
                previewUrls={['data:1', 'data:2', 'data:3']}
                onAdd={noop}
                onRemove={noop}
            />,
        );
        expect(screen.queryByRole('button', { name: /add photos/i })).toBeNull();
        expect(screen.getAllByRole('button', { name: /remove photo/i })).toHaveLength(3);
    });

    it('removes the photo the focused button names', async () => {
        const user = userEvent.setup();
        const onRemove = vi.fn();
        render(<PhotoUpload previewUrls={['data:1', 'data:2']} onAdd={noop} onRemove={onRemove} />);

        screen.getByRole('button', { name: 'Remove photo 2' }).focus();
        await user.keyboard('{Enter}');
        expect(onRemove).toHaveBeenCalledWith(1);
    });

    it('keeps focus in the control when the focused remove button unmounts', async () => {
        // Stateful harness: the remove button that holds focus disappears
        // with its photo, and focus must land on a surviving control rather
        // than falling to <body>.
        const user = userEvent.setup();
        function Harness() {
            const [urls, setUrls] = React.useState(['data:1', 'data:2']);
            return (
                <PhotoUpload
                    previewUrls={urls}
                    onAdd={noop}
                    onRemove={(i) => setUrls(prev => prev.filter((_, j) => j !== i))}
                />
            );
        }
        render(<Harness />);

        screen.getByRole('button', { name: 'Remove photo 2' }).focus();
        await user.keyboard('{Enter}');

        expect(document.activeElement).not.toBe(document.body);
        expect(document.activeElement).toBe(screen.getByRole('button', { name: /add photos/i }));
    });

    it('lands focus on a surviving control when a multi-file selection fills the cap', async () => {
        // The repro: Tab to "Add Photos", Enter, pick three images at once.
        // Each preview arrives from its own FileReader callback, so the count
        // climbs 0 -> 1 -> 2 -> 3 in separate renders and the trigger unmounts
        // only on the last one -- focus must not be spent on the first.
        const user = userEvent.setup();
        function Harness() {
            const [urls, setUrls] = React.useState<string[]>([]);
            return (
                <PhotoUpload
                    previewUrls={urls}
                    onAdd={(files) => {
                        Array.from(files).forEach((file, i) => {
                            // One state update per file, each in its own task
                            // and so its own render, the way separate
                            // FileReader.onloadend callbacks deliver them.
                            setTimeout(() => setUrls(prev => [...prev, `data:${file.name}-${i}`]), (i + 1) * 5);
                        });
                    }}
                    onRemove={(i) => setUrls(prev => prev.filter((_, j) => j !== i))}
                />
            );
        }
        const { container } = render(<Harness />);

        const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
        await user.tab();
        expect(document.activeElement).toBe(screen.getByRole('button', { name: /add photos/i }));
        chooseFiles(input, [
            new File(['a'], 'a.png', { type: 'image/png' }),
            new File(['b'], 'b.png', { type: 'image/png' }),
            new File(['c'], 'c.png', { type: 'image/png' }),
        ]);

        await screen.findByRole('button', { name: 'Remove photo 3' });
        expect(screen.queryByRole('button', { name: /add photos/i })).toBeNull();
        expect(document.activeElement).not.toBe(document.body);
        expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Remove photo 3' }));
    });

    it('leaves focus on the trigger when a multi-file selection stays under the cap', async () => {
        const user = userEvent.setup();
        function Harness() {
            const [urls, setUrls] = React.useState<string[]>([]);
            return (
                <PhotoUpload
                    previewUrls={urls}
                    onAdd={(files) => {
                        Array.from(files).forEach((file, i) => {
                            setTimeout(() => setUrls(prev => [...prev, `data:${file.name}-${i}`]), (i + 1) * 5);
                        });
                    }}
                    onRemove={noop}
                />
            );
        }
        const { container } = render(<Harness />);

        const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
        await user.tab();
        expect(document.activeElement).toBe(screen.getByRole('button', { name: /add photos/i }));
        chooseFiles(input, [
            new File(['a'], 'a.png', { type: 'image/png' }),
            new File(['b'], 'b.png', { type: 'image/png' }),
        ]);

        await screen.findByRole('button', { name: 'Remove photo 2' });
        expect(document.activeElement).toBe(screen.getByRole('button', { name: /add photos/i }));
    });

    it('leaves the control unchanged when the host is not screening', () => {
        // `statuses` is optional, and a host that does not pass it must get
        // exactly the pre-screening control back -- no badges, no extra text in
        // the image names, nothing new in the live region.
        render(<PhotoUpload previewUrls={['data:1']} onAdd={noop} onRemove={noop} />);
        expect(screen.getByRole('img', { name: 'Photo 1' })).toBeTruthy();
        expect(screen.getByRole('status').textContent).toBe('1 of 3 photos attached.');
    });

    it('announces the attached-photo count, and the cap, via a live region', () => {
        const { rerender } = render(<PhotoUpload previewUrls={['data:1']} onAdd={noop} onRemove={noop} />);
        const status = screen.getByRole('status');
        expect(status.getAttribute('aria-live')).toBe('polite');
        expect(status.textContent).toMatch(/1 of 3 photos attached/);

        rerender(<PhotoUpload previewUrls={['data:1', 'data:2', 'data:3']} onAdd={noop} onRemove={noop} />);
        expect(status.textContent).toMatch(/3 of 3 photos attached/);
        expect(status.textContent).toMatch(/maximum reached/i);
    });
});

/**
 * Photos are screened -- moderated and blurred -- while the resident is still
 * filling in the rest of the form, so this control now reports where each one
 * has got to. The wait it replaces was a single generic spinner on the Submit
 * button, which said nothing about which photo, how far along, or that one of
 * them had come back refused.
 *
 * None of that is worth anything if it is only visible, so these tests are
 * mostly about what a screen reader hears -- an NVDA audit is the audience.
 */
describe('PhotoUpload screening status', () => {
    const withStatus = (statuses: any[], urls = statuses.map((_, i) => `data:${i}`)) =>
        render(
            <PhotoUpload previewUrls={urls} statuses={statuses} onAdd={noop} onRemove={noop} />,
        );

    it('names the state of each photo in the live region', () => {
        withStatus([{ state: 'checking' }, { state: 'ready' }]);
        const status = screen.getByRole('status');
        expect(status.getAttribute('aria-live')).toBe('polite');
        expect(status.textContent).toMatch(/Photo 1: Checking your photo/);
        expect(status.textContent).toMatch(/Photo 2: Ready to send/);
    });

    it('keeps exactly one live region so the announcements cannot collide', () => {
        // Two polite regions updating in the same tick is how a screen reader
        // user ends up hearing neither. The photo count and the per-photo
        // states share one region for that reason.
        withStatus([{ state: 'ready' }]);
        expect(screen.getAllByRole('status')).toHaveLength(1);
        expect(screen.getByRole('status').textContent).toMatch(/1 of 3 photos attached/);
    });

    it('says a blocked photo cannot be used, and why', () => {
        // The whole UX argument for screening early: the refusal arrives while
        // the resident is still looking at the photo they picked, not after
        // they have filled in a form and pressed Submit.
        withStatus([{ state: 'blocked', message: "This photo can't be used. Please choose another." }]);
        expect(screen.getByRole('status').textContent)
            .toMatch(/Photo 1: This photo can't be used/);
    });

    it('says a photo that could not be checked is still attached', () => {
        // The failure case must not read as "lost". It is attached; it is
        // waiting on a person.
        withStatus([{ state: 'review' }]);
        expect(screen.getByRole('status').textContent)
            .toMatch(/Photo 1: A staff member will review this photo/);
    });

    it('carries the state on the image name too', () => {
        // The live region announces changes; a resident who arrives at the
        // thumbnail later, by arrowing through, gets it from the image itself.
        withStatus([{ state: 'checking' }]);
        expect(screen.getByRole('img', { name: /Photo 1, Checking your photo/ })).toBeTruthy();
    });

    it('does not announce the visible badge twice', () => {
        // The badge duplicates what the live region already said, so it is
        // hidden from the accessibility tree. Without this every state change
        // is read out twice.
        const { container } = withStatus([{ state: 'ready' }]);
        const badge = container.querySelector('[aria-hidden="true"].absolute');
        expect(badge).toBeTruthy();
        expect(badge!.textContent).toMatch(/Ready to send/);
    });

    it('still lets a blocked photo be removed', () => {
        // A photo the resident cannot use and cannot get rid of is a dead end
        // on a form they are trying to finish.
        withStatus([{ state: 'blocked' }]);
        expect(screen.getByRole('button', { name: 'Remove photo 1' })).toBeTruthy();
    });

    it('keeps the trigger and its focus handling with statuses present', async () => {
        // The screening props must not disturb the keyboard repair: the badge
        // is not focusable and does not get between the photo's remove button
        // and the trigger, so Tab still walks remove-then-add.
        const user = userEvent.setup();
        withStatus([{ state: 'ready' }], ['data:1']);
        await user.tab();
        expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Remove photo 1' }));
        await user.tab();
        expect(document.activeElement).toBe(screen.getByRole('button', { name: /add photos/i }));
    });

    it('ignores a status for a photo that is no longer attached', () => {
        // A screening result can land after its photo was removed. The status
        // array is trimmed to the photos that exist rather than announcing a
        // phantom fourth photo.
        render(
            <PhotoUpload
                previewUrls={['data:1']}
                statuses={[{ state: 'ready' }, { state: 'checking' }]}
                onAdd={noop}
                onRemove={noop}
            />,
        );
        expect(screen.getByRole('status').textContent).not.toMatch(/Photo 2/);
    });
});
