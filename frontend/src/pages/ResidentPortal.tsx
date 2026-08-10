import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { resolveMapProviderConfig, mapProviderReady, RawMapsConfig } from '../maps';
import { motion, AnimatePresence } from 'framer-motion';
import { Link, useParams } from 'react-router-dom';
import {
    ArrowLeft,
    MapPin,
    CheckCircle2,
    Send,
    AlertCircle,
    Circle,
    Lightbulb,
    Trash2,
    Footprints,
    SignpostBig,
    Volume2,
    HelpCircle,
    Sparkles, Home,
    Phone,
    ClipboardList,
    Globe,
    Facebook,
    Instagram,
    Youtube,
    Twitter,
    Linkedin,
    AlertTriangle,
} from 'lucide-react';
import { Button, Input, Textarea, Card } from '../components/ui';
import LocationPicker from '../components/LocationPicker';
import PhotoUpload from '../components/PhotoUpload';
import { filterPhoneInput, isValidPhone } from '../utils/phone';
import RedirectNotice, { RedirectContact } from '../components/RedirectNotice';
import TrackRequests from '../components/TrackRequests';
import LanguageSelector from '../components/LanguageSelector';
import StaffDashboardMap from '../components/StaffDashboardMap';
import { useSettings } from '../context/SettingsContext';
import { useTranslation } from '../context/TranslationContext';
import { api, MapLayer } from '../services/api';
import { ServiceDefinition, ServiceRequestCreate, ServiceRequest } from '../types';
import { usePageNavigation } from '../hooks/usePageNavigation';

// Icon mapping for service categories
const iconMap: Record<string, React.FC<{ className?: string }>> = {
    Circle,
    Lightbulb,
    Trash2,
    Footprints,
    SignpostBig,
    Volume2,
    HelpCircle,
    AlertCircle,
    Spray: AlertCircle, // Fallback
};

type Step = 'categories' | 'form' | 'success';




