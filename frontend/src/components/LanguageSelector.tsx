import { useEffect, useRef, useState } from 'react';
import { Globe, Check, ChevronDown, Search } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { useTranslation } from '../context/TranslationContext';

// All 130+ languages supported by Google Translate
const LANGUAGES = [
    { code: 'af', name: 'Afrikaans', nativeName: 'Afrikaans' },
    { code: 'sq', name: 'Albanian', nativeName: 'Shqip' },
    { code: 'am', name: 'Amharic', nativeName: 'አማርኛ' },
    { code: 'ar', name: 'Arabic', nativeName: 'العربية' },
    { code: 'hy', name: 'Armenian', nativeName: 'Հայերեն' },
    { code: 'az', name: 'Azerbaijani', nativeName: 'Azərbaycan' },
    { code: 'eu', name: 'Basque', nativeName: 'Euskara' },
    { code: 'be', name: 'Belarusian', nativeName: 'Беларуская' },
    { code: 'bn', name: 'Bengali', nativeName: 'বাংলা' },
    { code: 'bs', name: 'Bosnian', nativeName: 'Bosanski' },
    { code: 'bg', name: 'Bulgarian', nativeName: 'Български' },
    { code: 'ca', name: 'Catalan', nativeName: 'Català' },
    { code: 'ceb', name: 'Cebuano', nativeName: 'Cebuano' },
    { code: 'ny', name: 'Chichewa', nativeName: 'Chichewa' },
    { code: 'zh', name: 'Chinese (Simplified)', nativeName: '中文(简体)' },
    { code: 'zh-TW', name: 'Chinese (Traditional)', nativeName: '中文(繁體)' },
    { code: 'co', name: 'Corsican', nativeName: 'Corsu' },
    { code: 'hr', name: 'Croatian', nativeName: 'Hrvatski' },
    { code: 'cs', name: 'Czech', nativeName: 'Čeština' },
    { code: 'da', name: 'Danish', nativeName: 'Dansk' },
    { code: 'nl', name: 'Dutch', nativeName: 'Nederlands' },
    { code: 'en', name: 'English', nativeName: 'English' },
    { code: 'eo', name: 'Esperanto', nativeName: 'Esperanto' },
    { code: 'et', name: 'Estonian', nativeName: 'Eesti' },
    { code: 'tl', name: 'Filipino (Tagalog)', nativeName: 'Filipino' },
    { code: 'fi', name: 'Finnish', nativeName: 'Suomi' },
    { code: 'fr', name: 'French', nativeName: 'Français' },
    { code: 'fy', name: 'Frisian', nativeName: 'Frysk' },
    { code: 'gl', name: 'Galician', nativeName: 'Galego' },
    { code: 'ka', name: 'Georgian', nativeName: 'ქართული' },
    { code: 'de', name: 'German', nativeName: 'Deutsch' },
    { code: 'el', name: 'Greek', nativeName: 'Ελληνικά' },
    { code: 'gu', name: 'Gujarati', nativeName: 'ગુજરાતી' },
    { code: 'ht', name: 'Haitian Creole', nativeName: 'Kreyòl Ayisyen' },
    { code: 'ha', name: 'Hausa', nativeName: 'Hausa' },
    { code: 'haw', name: 'Hawaiian', nativeName: 'ʻŌlelo Hawaiʻi' },
    { code: 'he', name: 'Hebrew', nativeName: 'עברית' },
    { code: 'hi', name: 'Hindi', nativeName: 'हिन्दी' },
    { code: 'hmn', name: 'Hmong', nativeName: 'Hmong' },
    { code: 'hu', name: 'Hungarian', nativeName: 'Magyar' },
    { code: 'is', name: 'Icelandic', nativeName: 'Íslenska' },
    { code: 'ig', name: 'Igbo', nativeName: 'Igbo' },
    { code: 'id', name: 'Indonesian', nativeName: 'Bahasa Indonesia' },
    { code: 'ga', name: 'Irish', nativeName: 'Gaeilge' },
    { code: 'it', name: 'Italian', nativeName: 'Italiano' },
    { code: 'ja', name: 'Japanese', nativeName: '日本語' },
    { code: 'jw', name: 'Javanese', nativeName: 'Basa Jawa' },
    { code: 'kn', name: 'Kannada', nativeName: 'ಕನ್ನಡ' },
    { code: 'kk', name: 'Kazakh', nativeName: 'Қазақ' },
    { code: 'km', name: 'Khmer', nativeName: 'ភាសាខ្មែរ' },
    { code: 'rw', name: 'Kinyarwanda', nativeName: 'Kinyarwanda' },
    { code: 'ko', name: 'Korean', nativeName: '한국어' },
    { code: 'ku', name: 'Kurdish (Kurmanji)', nativeName: 'Kurdî' },
    { code: 'ky', name: 'Kyrgyz', nativeName: 'Кыргызча' },
    { code: 'lo', name: 'Lao', nativeName: 'ລາວ' },
    { code: 'la', name: 'Latin', nativeName: 'Latina' },
    { code: 'lv', name: 'Latvian', nativeName: 'Latviešu' },
    { code: 'lt', name: 'Lithuanian', nativeName: 'Lietuvių' },
    { code: 'lb', name: 'Luxembourgish', nativeName: 'Lëtzebuergesch' },
    { code: 'mk', name: 'Macedonian', nativeName: 'Македонски' },
    { code: 'mg', name: 'Malagasy', nativeName: 'Malagasy' },
    { code: 'ms', name: 'Malay', nativeName: 'Bahasa Melayu' },
    { code: 'ml', name: 'Malayalam', nativeName: 'മലയാളം' },
    { code: 'mt', name: 'Maltese', nativeName: 'Malti' },
    { code: 'mi', name: 'Maori', nativeName: 'Māori' },
    { code: 'mr', name: 'Marathi', nativeName: 'मराठी' },
    { code: 'mn', name: 'Mongolian', nativeName: 'Монгол' },
    { code: 'my', name: 'Myanmar (Burmese)', nativeName: 'မြန်မာဘာသာ' },
    { code: 'ne', name: 'Nepali', nativeName: 'नेपाली' },
    { code: 'no', name: 'Norwegian', nativeName: 'Norsk' },
    { code: 'or', name: 'Odia (Oriya)', nativeName: 'ଓଡ଼ିଆ' },
    { code: 'ps', name: 'Pashto', nativeName: 'پښتو' },
    { code: 'fa', name: 'Persian', nativeName: 'فارسی' },
    { code: 'pl', name: 'Polish', nativeName: 'Polski' },
    { code: 'pt', name: 'Portuguese', nativeName: 'Português' },
    { code: 'pa', name: 'Punjabi', nativeName: 'ਪੰਜਾਬੀ' },
    { code: 'ro', name: 'Romanian', nativeName: 'Română' },
    { code: 'ru', name: 'Russian', nativeName: 'Русский' },
    { code: 'sm', name: 'Samoan', nativeName: 'Gagana Samoa' },
    { code: 'gd', name: 'Scots Gaelic', nativeName: 'Gàidhlig' },
    { code: 'sr', name: 'Serbian', nativeName: 'Српски' },
    { code: 'st', name: 'Sesotho', nativeName: 'Sesotho' },
    { code: 'sn', name: 'Shona', nativeName: 'Shona' },
    { code: 'sd', name: 'Sindhi', nativeName: 'سنڌي' },
    { code: 'si', name: 'Sinhala', nativeName: 'සිංහල' },
    { code: 'sk', name: 'Slovak', nativeName: 'Slovenčina' },
    { code: 'sl', name: 'Slovenian', nativeName: 'Slovenščina' },
    { code: 'so', name: 'Somali', nativeName: 'Soomaali' },
    { code: 'es', name: 'Spanish', nativeName: 'Español' },
    { code: 'su', name: 'Sundanese', nativeName: 'Basa Sunda' },
    { code: 'sw', name: 'Swahili', nativeName: 'Kiswahili' },
    { code: 'sv', name: 'Swedish', nativeName: 'Svenska' },
    { code: 'tg', name: 'Tajik', nativeName: 'Тоҷикӣ' },
    { code: 'ta', name: 'Tamil', nativeName: 'தமிழ்' },
    { code: 'tt', name: 'Tatar', nativeName: 'Татар' },
    { code: 'te', name: 'Telugu', nativeName: 'తెలుగు' },
    { code: 'th', name: 'Thai', nativeName: 'ไทย' },
    { code: 'tr', name: 'Turkish', nativeName: 'Türkçe' },
    { code: 'tk', name: 'Turkmen', nativeName: 'Türkmen' },
    { code: 'uk', name: 'Ukrainian', nativeName: 'Українська' },
    { code: 'ur', name: 'Urdu', nativeName: 'اردو' },
    { code: 'ug', name: 'Uyghur', nativeName: 'ئۇيغۇرچە' },
    { code: 'uz', name: 'Uzbek', nativeName: 'Oʻzbek' },
    { code: 'vi', name: 'Vietnamese', nativeName: 'Tiếng Việt' },
    { code: 'cy', name: 'Welsh', nativeName: 'Cymraeg' },
    { code: 'xh', name: 'Xhosa', nativeName: 'isiXhosa' },
    { code: 'yi', name: 'Yiddish', nativeName: 'ייִדיש' },
    { code: 'yo', name: 'Yoruba', nativeName: 'Yorùbá' },
    { code: 'zu', name: 'Zulu', nativeName: 'isiZulu' },
];

