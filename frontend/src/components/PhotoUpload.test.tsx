// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import React from 'react';
import { render, screen, cleanup } from '@testing-library/react';
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