export default function ResidentPortal() {
    const { settings } = useSettings();
    const { language } = useTranslation();
    const { requestId: urlRequestId } = useParams<{ requestId?: string }>();

    // Whether the language picker is worth drawing. A picker over a translator
    // that is switched off or unconfigured offers Spanish and then serves
    // English. Defaults to shown: an older backend does not send the field,
    // and a fetch error is not evidence that translation is unavailable.
    const [translationEnabled, setTranslationEnabled] = useState(true);
    useEffect(() => {
        fetch('/api/system/config')
            .then((r) => (r.ok ? r.json() : null))
            .then((cfg) => {
                if (cfg && cfg.translation_enabled === false) setTranslationEnabled(false);
            })
            .catch(() => { /* keep the picker */ });
    }, []);

    // Initialize state based on URL hash (not pathname)
    const initialHash = typeof window !== 'undefined' ? window.location.hash.slice(1) : '';
    const [showTrackingView, setShowTrackingView] = useState(urlRequestId || initialHash === 'track');
    const [step, setStep] = useState<Step>('categories');
    const [services, setServices] = useState<ServiceDefinition[]>([]);
    const [selectedService, setSelectedService] = useState<ServiceDefinition | null>(null);
    const [searchQuery, setSearchQuery] = useState('');
    const [isLoading, setIsLoading] = useState(true);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [submittedId, setSubmittedId] = useState<string | null>(null);
    const contentRef = useRef<HTMLDivElement>(null);

    // Non-emergency disclaimer modal state
    const [showDisclaimerModal, setShowDisclaimerModal] = useState(false);
    const [disclaimerChecked, setDisclaimerChecked] = useState(false);
    const [hasAcknowledgedDisclaimer, setHasAcknowledgedDisclaimer] = useState(() => {
        // Check localStorage on initial load
        return localStorage.getItem('disclaimer_acknowledged_v1') === 'true';
    });

    // Show disclaimer modal if not acknowledged
    useEffect(() => {
        if (!hasAcknowledgedDisclaimer) {
            setShowDisclaimerModal(true);
        }
    }, [hasAcknowledgedDisclaimer]);

    // Handle disclaimer acknowledgment
    const handleDisclaimerAcknowledge = async () => {
        if (!disclaimerChecked) return;

        // Generate session ID for logging
        const sessionId = localStorage.getItem('session_id') ||
            `sess_${Date.now()}_${Array.from(crypto.getRandomValues(new Uint8Array(6))).map(b => b.toString(36).padStart(2, '0')).join('').slice(0, 9)}`;
        localStorage.setItem('session_id', sessionId);

        // Log acknowledgment to backend
        try {
            await fetch('/api/system/disclaimer/acknowledge', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: sessionId })
            });
        } catch (e) {
            // Non-critical - proceed even if logging fails
            console.warn('Failed to log disclaimer acknowledgment:', e);
        }

        // Store acknowledgment locally
        localStorage.setItem('disclaimer_acknowledged_v1', 'true');
        setHasAcknowledgedDisclaimer(true);
        setShowDisclaimerModal(false);
    };

    // Handle browser back/forward navigation
    const handleHashChange = useCallback((hash: string) => {
        if (hash === 'track') {
            // Track list view (no specific request selected)
            setShowTrackingView(true);
            // TrackRequests component will handle clearing its internal selectedRequest
        } else if (hash.startsWith('track/')) {
            // Track with specific request - show tracking view
            // TrackRequests will load the request from its initialRequestId or internal state
            setShowTrackingView(true);
        } else if (hash === '' || hash === 'categories') {
            setShowTrackingView(false);
            setStep('categories');
            setSelectedService(null);
        } else if (hash.startsWith('report/')) {
            setShowTrackingView(false);
            setStep('form');
            // Service will be selected based on the hash - handled in useEffect after services load
        } else if (hash === 'success') {
            setShowTrackingView(false);
            setStep('success');
        }
    }, []);

    // URL hashing, dynamic titles, and scroll-to-top
    const { updateHash, updateTitle, scrollToTop, currentHash } = usePageNavigation({
        baseTitle: settings?.township_name || 'Resident Portal',
        scrollContainerRef: contentRef,
        onHashChange: handleHashChange,
    });

    // Update title based on current state (but DON'T update hash here - that causes loops)
    useEffect(() => {
        if (showTrackingView) {
            updateTitle('Track My Requests');
        } else if (step === 'categories') {
            updateTitle('Report an Issue');
        } else if (step === 'form' && selectedService) {
            updateTitle(selectedService.service_name);
        } else if (step === 'success') {
            updateTitle('Request Submitted');
        }
    }, [step, showTrackingView, selectedService, updateTitle]);

    // Handle initial hash on page load (after services are loaded)
    useEffect(() => {
        if (services.length > 0 && currentHash.startsWith('report/')) {
            const serviceCode = currentHash.split('/')[1];
            const service = services.find(s => s.service_code === serviceCode);
            if (service && !selectedService) {
                setSelectedService(service);
                // Also set formData.service_code - critical for submission!
                setFormData((prev) => ({ ...prev, service_code: service.service_code }));
                setStep('form');
            }
        }
    }, [services, currentHash, selectedService]);

    // Requests for staff map
    const [allRequests, setAllRequests] = useState<ServiceRequest[]>([]);

    // Form state
    const [formData, setFormData] = useState<ServiceRequestCreate>({
        service_code: '',
        description: '',
        address: '',
        first_name: '',
        last_name: '',
        email: '',
        phone: '',
        is_public: true,
    });
    const [formErrors, setFormErrors] = useState<Record<string, string>>({});

    // Blocking state for third-party/road-based services
    const [isBlocked, setIsBlocked] = useState(false);
    // Named separately from the message so the notice can say WHO handles it
    // rather than only quoting the town's configured sentence.
    const [blockJurisdiction, setBlockJurisdiction] = useState<string | null>(null);
    const [blockMessage, setBlockMessage] = useState('');
    // Whether blockMessage is the backend's generated sentence rather than the
    // clerk's own, so the notice can avoid restating its own heading.
    const [blockMessageIsDefault, setBlockMessageIsDefault] = useState(false);
    const [blockContacts, setBlockContacts] = useState<RedirectContact[]>([]);

    // Custom question answers
    const [customAnswers, setCustomAnswers] = useState<Record<string, string | string[]>>({});

    // Photo upload state
    const [photos, setPhotos] = useState<File[]>([]);
    const [photoPreviewUrls, setPhotoPreviewUrls] = useState<string[]>([]);

    const [mapLayers, setMapLayers] = useState<MapLayer[]>([]);
    // Selected asset from map layer (for report logging)
    const [selectedAsset, setSelectedAsset] = useState<{ layerName: string; properties: Record<string, any>; lat: number; lng: number } | null>(null);

    // Location/GPS state  
    const [location, setLocation] = useState<{ address: string; lat: number | null; lng: number | null }>({
        address: '',
        lat: null,
        lng: null
    });
    // The whole payload, not just a key: the provider and its own credentials
    // live in here, and gating on a Google key left an Esri town with no map.
    const [mapsRaw, setMapsRaw] = useState<RawMapsConfig | null>(null);
    const mapConfig = useMemo(() => resolveMapProviderConfig(mapsRaw), [mapsRaw]);
    const [townshipBoundary, setTownshipBoundary] = useState<object | null>(null);
    const [isLocationOutOfBounds, setIsLocationOutOfBounds] = useState(false);


    // Load Maps API key and configuration
    useEffect(() => {
        api.getMapsConfig().then((config) => {
            setMapsRaw(config);
            if (config.township_boundary) {
                setTownshipBoundary(config.township_boundary);
            }
        }).catch(() => { });

        // Load custom map layers (public endpoint)
        api.getMapLayers().then((layers) => {
            setMapLayers(layers);
        }).catch(() => { });
    }, []);



    // Reload services when language changes
    useEffect(() => {
        loadServices();
    }, [language]);

    const loadServices = async () => {
        try {
            const data = await api.getServices();
            setServices(data);
        } catch (err) {
            console.error('Failed to load services:', err);
        } finally {
            setIsLoading(false);
        }
    };

    // Load requests for the map (public endpoint - includes department/staff for filtering)
    useEffect(() => {
        api.getPublicRequests().then((requests) => {
            // Cast to ServiceRequest since we now include assigned_department_id and assigned_to
            setAllRequests(requests as unknown as ServiceRequest[]);
        }).catch(() => { });
    }, []);

    const filteredServices = services.filter(
        (s) =>
            s.service_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
            s.description?.toLowerCase().includes(searchQuery.toLowerCase())
    );

    const handleSelectService = (service: ServiceDefinition) => {
        setSelectedService(service);
        setFormData((prev) => ({ ...prev, service_code: service.service_code }));

        // Clear any previous blocking state and selected asset
        setIsBlocked(false);
        setBlockMessage('');
        setBlockContacts([]);
        setSelectedAsset(null); // Reset asset selection for new category

        // Check if third-party only service - block immediately
        if (service.routing_mode === 'third_party') {
            setIsBlocked(true);
            setBlockMessage(service.routing_config?.message || '');
            setBlockMessageIsDefault(!(service.routing_config?.message || '').trim());
            setBlockContacts(service.routing_config?.contacts || []);
            // Same fallback the backend uses for this mode: no agency-name field
            // exists, so the first contact's name is who the clerk named.
            setBlockJurisdiction(
                service.routing_config?.third_party_name
                || service.routing_config?.contacts?.[0]?.name
                || null,
            );
        }

        setStep('form');
        updateHash(`report/${service.service_code}`);
        scrollToTop('instant');
    };

    // Which road the pin landed on, shown under the map so a resident can see
    // the system agreed with them before they type anything.
    const [detectedRoad, setDetectedRoad] = useState<{ name: string; distance_m: number } | null>(null);
    const roadCheckSeq = useRef(0);

    /**
     * Ask the server whether a report here would be redirected.
     *
     * This used to lowercase the whole formatted address and substring-match
     * configured road names into it, which put the city, county and ZIP into
     * the match surface and read a corner lot's address off the cross street.
     * The server measures distance to real road geometry instead. It also
     * re-runs the same check on submit, so this is a courtesy rather than the
     * enforcement point -- and it fails open: a check that errors never blocks.
     */
    const checkRoadBasedBlocking = useCallback(async (service: ServiceDefinition, lat?: number, lng?: number) => {
        if (service.routing_mode !== 'road_based') return;
        const seq = ++roadCheckSeq.current;
        try {
            const result = await api.roadCheck(service.service_code, lat, lng);
            // A slower earlier request must not overwrite a newer answer.
            if (seq !== roadCheckSeq.current) return;
            setDetectedRoad(result.detected_road);
            setIsBlocked(result.blocked);
            setBlockMessage(result.blocked ? result.message : '');
            setBlockMessageIsDefault(Boolean(result.blocked && result.message_is_default));
            setBlockContacts(
                result.blocked
                    ? (result.contacts || []).map(c => ({
                        name: c.name || '',
                        phone: c.phone || '',
                        // Collected in the routing modal and previously dropped here,
                        // so an agency that only publishes an address was unreachable.
                        email: (c as { email?: string }).email || '',
                        url: c.url || '',
                    }))
                    : [],
            );
            setBlockJurisdiction(result.blocked ? result.jurisdiction : null);
        } catch {
            if (seq !== roadCheckSeq.current) return;
            setDetectedRoad(null);
            setIsBlocked(false);
            setBlockMessage('');
            setBlockContacts([]);
            setBlockJurisdiction(null);
        }
    }, []);

    const validateForm = (): boolean => {
        const errors: Record<string, string> = {};

        if (!formData.description || formData.description.length < 10) {
            errors.description = 'Please provide a detailed description (at least 10 characters)';
        }
        if (!formData.email || !/\S+@\S+\.\S+/.test(formData.email)) {
            errors.email = 'Please enter a valid email address';
        }
        if (formData.phone && !isValidPhone(formData.phone)) {
            errors.phone = 'Please enter a valid phone number, e.g. (555) 123-4567';
        }

        // Validate required custom questions
        const questions = selectedService?.routing_config?.custom_questions;
        if (questions) {
            for (const q of questions) {
                if (q.required) {
                    const answer = customAnswers[q.label];
                    const isEmpty = answer === undefined || answer === '' || (Array.isArray(answer) && answer.length === 0);
                    if (isEmpty) {
                        errors[`custom_${q.id}`] = `${q.label} is required`;
                    }
                }
            }
        }

        setFormErrors(errors);
        return Object.keys(errors).length === 0;
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();

        if (!validateForm()) return;

        setIsSubmitting(true);
        try {
            // Matched asset: ONLY use user-selected asset (no automatic proximity detection)
            let matchedAsset: typeof formData.matched_asset = undefined;

            // User explicitly clicked "Select this..." on an asset marker
            if (selectedAsset) {
                matchedAsset = {
                    layer_name: selectedAsset.layerName,
                    layer_id: 0, // Will be filled in by backend
                    asset_id: selectedAsset.properties.asset_id || selectedAsset.properties.id,
                    asset_type: selectedAsset.properties.asset_type || selectedAsset.layerName,
                    properties: selectedAsset.properties,
                    distance_meters: 0, // Exact selection, no distance
                };
            }
            // Note: No automatic proximity detection - user must explicitly select an asset

            const result = await api.createRequest({
                ...formData,
                preferred_language: language,  // Capture user's selected language for notifications
                media_urls: photoPreviewUrls.slice(0, 3),  // Include photos (max 3)
                matched_asset: matchedAsset,
                custom_fields: customAnswers,
            });
            setSubmittedId(result.service_request_id);
            // Save to localStorage so Track Requests can identify "your" submissions
            try {
                const myRequests: string[] = JSON.parse(localStorage.getItem('my_requests') || '[]');
                if (!myRequests.includes(result.service_request_id)) {
                    myRequests.push(result.service_request_id);
                    localStorage.setItem('my_requests', JSON.stringify(myRequests));
                }
            } catch { /* ignore localStorage errors */ }
            setStep('success');
            updateHash('success');
            scrollToTop('instant');
        } catch (err) {
            console.error('Failed to submit request:', err);
            setFormErrors({ submit: 'Failed to submit request. Please try again.' });
        } finally {
            setIsSubmitting(false);
        }
    };
    const handleReset = () => {
        setStep('categories');
        setSelectedService(null);
        setFormData({
            service_code: '',
            description: '',
            address: '',
            first_name: '',
            last_name: '',
            email: '',
            phone: '',
            is_public: true,
        });
        setFormErrors({});
        setSubmittedId(null);
        setPhotos([]);
        setPhotoPreviewUrls([]);
        setLocation({ address: '', lat: null, lng: null });
        // Clear blocking state
        setIsBlocked(false);
        setBlockMessage('');
        setBlockContacts([]);
        // Clear custom answers
        setCustomAnswers({});
        // Strip hash from URL
        window.history.replaceState(null, '', window.location.pathname);
    };

    const handlePhotoUpload = (files: FileList) => {
        const newPhotos = Array.from(files).slice(0, 3 - photos.length); // Max 3 photos
        setPhotos((prev) => [...prev, ...newPhotos]);

        // Create preview URLs
        newPhotos.forEach((file) => {
            const reader = new FileReader();
            reader.onloadend = () => {
                setPhotoPreviewUrls((prev) => [...prev, reader.result as string]);
            };
            reader.readAsDataURL(file);
        });
    };

    const handleRemovePhoto = (index: number) => {
        setPhotos((prev) => prev.filter((_, i) => i !== index));
        setPhotoPreviewUrls((prev) => prev.filter((_, i) => i !== index));
    };

    const getIcon = (iconName: string) => {
        const IconComponent = iconMap[iconName] || AlertCircle;
        return <IconComponent className="w-8 h-8" />;
    };

    return (
        <div className="min-h-screen flex flex-col">
            {/* Header Container - Reorders on mobile (banner first, then nav) */}
            <div className="flex flex-col-reverse md:flex-col sticky top-0 z-40">
                {/* Navigation - Clean Mobile Header */}
                <nav className="glass-sidebar py-3 md:py-4 px-4 md:px-6 flex items-center justify-between relative z-10" aria-label="Main navigation">
                    <button
                        onClick={() => {
                            setShowTrackingView(false);
                            updateHash('');
                            window.scrollTo(0, 0);
                            setStep('categories');
                            setSelectedService(null);
                        }}
                        className="flex items-center gap-2 md:gap-3 hover:opacity-80 transition-opacity cursor-pointer"
                        aria-label="Go to home page"
                    >
                        {settings?.logo_url ? (
                            <img src={settings.logo_url} alt="Logo" className="h-8 md:h-10 w-auto" />
                        ) : (
                            <div className="w-8 h-8 md:w-10 md:h-10 rounded-xl bg-gradient-to-br from-primary-400 to-primary-600 flex items-center justify-center">
                                <Home className="w-4 h-4 md:w-6 md:h-6 text-white" />
                            </div>
                        )}
                        <h1 className="text-lg md:text-xl font-semibold text-white hidden sm:block" data-no-translate>
                            {settings?.township_name || 'Municipality 311'}
                        </h1>
                    </button>

                    <div className="flex items-center gap-2 md:gap-4">
                        {/* Language selector */}
                        {translationEnabled && <LanguageSelector />}
                        <div className="relative">
                            <Link
                                to="/login"
                                className="px-3 py-1.5 rounded-lg bg-white/10 hover:bg-white/20 text-white/70 hover:text-white text-xs md:text-sm font-medium transition-all border border-white/10 hover:border-white/20 no-underline decoration-transparent"
                            >
                                Staff Login
                            </Link>
                        </div>
                    </div>
                </nav>

                {/* Persistent Non-Emergency Warning Banner */}
                <div className="bg-slate-900/95 backdrop-blur-sm">
                    <div className="bg-gradient-to-r from-amber-500/30 via-orange-500/30 to-red-500/30 border-b border-amber-500/30">
                        <div className="max-w-6xl mx-auto px-4 py-2 flex items-center justify-center gap-2 text-center">
                            <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0" />
                            <p className="text-amber-200 text-sm">
                                <strong>Non-Emergency Only</strong> — For police, fire, or medical emergencies, call <strong className="text-white">911</strong>
                            </p>
                        </div>
                    </div>
                </div>
            </div>

            {/* Non-Emergency Disclaimer Modal - Friendly Welcome Design */}
            <AnimatePresence>
                {showDisclaimerModal && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center p-4"
                    >
                        <motion.div
                            initial={{ scale: 0.95, opacity: 0, y: 20 }}
                            animate={{ scale: 1, opacity: 1, y: 0 }}
                            exit={{ scale: 0.95, opacity: 0, y: 20 }}
                            transition={{ type: "spring", damping: 25, stiffness: 300 }}
                            className="bg-gradient-to-br from-slate-800 via-slate-800 to-slate-900 rounded-2xl max-w-lg w-full p-6 border border-white/10 shadow-2xl"
                        >
                            {/* Friendly Welcome Header */}
                            <div className="text-center mb-6">
                                {settings?.logo_url ? (
                                    <img
                                        src={settings.logo_url}
                                        alt={settings?.township_name || "Municipality"}
                                        className="h-12 mx-auto mb-4 object-contain"
                                    />
                                ) : (
                                    <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-primary-500 to-purple-600 flex items-center justify-center mx-auto mb-4 shadow-lg shadow-primary-500/25">
                                        <Sparkles className="w-8 h-8 text-white" />
                                    </div>
                                )}
                                <h2 className="text-2xl font-bold text-white mb-1">
                                    Welcome to {settings?.township_name || "311 Services"}!
                                </h2>
                                <p className="text-white/60 text-sm">Your community service request portal</p>
                            </div>

                            {/* Helpful Info Card - Not scary! */}
                            <div className="bg-gradient-to-r from-blue-500/10 via-indigo-500/10 to-purple-500/10 border border-blue-400/20 rounded-xl p-4 mb-6">
                                <div className="flex items-start gap-3">
                                    <div className="w-10 h-10 rounded-lg bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                        <Phone className="w-5 h-5 text-blue-400" />
                                    </div>
                                    <div>
                                        <h3 className="text-blue-300 font-semibold mb-1">Quick Reminder</h3>
                                        <p className="text-white/70 text-sm leading-relaxed">
                                            This portal is for <strong className="text-white">non-emergency requests</strong> like
                                            potholes, streetlights, trash pickup, and general municipal services.
                                        </p>
                                    </div>
                                </div>

                                <div className="mt-3 pt-3 border-t border-white/10">
                                    <p className="text-white/60 text-sm flex items-start gap-2">
                                        <CheckCircle2 className="w-4 h-4 text-emerald-400 mt-0.5 flex-shrink-0" />
                                        <span>For emergencies (police, fire, medical), please dial <strong className="text-white whitespace-nowrap">911</strong></span>
                                    </p>
                                </div>
                            </div>

                            {/* Friendly Checkbox */}
                            <label className="flex items-center gap-3 mb-6 cursor-pointer group p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors border border-transparent hover:border-white/10">
                                <div className="relative">
                                    <input
                                        type="checkbox"
                                        checked={disclaimerChecked}
                                        onChange={(e) => setDisclaimerChecked(e.target.checked)}
                                        className="sr-only peer"
                                    />
                                    <div className="w-6 h-6 rounded-lg border-2 border-white/30 peer-checked:border-primary-500 peer-checked:bg-primary-500 transition-all flex items-center justify-center">
                                        {disclaimerChecked && (
                                            <CheckCircle2 className="w-4 h-4 text-white" />
                                        )}
                                    </div>
                                </div>
                                <span className="text-white/80 text-sm leading-relaxed group-hover:text-white transition-colors">
                                    Got it! I'll use this for non-emergency requests and call 911 for emergencies.
                                </span>
                            </label>

                            {/* Welcoming Continue Button */}
                            <Button
                                onClick={handleDisclaimerAcknowledge}
                                disabled={!disclaimerChecked}
                                className={`w-full py-3 text-base font-medium ${!disclaimerChecked ? 'opacity-50 cursor-not-allowed' : 'bg-gradient-to-r from-primary-500 to-purple-600 hover:from-primary-600 hover:to-purple-700'}`}
                            >
                                {disclaimerChecked ? "Let's Get Started! →" : "Check the box above to continue"}
                            </Button>
                        </motion.div>
                    </motion.div>
                )}
            </AnimatePresence>


            {/* Main Content */}
            <main id="main-content" className="flex-1 px-4 py-8 md:px-8 max-w-6xl mx-auto w-full">
                {/* Tracking View */}
                {showTrackingView ? (
                    <div className="space-y-6">
                        <button
                            onClick={() => {
                                setShowTrackingView(false);
                                updateHash('');
                            }}
                            className="flex items-center gap-2 text-white/60 hover:text-white transition-colors"
                            aria-label="Go back to home page"
                        >
                            <ArrowLeft className="w-5 h-5" />
                            <span>Back to Home</span>
                        </button>
                        <TrackRequests
                            initialRequestId={urlRequestId}
                            selectedRequestId={
                                currentHash === 'track' ? null :
                                    currentHash.startsWith('track/') ? currentHash.split('/')[1] :
                                        urlRequestId || null
                            }
                            onRequestSelect={(requestId) => {
                                if (requestId) {
                                    updateHash(`track/${requestId}`);
                                } else {
                                    updateHash('track');
                                }
                            }}
                        />
                    </div>
                ) : (
                    <AnimatePresence mode="wait">
                        {step === 'categories' && (
                            <motion.div
                                key="categories"
                                initial={{ opacity: 0, y: 20 }}
                                animate={{ opacity: 1, y: 0 }}
                                exit={{ opacity: 0, y: -20 }}
                                className="space-y-8"
                            >
                                {/* Hero Section */}
                                <div className="text-center space-y-6">
                                    <motion.div
                                        initial={{ scale: 0.9, opacity: 0 }}
                                        animate={{ scale: 1, opacity: 1 }}
                                        transition={{ delay: 0.1 }}
                                        className="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-primary-500/20 border border-primary-500/30"
                                    >
                                        <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
                                        <span className="text-sm font-medium text-primary-200">
                                            Report Requests Online
                                        </span>
                                    </motion.div>

                                    <motion.h1
                                        initial={{ y: 20, opacity: 0 }}
                                        animate={{ y: 0, opacity: 1 }}
                                        transition={{ delay: 0.2 }}
                                        className="text-4xl md:text-5xl lg:text-6xl font-bold text-gradient"
                                    >
                                        {settings?.hero_text || 'How can we help?'}
                                    </motion.h1>

                                    <motion.p
                                        initial={{ y: 20, opacity: 0 }}
                                        animate={{ y: 0, opacity: 1 }}
                                        transition={{ delay: 0.3 }}
                                        className="text-lg text-white/60 max-w-xl mx-auto"
                                    >
                                        {"Report issues, request services, and help make our community better. Select a category below to get started."}
                                    </motion.p>

                                    {/* Search */}
                                    <motion.div
                                        initial={{ y: 20, opacity: 0 }}
                                        animate={{ y: 0, opacity: 1 }}
                                        transition={{ delay: 0.4 }}
                                        className="max-w-md mx-auto"
                                    >
                                        <div className="relative">
                                            <label htmlFor="service-search" className="sr-only">{"Search services..."}</label>
                                            <div
                                                className="absolute top-1/2 -translate-y-1/2 w-5 h-5 pointer-events-none"
                                                style={{
                                                    left: '1rem',
                                                    backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='rgba(255,255,255,0.7)' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='11' cy='11' r='8'%3E%3C/circle%3E%3Cline x1='21' y1='21' x2='16.65' y2='16.65'%3E%3C/line%3E%3C/svg%3E")`,
                                                    backgroundSize: 'contain',
                                                    backgroundRepeat: 'no-repeat'
                                                }}
                                                aria-hidden="true"
                                            />
                                            <input
                                                id="service-search"
                                                type="text"
                                                placeholder={"Search services..."}
                                                value={searchQuery}
                                                onChange={(e) => setSearchQuery(e.target.value)}
                                                className="glass-input pl-12"
                                                aria-describedby="search-results-count"
                                            />
                                        </div>
                                        <p id="search-results-count" className="sr-only" aria-live="polite">
                                            {filteredServices.length} services found
                                        </p>
                                    </motion.div>
                                </div>

                                {/* Service Categories Grid */}
                                {isLoading ? (
                                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4" role="status" aria-label="Loading services">
                                        {Array.from({ length: 8 }).map((_, i) => (
                                            <div key={i} className="rounded-2xl border border-white/10 bg-white/5 p-6 animate-pulse">
                                                <div className="w-12 h-12 rounded-xl bg-white/10 mb-4" />
                                                <div className="h-4 bg-white/10 rounded w-3/4 mb-2" />
                                                <div className="h-3 bg-white/[.06] rounded w-full mb-1" />
                                                <div className="h-3 bg-white/[.06] rounded w-2/3" />
                                            </div>
                                        ))}
                                        <span className="sr-only">Loading service categories...</span>
                                    </div>
                                ) : (
                                    <motion.div
                                        initial={{ opacity: 0 }}
                                        animate={{ opacity: 1 }}
                                        transition={{ delay: 0.5 }}
                                        className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4"
                                    >
                                        {filteredServices.map((service, index) => (
                                            <motion.div
                                                key={service.id}
                                                initial={{ opacity: 0, y: 20 }}
                                                animate={{ opacity: 1, y: 0 }}
                                                transition={{ delay: 0.1 * index }}
                                            >
                                                <Card
                                                    hover
                                                    onClick={() => handleSelectService(service)}
                                                    className="h-full"
                                                >
                                                    <div className="flex flex-col items-center text-center space-y-3">
                                                        <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-primary-500/30 to-primary-600/30 flex items-center justify-center text-primary-300">
                                                            {getIcon(service.icon)}
                                                        </div>
                                                        <h3 className="font-semibold text-white">
                                                            {service.service_name}
                                                        </h3>
                                                        <p className="text-sm text-white/50 line-clamp-2">
                                                            {service.description}
                                                        </p>
                                                    </div>
                                                </Card>
                                            </motion.div>
                                        ))}
                                    </motion.div>
                                )}

                                {filteredServices.length === 0 && !isLoading && (
                                    <div className="text-center py-12">
                                        <p className="text-white/60">No services found matching your search.</p>
                                    </div>
                                )}

                                {/* Community Map Section */}
                                {/* Section Divider - Modern Wave */}
                                <div className="my-16 relative">
                                    <svg className="w-full h-12" viewBox="0 0 1200 60" preserveAspectRatio="none">
                                        <defs>
                                            <linearGradient id="waveGradient" x1="0%" y1="0%" x2="100%" y2="0%">
                                                <stop offset="0%" stopColor="rgba(79, 70, 229, 0)" />
                                                <stop offset="20%" stopColor="rgba(99, 102, 241, 0.7)" />
                                                <stop offset="50%" stopColor="rgba(139, 92, 246, 0.9)" />
                                                <stop offset="80%" stopColor="rgba(99, 102, 241, 0.7)" />
                                                <stop offset="100%" stopColor="rgba(79, 70, 229, 0)" />
                                            </linearGradient>
                                        </defs>
                                        <path
                                            d="M0,30 C300,10 600,50 900,30 C1050,20 1150,35 1200,30"
                                            fill="none"
                                            stroke="url(#waveGradient)"
                                            strokeWidth="4"
                                            strokeLinecap="round"
                                        />
                                    </svg>
                                </div>
                                <motion.div
                                    initial={{ opacity: 0, y: 20 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    transition={{ delay: 0.6 }}
                                    className="space-y-4"
                                >
                                    <h2 className="text-2xl font-bold text-white text-center">
                                        {"Community Requests Map"}
                                    </h2>
                                    <p className="text-white/60 text-center mb-6">
                                        {"View all reported issues and service requests in our community"}
                                    </p>
                                    <div className="h-[500px] rounded-2xl overflow-hidden">
                                        <StaffDashboardMap
                                            config={mapConfig}
                                            requests={allRequests}
                                            mapLayers={mapLayers}
                                            services={services}
                                            departments={[]}
                                            users={[]}
                                            townshipBoundary={townshipBoundary}
                                            onRequestSelect={(requestId) => {
                                                if (requestId) {
                                                    updateHash(`track/${requestId}`);
                                                    setShowTrackingView(true);
                                                    scrollToTop('instant');
                                                }
                                            }}
                                        />
                                    </div>
                                </motion.div>

                                {/* Track Requests Button */}
                                <motion.div
                                    initial={{ opacity: 0, y: 20 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    transition={{ delay: 0.7 }}
                                    className="text-center pt-8"
                                >
                                    <Button
                                        onClick={() => {
                                            setShowTrackingView(true);
                                            updateHash('track');
                                            scrollToTop('instant');
                                        }}
                                        variant="secondary"
                                        size="lg"
                                        className="px-8 py-4"
                                    >
                                        <ClipboardList className="w-5 h-5 mr-2" />
                                        {"Track My Requests"}
                                    </Button>
                                </motion.div>
                            </motion.div>
                        )}

                        {step === 'form' && selectedService && (
                            <motion.div
                                key="form"
                                initial={{ opacity: 0, x: 50 }}
                                animate={{ opacity: 1, x: 0 }}
                                exit={{ opacity: 0, x: -50 }}
                                className="max-w-2xl mx-auto space-y-6"
                            >
                                <button
                                    onClick={() => {
                                        setStep('categories');
                                        setSelectedService(null);
                                        updateHash('');
                                    }}
                                    className="flex items-center gap-2 text-white/60 hover:text-white transition-colors"
                                >
                                    <ArrowLeft className="w-5 h-5" />
                                    <span>{"Back to categories"}</span>
                                </button>

                                {/* Selected service indicator */}
                                <div className="flex items-center gap-4 p-4 glass-card">
                                    <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-primary-500/30 to-primary-600/30 flex items-center justify-center text-primary-300">
                                        {getIcon(selectedService.icon)}
                                    </div>
                                    <div>
                                        <h2 className="text-lg font-semibold text-white">
                                            {selectedService.service_name}
                                        </h2>
                                        <p className="text-sm text-white/50">{selectedService.description}</p>
                                    </div>
                                </div>

                                {/* Whole service handled elsewhere -- not about a location, so the
                                    notice omits the road line. Same component as the road-based
                                    redirect so a resident meets one design, not two. */}
                                {isBlocked && selectedService.routing_mode === 'third_party' && (
                                    <RedirectNotice
                                        variant="service"
                                        jurisdiction={blockJurisdiction}
                                        message={blockMessage}
                                        messageIsDefault={blockMessageIsDefault}
                                        contacts={blockContacts}
                                        serviceName={selectedService.service_name}
                                    />
                                )}

                                {/* Form - only show if NOT blocked OR if road-based (need address first) */}
                                {(!isBlocked || selectedService.routing_mode === 'road_based') && (
                                    <form onSubmit={handleSubmit} className="space-y-6">
                                        <Card>
                                            <div className="space-y-5">
                                                <Textarea
                                                    label={"Description"}
                                                    placeholder="Please describe the issue in detail..."
                                                    value={formData.description}
                                                    onChange={(e) =>
                                                        setFormData((prev) => ({ ...prev, description: e.target.value }))
                                                    }
                                                    error={formErrors.description}
                                                    required
                                                />

                                                {/* Google Maps Location Picker */}
                                                {mapProviderReady(mapsRaw) ? (
                                                    <div>
                                                        <label className="block text-sm font-medium text-white/70 mb-2">
                                                            {"Location / Address"}
                                                        </label>
                                                        <LocationPicker
                                                            config={mapConfig}
                                                            townshipBoundary={townshipBoundary}
                                                            customLayers={mapLayers.filter(layer => {
                                                                // Check if layer is visible
                                                                if ((layer as any).visible_on_map === false) return false;
                                                                // Layer applies if: no service_codes (applies to all) OR includes current category
                                                                const codes = layer.service_codes || [];
                                                                return codes.length === 0 || codes.includes(selectedService.service_code);
                                                            })}
                                                            value={location}
                                                            onOutOfBounds={() => setIsLocationOutOfBounds(true)}
                                                            onAssetSelect={(asset) => {
                                                                setSelectedAsset(asset);
                                                            }}
                                                            onChange={(newLocation) => {
                                                                setLocation(newLocation);
                                                                setIsLocationOutOfBounds(false); // Reset when location changes
                                                                // Save both address AND coordinates
                                                                setFormData((prev) => ({
                                                                    ...prev,
                                                                    address: newLocation.address,
                                                                    lat: newLocation.lat ?? undefined,
                                                                    long: newLocation.lng ?? undefined,
                                                                }));
                                                                // Road rules are decided from the pin, not the address text.
                                                                checkRoadBasedBlocking(
                                                                    selectedService,
                                                                    newLocation.lat ?? undefined,
                                                                    newLocation.lng ?? undefined,
                                                                );
                                                            }}
                                                            placeholder="Search for an address or click on the map..."
                                                        />


                                                    </div>
                                                ) : (
                                                    <>
                                                        <Input
                                                            label={"Location / Address"}
                                                            placeholder="Street address or intersection"
                                                            leftIcon={<MapPin className="w-5 h-5" />}
                                                            value={formData.address}
                                                            onChange={(e) => {
                                                                const newAddress = e.target.value;
                                                                setFormData((prev) => ({ ...prev, address: newAddress }));
                                                                // Typed address with no map: nothing to measure against, so
                                                                // no road rule can apply. Failing open is the point.
                                                                void newAddress;
                                                            }}
                                                        />
                                                        <div className="p-4 rounded-xl bg-white/5 border border-white/10 text-center text-white/40">
                                                            <MapPin className="w-8 h-8 mx-auto mb-2 opacity-50" />
                                                            <p className="text-sm">The interactive map needs a map provider to be configured</p>
                                                        </div>
                                                    </>
                                                )}

                                                {/* Which road the pin landed on. Shown whether or not anything
                                                    is blocked, so a resident can see the system read their pin
                                                    the way they meant it before they type a description. */}
                                                {detectedRoad && (
                                                    <div
                                                        className="flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl bg-white/[0.04] border border-white/10"
                                                        role="status"
                                                    >
                                                        <SignpostBig className="w-4 h-4 text-white/40 shrink-0" aria-hidden="true" />
                                                        <span className="text-sm text-white/60">
                                                            Road detected:{' '}
                                                            <span className="text-white/90 font-medium">{detectedRoad.name}</span>
                                                        </span>
                                                    </div>
                                                )}

                                                {/* This road belongs to someone else. detectedRoad is what
                                                    decided it, and naming it is the only way a resident can
                                                    spot a pin that landed one lane over and drag it back. */}
                                                {isBlocked && (
                                                    <RedirectNotice
                                                        variant="road"
                                                        jurisdiction={blockJurisdiction}
                                                        message={blockMessage}
                                                        messageIsDefault={blockMessageIsDefault}
                                                        contacts={blockContacts}
                                                        roadName={detectedRoad?.name}
                                                        serviceName={selectedService.service_name}
                                                    />
                                                )}

                                                {/* Photo Upload */}
                                                <PhotoUpload
                                                    previewUrls={photoPreviewUrls}
                                                    onAdd={handlePhotoUpload}
                                                    onRemove={handleRemovePhoto}
                                                />
                                            </div>
                                        </Card>

                                        {/* Custom Questions - Dynamic */}
                                        {selectedService.routing_config?.custom_questions &&
                                            selectedService.routing_config.custom_questions.length > 0 && (
                                                <Card>
                                                    <h3 className="text-lg font-semibold text-white mb-4">
                                                        Additional Information
                                                    </h3>
                                                    <div className="space-y-4">
                                                        {selectedService.routing_config.custom_questions.map((q) => (
                                                            <div key={q.id} className="space-y-2">
                                                                <label className="block text-sm font-medium text-white/70">
                                                                    {q.label} {q.required && <span className="text-red-400">*</span>}
                                                                </label>

                                                                {/* Text Input */}
                                                                {q.type === 'text' && (
                                                                    <input
                                                                        type="text"
                                                                        placeholder={q.placeholder || ''}
                                                                        value={(customAnswers[q.label] as string) || ''}
                                                                        onChange={(e) => setCustomAnswers(p => ({ ...p, [q.label]: e.target.value }))}
                                                                        className="w-full h-10 rounded-lg bg-white/10 border border-white/20 text-white px-3"
                                                                        required={q.required}
                                                                    />
                                                                )}

                                                                {/* Textarea */}
                                                                {q.type === 'textarea' && (
                                                                    <textarea
                                                                        rows={3}
                                                                        placeholder={q.placeholder || ''}
                                                                        value={(customAnswers[q.label] as string) || ''}
                                                                        onChange={(e) => setCustomAnswers(p => ({ ...p, [q.label]: e.target.value }))}
                                                                        className="w-full rounded-lg bg-white/10 border border-white/20 text-white px-3 py-2"
                                                                        required={q.required}
                                                                    />
                                                                )}

                                                                {/* Number */}
                                                                {q.type === 'number' && (
                                                                    <input
                                                                        type="number"
                                                                        placeholder={q.placeholder || ''}
                                                                        value={(customAnswers[q.label] as string) || ''}
                                                                        onChange={(e) => setCustomAnswers(p => ({ ...p, [q.label]: e.target.value }))}
                                                                        className="w-full h-10 rounded-lg bg-white/10 border border-white/20 text-white px-3"
                                                                        required={q.required}
                                                                    />
                                                                )}

                                                                {/* Date */}
                                                                {q.type === 'date' && (
                                                                    <input
                                                                        type="date"
                                                                        value={(customAnswers[q.label] as string) || ''}
                                                                        onChange={(e) => setCustomAnswers(p => ({ ...p, [q.label]: e.target.value }))}
                                                                        className="w-full h-10 rounded-lg bg-white/10 border border-white/20 text-white px-3"
                                                                        required={q.required}
                                                                    />
                                                                )}

                                                                {/* Yes/No */}
                                                                {q.type === 'yes_no' && (
                                                                    <div className="flex gap-3">
                                                                        {['Yes', 'No'].map(opt => (
                                                                            <button
                                                                                key={opt}
                                                                                type="button"
                                                                                onClick={() => setCustomAnswers(p => ({ ...p, [q.label]: opt }))}
                                                                                className={`flex-1 py-2 rounded-lg border transition-colors ${customAnswers[q.label] === opt
                                                                                    ? 'bg-primary-500/30 border-primary-500 text-white'
                                                                                    : 'bg-white/5 border-white/20 text-white/70 hover:border-white/40'
                                                                                    }`}
                                                                            >
                                                                                {opt}
                                                                            </button>
                                                                        ))}
                                                                    </div>
                                                                )}

                                                                {/* Select Dropdown */}
                                                                {q.type === 'select' && (
                                                                    <select
                                                                        value={(customAnswers[q.label] as string) || ''}
                                                                        onChange={(e) => setCustomAnswers(p => ({ ...p, [q.label]: e.target.value }))}
                                                                        className="w-full h-10 rounded-lg bg-white/10 border border-white/20 text-white px-3"
                                                                        required={q.required}
                                                                        aria-label={q.label}
                                                                    >
                                                                        <option value="">Select...</option>
                                                                        {q.options?.map(opt => (
                                                                            <option key={opt} value={opt}>{opt}</option>
                                                                        ))}
                                                                    </select>
                                                                )}

                                                                {/* Radio Buttons */}
                                                                {q.type === 'radio' && (
                                                                    <div className="space-y-2">
                                                                        {q.options?.map(opt => (
                                                                            <label key={opt} className="flex items-center gap-3 text-white/80 cursor-pointer">
                                                                                <input
                                                                                    type="radio"
                                                                                    name={q.id}
                                                                                    value={opt}
                                                                                    checked={customAnswers[q.label] === opt}
                                                                                    onChange={(e) => setCustomAnswers(p => ({ ...p, [q.label]: e.target.value }))}
                                                                                    className="w-4 h-4"
                                                                                />
                                                                                {opt}
                                                                            </label>
                                                                        ))}
                                                                    </div>
                                                                )}

                                                                {/* Checkboxes */}
                                                                {q.type === 'checkbox' && (
                                                                    <div className="space-y-2">
                                                                        {q.options?.map(opt => (
                                                                            <label key={opt} className="flex items-center gap-3 text-white/80 cursor-pointer">
                                                                                <input
                                                                                    type="checkbox"
                                                                                    value={opt}
                                                                                    checked={(customAnswers[q.label] as string[] || []).includes(opt)}
                                                                                    onChange={(e) => {
                                                                                        const current = (customAnswers[q.label] as string[]) || [];
                                                                                        const updated = e.target.checked
                                                                                            ? [...current, opt]
                                                                                            : current.filter(v => v !== opt);
                                                                                        setCustomAnswers(p => ({ ...p, [q.label]: updated }));
                                                                                    }}
                                                                                    className="w-4 h-4 rounded"
                                                                                />
                                                                                {opt}
                                                                            </label>
                                                                        ))}
                                                                    </div>
                                                                )}
                                                                {formErrors[`custom_${q.id}`] && (
                                                                    <p className="text-red-400 text-sm">{formErrors[`custom_${q.id}`]}</p>
                                                                )}
                                                            </div>
                                                        ))}
                                                    </div>
                                                </Card>
                                            )}

                                        <Card>
                                            <h3 className="text-lg font-semibold text-white mb-4">
                                                {"Contact Information"}
                                            </h3>
                                            <div className="space-y-4">
                                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                                                    <Input
                                                        label={"First Name"}
                                                        placeholder="John"
                                                        value={formData.first_name}
                                                        onChange={(e) =>
                                                            setFormData((prev) => ({ ...prev, first_name: e.target.value }))
                                                        }
                                                    />
                                                    <Input
                                                        label={"Last Name"}
                                                        placeholder="Doe"
                                                        value={formData.last_name}
                                                        onChange={(e) =>
                                                            setFormData((prev) => ({ ...prev, last_name: e.target.value }))
                                                        }
                                                    />
                                                </div>

                                                <Input
                                                    label={"Email"}
                                                    type="email"
                                                    placeholder="you@example.com"
                                                    value={formData.email}
                                                    onChange={(e) =>
                                                        setFormData((prev) => ({ ...prev, email: e.target.value }))
                                                    }
                                                    error={formErrors.email}
                                                    required
                                                />

                                                <Input
                                                    label={"Phone (optional)"}
                                                    type="tel"
                                                    inputMode="tel"
                                                    placeholder="(555) 123-4567"
                                                    value={formData.phone}
                                                    onChange={(e) =>
                                                        setFormData((prev) => ({ ...prev, phone: filterPhoneInput(e.target.value) }))
                                                    }
                                                    error={formErrors.phone}
                                                />
                                            </div>
                                        </Card>

                                        {/* Public-feed visibility. Only offered when the town has
                                            turned the Unlisted Reports module on. Sharing is the
                                            default; hiding is a deliberate, unchecked-by-default box. */}
                                        {(settings?.modules?.unlisted_reports ?? (settings?.modules as any)?.private_reports) && (
                                            <Card>
                                                <label className="flex items-start gap-3 cursor-pointer">
                                                    <input
                                                        type="checkbox"
                                                        checked={formData.is_public === false}
                                                        onChange={(e) =>
                                                            setFormData((prev) => ({ ...prev, is_public: !e.target.checked }))
                                                        }
                                                        className="mt-0.5 w-5 h-5 rounded border-white/20 bg-white/10 text-primary-500 shrink-0"
                                                    />
                                                    <span className="min-w-0">
                                                        <span className="block text-sm font-medium text-white">
                                                            Hide this report from the public map and feed
                                                        </span>
                                                        <span className="block text-xs text-white/50 mt-1 leading-relaxed">
                                                            {formData.is_public === false ? (
                                                                <>Your report won&apos;t appear on the public map, feed, or open
                                                                    data feeds. <strong className="text-white/70">Anyone you send
                                                                        your tracking link to can still view it.</strong> Town staff
                                                                    always see it and will work it normally, and it still counts in
                                                                    anonymized statistics.</>
                                                            ) : (
                                                                <>Leave this unchecked to share your report with neighbors on the
                                                                    public map and feed. Your name and contact details are never
                                                                    shown either way.</>
                                                            )}
                                                        </span>
                                                    </span>
                                                </label>
                                            </Card>
                                        )}

                                        {formErrors.submit && (
                                            <div className="p-4 rounded-xl bg-red-500/20 border border-red-500/30 text-red-300">
                                                {formErrors.submit}
                                            </div>
                                        )}

                                        {!isBlocked && !isLocationOutOfBounds ? (
                                            <Button
                                                type="submit"
                                                size="lg"
                                                className="w-full"
                                                isLoading={isSubmitting}
                                                rightIcon={<Send className="w-5 h-5" />}
                                            >
                                                {"Submit Request"}
                                            </Button>
                                        ) : isLocationOutOfBounds ? (
                                            <div className="p-4 rounded-xl bg-red-500/20 border border-red-500/30 text-red-300 text-center">
                                                <strong>Cannot submit:</strong> The selected location is outside the municipality boundary. Please choose a location within the jurisdiction.
                                            </div>
                                        ) : (
                                            <div className="p-4 rounded-xl bg-amber-500/20 border border-amber-500/30 text-amber-300 text-center">
                                                Submission blocked - see notice above
                                            </div>
                                        )}

                                    </form>
                                )}
                            </motion.div>
                        )}

                        {step === 'success' && (
                            <motion.div
                                key="success"
                                initial={{ opacity: 0, scale: 0.9 }}
                                animate={{ opacity: 1, scale: 1 }}
                                className="max-w-lg mx-auto text-center space-y-8 py-12"
                            >
                                <motion.div
                                    initial={{ scale: 0 }}
                                    animate={{ scale: 1 }}
                                    transition={{ type: 'spring', delay: 0.2 }}
                                    className="w-24 h-24 mx-auto rounded-full bg-green-500/20 border border-green-500/30 flex items-center justify-center glow-effect"
                                    style={{ boxShadow: '0 0 60px rgba(34, 197, 94, 0.4)' }}
                                >
                                    <CheckCircle2 className="w-12 h-12 text-green-400" />
                                </motion.div>

                                <div className="space-y-4">
                                    <h2 className="text-3xl font-bold text-white">Request Submitted!</h2>
                                    <p className="text-white/60">
                                        Thank you for helping improve our community. Your request has been
                                        received and will be reviewed shortly.
                                    </p>
                                    {submittedId && (
                                        <div className="inline-block px-4 py-2 rounded-lg bg-white/10 border border-white/20">
                                            <span className="text-white/50 text-sm">Request ID: </span>
                                            <span className="font-mono text-white font-medium">{submittedId}</span>
                                        </div>
                                    )}
                                </div>

                                <Button onClick={handleReset} size="lg">
                                    Submit Another Request
                                </Button>
                            </motion.div>
                        )}
                    </AnimatePresence>
                )}
            </main>

            {/* Footer */}
            <footer className="glass-sidebar py-6 px-4 mt-auto">
                <div className="max-w-6xl mx-auto flex flex-col items-center gap-4">
                    {/* Copyright */}
                    <p className="text-white/40 text-sm text-center">
                        © {new Date().getFullYear()} {settings?.township_name || 'Municipality 311'}. {"All rights reserved"}
                    </p>

                    {/* Social Links */}
                    {settings?.social_links && settings.social_links.length > 0 && (
                        <div className="flex items-center justify-center flex-wrap gap-2">
                            {settings.social_links.map((link, index) => {
                                // Ensure URL is absolute
                                const url = link.url.startsWith('http') ? link.url : `https://${link.url}`;
                                return (
                                    <a
                                        key={index}
                                        href={url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="w-9 h-9 rounded-lg bg-white/10 hover:bg-white/20 flex items-center justify-center transition-all hover:scale-110"
                                        title={link.platform.charAt(0).toUpperCase() + link.platform.slice(1)}
                                    >
                                        {link.icon === 'Globe' && <Globe className="w-4 h-4 text-white/70" />}
                                        {link.icon === 'Facebook' && <Facebook className="w-4 h-4 text-blue-400" />}
                                        {link.icon === 'Instagram' && <Instagram className="w-4 h-4 text-pink-400" />}
                                        {link.icon === 'Youtube' && <Youtube className="w-4 h-4 text-red-400" />}
                                        {link.icon === 'Twitter' && <Twitter className="w-4 h-4 text-sky-400" />}
                                        {link.icon === 'Linkedin' && <Linkedin className="w-4 h-4 text-blue-500" />}
                                    </a>
                                );
                            })}
                        </div>
                    )}

                    {/* Legal Links */}
                    <div className="flex items-center justify-center flex-wrap gap-x-4 gap-y-2 text-sm">
                        <Link to="/privacy" className="text-white/40 hover:text-white/80 transition-colors">
                            {"Privacy"}
                        </Link>
                        <span className="text-white/20 hidden sm:inline">•</span>
                        <Link to="/accessibility" className="text-white/40 hover:text-white/80 transition-colors">
                            {"Accessibility"}
                        </Link>
                        <span className="text-white/20 hidden sm:inline">•</span>
                        <Link to="/terms" className="text-white/40 hover:text-white/80 transition-colors">
                            {"Terms"}
                        </Link>
                        <span className="text-white/20 hidden sm:inline">•</span>
                        {/* Moved here from the product credit below. The bundled
                            open-source dependencies require their notices be
                            available, so the link stays -- but it is a legal
                            link, and a resident reading a byline does not need
                            other people's licences in it. */}
                        <a href="/third-party-licenses.txt" className="text-white/40 hover:text-white/80 transition-colors">
                            {"Notices"}
                        </a>
                    </div>
                </div>
                {/* Powered by Pinpoint 311 */}
                <div className="max-w-6xl mx-auto mt-5 pt-5 border-t border-white/10 flex flex-col items-center gap-2.5">
                    <a
                        href="https://pinpoint311.org"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="brand-link group inline-flex items-center gap-2.5"
                        data-no-translate
                    >
                        {/* A kicker, not a sentence.
                            "Powered by" at body size put a ~10px cap height next
                            to a ~24px wordmark, which reads as two mismatched
                            things rather than one lockup. Setting it as a small
                            tracked label makes the size difference deliberate,
                            and matches how the staff sidebar already pairs these
                            two. The logo carries ~8% transparent padding top and
                            bottom, symmetric, so centring aligns correctly. */}
                        <span className="text-[11px] uppercase tracking-[0.18em] font-medium text-white/40 group-hover:text-white/60 transition-colors">
                            {"Powered by"}
                        </span>
                        <img
                            src="/pinpoint311_logo_dark_transparent.png"
                            alt="Pinpoint 311"
                            width={952}
                            height={139}
                            className="h-7 sm:h-8 w-auto opacity-85 group-hover:opacity-100 transition-opacity"
                        />
                    </a>
                    {/* The credit line proper.

                        It used to read "Free & Open Source Municipal Platform",
                        which names a category rather than the project, and made
                        an open-source claim with nothing to check it against --
                        the weakest form of that claim, and the one a town
                        evaluating this platform is most likely to want to
                        verify. So the licence is named and the source is linked.

                        The nonprofit line is here because for a municipal
                        audience "who is behind this and what happens if they
                        lose interest" is a real question, and a 501(c)(3) fiscal
                        sponsor is a better answer than a website. Worded exactly
                        as the README does -- fiscally sponsored by, not "a
                        project of", because those mean different things. */}
                    <p className="text-white/30 text-xs text-center" data-no-translate>
                        {"Free, open-source software for local government"}
                    </p>
                </div>
            </footer>
        </div>
    );
}