export default function LanguageSelector() {
    const [isOpen, setIsOpen] = useState(false);
    const [searchQuery, setSearchQuery] = useState('');
    const { language, setLanguage } = useTranslation();

    const currentLanguage = LANGUAGES.find(lang => lang.code === language) || LANGUAGES.find(l => l.code === 'en')!;

    // Filter languages based on search
    const filteredLanguages = LANGUAGES.filter(lang =>
        lang.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        lang.nativeName.toLowerCase().includes(searchQuery.toLowerCase()) ||
        lang.code.toLowerCase().includes(searchQuery.toLowerCase())
    );

    // The dropdown could only be dismissed by clicking the backdrop, which
    // leaves a keyboard user inside a list of 100+ languages with no way out
    // but tabbing through all of them. Escape closes it and hands focus back
    // to the trigger, the same contract as every other menu in the app.
    const triggerRef = useRef<HTMLButtonElement>(null);
    useEffect(() => {
        if (!isOpen) return;
        const onKeyDown = (e: KeyboardEvent) => {
            if (e.key !== 'Escape') return;
            setIsOpen(false);
            setSearchQuery('');
            triggerRef.current?.focus();
        };
        window.addEventListener('keydown', onKeyDown);
        return () => window.removeEventListener('keydown', onKeyDown);
    }, [isOpen]);

    const changeLanguage = (code: string) => {
        // Picking a language unmounts the button that was focused, so focus
        // goes back to the trigger -- the same place Escape leaves it, rather
        // than dropping to <body> and restarting Tab from the top of the page.
        if (code === language) {
            setIsOpen(false);
            setSearchQuery('');
            triggerRef.current?.focus();
            return;
        }
        setLanguage(code);
        setIsOpen(false);
        setSearchQuery('');
        triggerRef.current?.focus();
        // Refresh the page to apply translations cleanly
        setTimeout(() => {
            window.location.reload();
        }, 100);
    };

    return (
        <div className="relative">
            <button
                ref={triggerRef}
                onClick={() => setIsOpen(!isOpen)}
                className="flex items-center gap-1.5 sm:gap-2 px-2 sm:px-3 py-1.5 sm:py-2 rounded-lg bg-slate-800/80 hover:bg-slate-700/80 border border-white/10 transition-all text-white shadow-lg"
                aria-label={`Select language, currently ${currentLanguage.name}`}
                // Not "listbox": the popup is a search field plus ordinary
                // buttons, and claiming a listbox that has no options makes a
                // screen reader announce a control the user cannot find. The
                // honest claim is that a popup opens; the buttons inside are
                // announced as buttons, which is what they are, and the
                // current language carries aria-current.
                aria-haspopup="true"
                aria-expanded={isOpen}
            >
                <Globe className="w-4 h-4 flex-shrink-0" />
                <span className="hidden sm:inline text-sm font-medium">{currentLanguage.nativeName}</span>
                <span className="sm:hidden text-xs font-medium">{currentLanguage.code.toUpperCase()}</span>
                <ChevronDown className={`w-3 h-3 sm:w-4 sm:h-4 flex-shrink-0 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
            </button>

            <AnimatePresence>
                {isOpen && (
                    <>
                        {/* Backdrop */}
                        <div
                            className="fixed inset-0 z-[9998]"
                            aria-hidden="true"
                            onClick={() => {
                                setIsOpen(false);
                                setSearchQuery('');
                            }}
                        />

                        {/* Dropdown */}
                        <motion.div
                            initial={{ opacity: 0, y: -10, scale: 0.95 }}
                            animate={{ opacity: 1, y: 0, scale: 1 }}
                            exit={{ opacity: 0, y: -10, scale: 0.95 }}
                            transition={{ duration: 0.15 }}
                            className="fixed sm:absolute inset-x-2 sm:inset-x-auto sm:right-0 top-14 sm:top-auto sm:mt-2 sm:w-80 rounded-xl bg-slate-800 border border-white/20 shadow-2xl z-[9999]"
                            style={{
                                boxShadow: '0 20px 50px rgba(0,0,0,0.5), 0 0 20px rgba(0,0,0,0.3)'
                            }}
                        >
                            {/* Search Header */}
                            <div className="p-3 border-b border-white/10 sticky top-0 bg-slate-800 rounded-t-xl">
                                <div className="relative">
                                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-white/40" />
                                    <input
                                        type="text"
                                        aria-label="Search languages"
                                        placeholder="Search languages..."
                                        value={searchQuery}
                                        onChange={(e) => setSearchQuery(e.target.value)}
                                        className="w-full pl-10 pr-4 py-2 bg-white/5 border border-white/10 rounded-lg text-white text-sm placeholder-white/40 focus:outline-none focus:ring-2 focus:ring-primary-500/50 focus:border-primary-500/50"
                                        onClick={(e) => e.stopPropagation()}
                                    />
                                </div>
                                <div className="mt-2 text-xs text-white/40">
                                    {filteredLanguages.length} of {LANGUAGES.length} languages
                                </div>
                            </div>

                            {/* Language List */}
                            <div className="max-h-[60vh] sm:max-h-[400px] overflow-y-auto p-2">
                                {filteredLanguages.length === 0 ? (
                                    <div className="text-center py-8 text-white/40 text-sm">
                                        No languages found
                                    </div>
                                ) : (
                                    filteredLanguages.map((lang) => (
                                        <button
                                            key={lang.code}
                                            onClick={() => changeLanguage(lang.code)}
                                            // The check mark is the only sighted
                                            // marker of the active language.
                                            aria-current={language === lang.code ? 'true' : undefined}
                                            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all text-left ${language === lang.code
                                                ? 'bg-primary-500/30 text-white border border-primary-400/30'
                                                : 'text-white/80 hover:bg-white/10 hover:text-white'
                                                }`}
                                        >
                                            <div className="flex-1 min-w-0">
                                                <div className="font-medium text-sm truncate">{lang.nativeName}</div>
                                                {lang.nativeName !== lang.name && (
                                                    <div className="text-xs text-white/50 truncate">{lang.name}</div>
                                                )}
                                            </div>
                                            {language === lang.code && (
                                                <Check className="w-4 h-4 text-primary-400 flex-shrink-0" />
                                            )}
                                        </button>
                                    ))
                                )}
                            </div>
                        </motion.div>
                    </>
                )}
            </AnimatePresence>
        </div>
    );
}
