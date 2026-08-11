import React from 'react';
import { motion } from 'framer-motion';

interface CardProps {
    children: React.ReactNode;
    className?: string;
    hover?: boolean;
    onClick?: () => void;
    /** Accessible name for clickable cards whose visible content is too
     *  fragmented (badges, dates, IDs) to read well as a button label. */
    'aria-label'?: string;
}

export const Card: React.FC<CardProps> = ({
    children,
    className = '',
    hover = false,
    onClick,
    'aria-label': ariaLabel,
}) => {
    const baseStyles = 'glass-card p-6';

    // Handle keyboard Enter/Space for accessibility. Only when the card
    // itself is focused: keydown bubbles, so without the target check a
    // nested button's Enter would be swallowed and activate the card instead.
    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.target !== e.currentTarget) return;
        if (onClick && (e.key === 'Enter' || e.key === ' ')) {
            e.preventDefault();
            onClick();
        }
    };

    // ADA accessibility: a clickable card is a button to the keyboard too.
    const interactiveProps = onClick
        ? {
            onClick,
            tabIndex: 0,
            role: 'button',
            'aria-label': ariaLabel,
            onKeyDown: handleKeyDown,
        }
        : {};

    const classes = `${baseStyles} ${hover || onClick ? 'cursor-pointer ' : ''}${className}`;

    // Motion is opt-in via `hover`, never a side effect of being clickable:
    // framer-motion writes the lift/scale as an inline transform, which the
    // prefers-reduced-motion rules in index.css cannot switch off. A card
    // that only gained a click handler stays as still as it was before.
    if (hover) {
        return (
            <motion.div
                whileHover={{ scale: 1.02, y: -4 }}
                whileTap={onClick ? { scale: 0.98 } : undefined}
                className={classes}
                {...interactiveProps}
            >
                {children}
            </motion.div>
        );
    }

    return (
        <div className={classes} {...interactiveProps}>
            {children}
        </div>
    );
};

export default Card;
