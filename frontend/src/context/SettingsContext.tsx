import React, { createContext, useContext, useState, useEffect } from 'react';
import { SystemSettings } from '../types';
import { api } from '../services/api';

interface SettingsContextType {
    settings: SystemSettings | null;
    isLoading: boolean;
    refreshSettings: () => Promise<void>;
}

const defaultSettings: SystemSettings = {
    id: 0,
    township_name: 'Your Municipality',
    logo_url: null,
    favicon_url: null,
    hero_text: 'How can we help?',
    primary_color: '#6366f1',
    modules: { research_portal: false, unlisted_reports: false, platform_feedback: false },
    updated_at: null,
};

const SettingsContext = createContext<SettingsContextType | undefined>(undefined);

export const SettingsProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [settings, setSettings] = useState<SystemSettings | null>(null);
    const [isLoading, setIsLoading] = useState(true);

    const refreshSettings = async () => {
        try {
            const data = await api.getSettings();
            setSettings(data);

            // Update document title
            document.title = `${data.township_name} | 311 Services`;

            // Update primary color CSS variable
            document.documentElement.style.setProperty('--primary', data.primary_color);
        } catch {
            setSettings(defaultSettings);
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => {
        refreshSettings();
    }, []);

    return (
        <SettingsContext.Provider value={{ settings, isLoading, refreshSettings }}>
            {children}
        </SettingsContext.Provider>
    );
};

export const useSettings = (): SettingsContextType => {
    const context = useContext(SettingsContext);
    if (!context) {
        throw new Error('useSettings must be used within SettingsProvider');
    }
    return context;
};

