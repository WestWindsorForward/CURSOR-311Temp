import React, { useState, useEffect, useRef, useMemo } from 'react';
import { resolveMapProviderConfig, mapProviderReady, RawMapsConfig } from '../maps';
import { motion, AnimatePresence } from 'framer-motion';
import { DndContext, closestCenter, KeyboardSensor, PointerSensor, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core';
import { arrayMove, SortableContext, sortableKeyboardCoordinates, useSortable, rectSortingStrategy } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { useNavigate } from 'react-router-dom';
import {
    Menu,
    X,
    Palette,
    Users,
    Grid3X3,
    LogOut,
    Save,
    Trash2,
    Plus,
    RefreshCw,
    Sparkles,
    Check,
    AlertTriangle,
    RotateCcw,
    Mail,
    Building2,
    ExternalLink,
    GitFork,
    Edit,
    Phone,
    UserCheck,
    AlertCircle,
    Car,
    Trash,
    Lightbulb,
    TreePine,
    Building,
    Hammer,
    Droplet,
    Bug,
    PaintBucket,
    Wrench,
    Route,
    MapPin,
    Home,
    Zap,
    Shield,
    Heart,
    Star,
    Flag,
    Bell,
    Camera,
    Clock,
    FileText,
    Settings,
    HelpCircle,
    Info,
    Layers,
    Upload,
    BarChart3,
    Terminal,


    ChevronDown,
    GripVertical,
    User as UserIcon,
    Globe,
    Facebook,
    Instagram,
    Youtube,
    Twitter,
    Linkedin,
    type LucideIcon,
    FlaskConical,
    Search,
    Download,
    Eye,
    EyeOff,
    CircleCheck,
    Pencil,
} from 'lucide-react';
import { Button, Card, Modal, Input, Select, Badge, AccordionSection } from '../components/ui';
import { useAuth } from '../context/AuthContext';
import { useSettings } from '../context/SettingsContext';
import { useDialog } from '../components/DialogProvider';
import { api, MapLayer, ResearchPackSwitch } from '../services/api';
import { User, ServiceDefinition, SystemSettings, SystemSecret, Department, RoutingContact } from '../types';
import { usePageNavigation } from '../hooks/usePageNavigation';
import ClientErrorPanel from '../components/ClientErrorPanel';
import OperationsPanel from '../components/OperationsPanel';
import RoadListInput from '../components/RoadListInput';
import RoadCorridorMap from '../components/RoadCorridorMap';
import SetupIntegrationsPage from '../components/SetupIntegrationsPage';
import AuditLogViewer from '../components/AuditLogViewer';
import VersionSwitcher from '../components/VersionSwitcher';
import StayInformedHost from '../components/StayInformed';

// Human-friendly retention period from a day count (reflects any override):
// 365 -> "1 year", 2190 -> "6 years", 2555 -> "7 years", 900 -> "2.5 years".
function formatYears(days: number): string {
    const years = days / 365;
    const rounded = Number.isInteger(years) ? years : Math.round(years * 10) / 10;
    return `${rounded} ${rounded === 1 ? 'year' : 'years'}`;
}

// Render an SLA target in hours as something readable ("72 hours" -> "3 days").
export function formatSlaTarget(hours: number): string {
    if (!hours || hours <= 0) return 'No target';
    if (hours < 24) return `${hours} ${hours === 1 ? 'hour' : 'hours'}`;
    const days = hours / 24;
    const rounded = Number.isInteger(days) ? days : Math.round(days * 10) / 10;
    return `${rounded} ${rounded === 1 ? 'day' : 'days'}`;
}

// Icon library for service categories
const ICON_LIBRARY: { name: string; icon: LucideIcon }[] = [
    { name: 'AlertCircle', icon: AlertCircle },
    { name: 'Car', icon: Car },
    { name: 'Trash', icon: Trash },
    { name: 'Lightbulb', icon: Lightbulb },
    { name: 'TreePine', icon: TreePine },
    { name: 'Building', icon: Building },
    { name: 'Hammer', icon: Hammer },
    { name: 'Droplet', icon: Droplet },
    { name: 'Bug', icon: Bug },
    { name: 'PaintBucket', icon: PaintBucket },
    { name: 'Wrench', icon: Wrench },
    { name: 'Route', icon: Route },
    { name: 'MapPin', icon: MapPin },
    { name: 'Home', icon: Home },
    { name: 'Zap', icon: Zap },
    { name: 'Shield', icon: Shield },
    { name: 'Heart', icon: Heart },
    { name: 'Star', icon: Star },
    { name: 'Flag', icon: Flag },
    { name: 'Bell', icon: Bell },
    { name: 'Camera', icon: Camera },
    { name: 'Mail', icon: Mail },
    { name: 'Phone', icon: Phone },
    { name: 'Clock', icon: Clock },
    { name: 'FileText', icon: FileText },
    { name: 'Settings', icon: Settings },
    { name: 'HelpCircle', icon: HelpCircle },
    { name: 'Info', icon: Info },
    { name: 'Users', icon: Users },
];

type Tab = 'branding' | 'users' | 'departments' | 'services' | 'integration' | 'system' | 'health' | 'compliance';

// Sidebar accordion components
interface SidebarGroupProps {
    title: string;
    icon: LucideIcon;
    isActive: boolean;
    defaultOpen?: boolean;
    children: React.ReactNode;
}

function SidebarGroup({ title, icon: Icon, isActive, defaultOpen = false, children }: SidebarGroupProps) {
    const [isOpen, setIsOpen] = useState(defaultOpen);

    // Auto-open when active
    React.useEffect(() => {
        if (isActive && !isOpen) {
            setIsOpen(true);
        }
    }, [isActive]);

    return (
        <div className="rounded-xl overflow-hidden">
            <button
                onClick={() => setIsOpen(!isOpen)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl transition-colors ${isActive ? 'bg-white/10 text-white' : 'text-white/60 hover:bg-white/5 hover:text-white'
                    }`}
            >
                <Icon className="w-5 h-5" />
                <span className="font-medium flex-1 text-left">{title}</span>
                <motion.div
                    animate={{ rotate: isOpen ? 180 : 0 }}
                    transition={{ duration: 0.2 }}
                >
                    <ChevronDown className="w-4 h-4 opacity-50" />
                </motion.div>
            </button>
            <AnimatePresence initial={false}>
                {isOpen && (
                    <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: 'auto', opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={{ duration: 0.2, ease: 'easeInOut' }}
                        className="overflow-hidden"
                    >
                        <div className="pl-4 py-1 space-y-1">
                            {children}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}

interface SidebarItemProps {
    icon: LucideIcon;
    label: string;
    isActive: boolean;
    onClick: () => void;
}

function SidebarItem({ icon: Icon, label, isActive, onClick }: SidebarItemProps) {
    return (
        <button
            onClick={onClick}
            className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors text-sm ${isActive
                ? 'bg-primary-500/20 text-white'
                : 'text-white/50 hover:bg-white/5 hover:text-white'
                }`}
        >
            <Icon className="w-4 h-4" />
            <span className="font-medium">{label}</span>
        </button>
    );
}


// ============ Drag-and-Drop Service Reorder ============

function BubblyServiceCard({ service, onEdit, onDelete }: {
    service: ServiceDefinition;
    onEdit: (s: ServiceDefinition) => void;
    onDelete: (id: number) => void;
}) {
    const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: service.id });

    const style = {
        transform: CSS.Transform.toString(transform),
        transition,
        zIndex: isDragging ? 50 : undefined,
        opacity: isDragging ? 0.85 : 1,
    };

    const getIconComponent = (iconName?: string) => {
        if (!iconName) return Grid3X3;
        const found = ICON_LIBRARY.find(i => i.name === iconName);
        return found ? found.icon : Grid3X3;
    };

    const IconComp = getIconComponent((service as any).icon);

    const getModeBadge = (mode?: string) => {
        if (mode === 'third_party') return (
            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-2xl text-xs font-semibold bg-gradient-to-r from-purple-500/20 to-indigo-500/20 text-purple-300 border border-purple-500/30 shadow-md shadow-purple-950/40">
                <ExternalLink className="w-3.5 h-3.5" /> 3rd Party
            </span>
        );
        if (mode === 'road_based') return (
            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-2xl text-xs font-semibold bg-gradient-to-r from-amber-500/20 to-orange-500/20 text-amber-300 border border-amber-500/30 shadow-md shadow-amber-950/40">
                <GitFork className="w-3.5 h-3.5" /> Road-Based
            </span>
        );
        return (
            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-2xl text-xs font-semibold bg-gradient-to-r from-emerald-500/20 to-teal-500/20 text-emerald-300 border border-emerald-500/30 shadow-md shadow-emerald-950/40">
                <Building2 className="w-3.5 h-3.5" /> Municipality
            </span>
        );
    };

    return (
        <div ref={setNodeRef} style={style} className={`group ${isDragging ? 'relative z-50' : ''}`}>
            <div className={`surface-card relative p-6 rounded-3xl bg-gradient-to-br from-white/[0.08] via-white/[0.03] to-indigo-950/30 border border-white/15 backdrop-blur-2xl shadow-[0_10px_30px_rgba(0,0,0,0.3)] hover:shadow-[0_20px_50px_rgba(99,102,241,0.2)] hover:border-primary-400/50 hover:-translate-y-1 transition-all duration-300 ${isDragging ? 'ring-2 ring-primary-500 shadow-2xl scale-[1.03]' : ''}`}>
                {/* Glow accent bar on top */}
                <div className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-transparent via-primary-400/40 to-transparent rounded-t-3xl" />

                {/* Top bar: Icon, Code Badge, Drag Handle */}
                <div className="flex items-center justify-between gap-3 mb-4">
                    <div className="flex items-center gap-3">
                        <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-primary-500/25 via-indigo-500/20 to-purple-500/15 border border-white/20 shadow-inner flex items-center justify-center text-primary-300 shrink-0">
                            <IconComp className="w-6 h-6" />
                        </div>
                        <div>
                            <h3 className="font-bold text-white text-lg tracking-tight group-hover:text-primary-200 transition-colors">{service.service_name}</h3>
                            <span className="text-[11px] font-mono font-semibold text-white/50 bg-white/10 border border-white/15 px-2.5 py-0.5 rounded-full tracking-wider">@{service.service_code.toLowerCase()}</span>
                        </div>
                    </div>

                    <button
                        {...attributes}
                        {...listeners}
                        className="p-2 rounded-xl text-white/20 hover:text-white/80 hover:bg-white/10 cursor-grab active:cursor-grabbing transition-colors touch-none shrink-0"
                        aria-label={`Drag to reorder ${service.service_name}`}
                    >
                        <GripVertical className="w-5 h-5" />
                    </button>
                </div>

                {/* Description */}
                <p className="text-xs text-white/65 leading-relaxed min-h-[32px] line-clamp-2">{service.description || 'No description provided.'}</p>

                {/* Routing & Department Badges */}
                <div className="flex items-center gap-2 mt-4 flex-wrap">
                    {getModeBadge(service.routing_mode)}
                    {service.assigned_department ? (
                        <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-2xl text-xs font-medium bg-white/10 border border-white/15 text-white/90">
                            <Users className="w-3.5 h-3.5 text-white/40" />
                            {service.assigned_department.name}
                        </span>
                    ) : (
                        <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-2xl text-xs font-medium bg-white/5 border border-white/10 text-white/30 italic">
                            No department assigned
                        </span>
                    )}
                </div>

                {/* Action Footer */}
                <div className="flex items-center justify-end gap-2.5 mt-5 pt-4 border-t border-white/10">
                    <Button
                        leftIcon={<Edit className="w-4 h-4" />}
                        onClick={() => onEdit(service)}
                        className="rounded-2xl px-5"
                    >
                        Configure Routing
                    </Button>
                    <button
                        onClick={() => onDelete(service.id)}
                        className="p-2.5 rounded-2xl bg-white/5 hover:bg-red-500/20 border border-white/10 hover:border-red-500/30 text-white/40 hover:text-red-300 transition-all"
                        title="Delete Category"
                    >
                        <Trash2 className="w-4 h-4" />
                    </button>
                </div>
            </div>
        </div>
    );
}

export function ServiceCategoriesTab({ services, setServices, loadTabData, setShowServiceModal, handleEditService, handleDeleteService }: {
    services: ServiceDefinition[];
    setServices: (s: ServiceDefinition[]) => void;
    loadTabData: () => void;
    setShowServiceModal: (v: boolean) => void;
    handleEditService: (s: ServiceDefinition) => void;
    handleDeleteService: (id: number) => void;
}) {
    const sensors = useSensors(
        useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
        useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
    );

    const handleDragEnd = async (event: DragEndEvent) => {
        const { active, over } = event;
        if (!over || active.id === over.id) return;

        // @dnd-kit may convert IDs to strings — coerce back to numbers
        const activeId = Number(active.id);
        const overId = Number(over.id);

        const oldIndex = services.findIndex(s => s.id === activeId);
        const newIndex = services.findIndex(s => s.id === overId);
        if (oldIndex === -1 || newIndex === -1) return;

        const newServices = arrayMove(services, oldIndex, newIndex);
        setServices(newServices);

        // Persist to backend
        try {
            await api.reorderServices(
                newServices.map((s, i) => ({ id: Number(s.id), display_order: i }))
            );
        } catch (err) {
            console.error('Failed to reorder services:', err);
            loadTabData(); // Revert on failure
        }
    };

    const roadBasedCount = services.filter(s => s.routing_mode === 'road_based').length;
    const thirdPartyCount = services.filter(s => s.routing_mode === 'third_party').length;
    const municipalCount = services.filter(s => !s.routing_mode || s.routing_mode === 'township').length;

    // No setter for either: the search box and filter dropdown these were written
    // for are not rendered anywhere, so `filteredServices` below always matches
    // everything. Left in place rather than deleted because the filter logic is
    // still correct and only needs a control wired to it -- but the unused
    // setters are dropped, because the build now type-checks and they would fail
    // it, and a name nothing can call is worse than no name.
    const [searchQuery] = useState('');
    const [filterMode] = useState<string>('all');

    const filteredServices = services.filter(s => {
        const matchesSearch = s.service_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
            s.service_code.toLowerCase().includes(searchQuery.toLowerCase()) ||
            (s.description || '').toLowerCase().includes(searchQuery.toLowerCase());
        const mode = s.routing_mode || 'township';
        const matchesFilter = filterMode === 'all' || mode === filterMode;
        return matchesSearch && matchesFilter;
    });

    return (
        <div className="space-y-6">
            {/* Top Bar Header */}
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                <div>
                    <h1 className="text-xl sm:text-2xl font-bold text-white tracking-tight">Service Categories</h1>
                    <p className="text-sm text-white/50 mt-1">Configure portal categories, assignment rules, and automated spatial routing</p>
                </div>
                <Button
                    leftIcon={<Plus className="w-4 h-4" />}
                    onClick={() => setShowServiceModal(true)}
                    className="w-full sm:w-auto bg-gradient-to-r from-primary-500 to-primary-600 hover:from-primary-400 hover:to-primary-500 shadow-lg shadow-primary-500/25"
                >
                    Add Category
                </Button>
            </div>

            {/* 3 Premium Stat Cards (Matching User Management Page) */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div className="p-4 rounded-2xl bg-gradient-to-br from-blue-500/10 to-blue-600/5 border border-blue-500/20 backdrop-blur-sm shadow-xl">
                    <div className="flex items-center gap-3.5">
                        <div className="w-10 h-10 rounded-xl bg-blue-500/20 flex items-center justify-center shrink-0">
                            <Building2 className="w-5 h-5 text-blue-400" />
                        </div>
                        <div>
                            <p className="text-2xl font-bold text-white">{municipalCount}</p>
                            <p className="text-xs text-blue-300/70">Municipality</p>
                        </div>
                    </div>
                </div>
                <div className="p-4 rounded-2xl bg-gradient-to-br from-amber-500/10 to-amber-600/5 border border-amber-500/20 backdrop-blur-sm shadow-xl">
                    <div className="flex items-center gap-3.5">
                        <div className="w-10 h-10 rounded-xl bg-amber-500/20 flex items-center justify-center shrink-0">
                            <GitFork className="w-5 h-5 text-amber-400" />
                        </div>
                        <div>
                            <p className="text-2xl font-bold text-white">{roadBasedCount}</p>
                            <p className="text-xs text-amber-300/70">Road-Based</p>
                        </div>
                    </div>
                </div>
                <div className="p-4 rounded-2xl bg-gradient-to-br from-emerald-500/10 to-emerald-600/5 border border-emerald-500/20 backdrop-blur-sm shadow-xl">
                    <div className="flex items-center gap-3.5">
                        <div className="w-10 h-10 rounded-xl bg-emerald-500/20 flex items-center justify-center shrink-0">
                            <ExternalLink className="w-5 h-5 text-emerald-400" />
                        </div>
                        <div>
                            <p className="text-2xl font-bold text-white">{thirdPartyCount}</p>
                            <p className="text-xs text-emerald-300/70">3rd Party</p>
                        </div>
                    </div>
                </div>
            </div>

            {/* Bubbly Glassmorphic Card Grid */}
            <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
                <SortableContext items={filteredServices.map(s => s.id)} strategy={rectSortingStrategy}>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                        {filteredServices.map((service) => (
                            <BubblyServiceCard
                                key={service.id}
                                service={service}
                                onEdit={handleEditService}
                                onDelete={handleDeleteService}
                            />
                        ))}
                    </div>
                </SortableContext>
            </DndContext>
        </div>
    );
}



/**
 * The departments tab.
 *
 * Lifted out of AdminConsole's render so it can be mounted on its own -- which
 * is what makes it possible to look at, rather than only to reason about. It is
 * the same markup, with the values it used to close over passed in.
 */
export function DepartmentsTab({
    departments, onAdd, onEdit, onDelete,
}: {
    departments: Department[];
    onAdd: () => void;
    onEdit: (dept: Department) => void;
    onDelete: (id: number) => void;
}) {
    return (
                            <div className="space-y-6">
                                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                                    <div>
                                        <h1 className="text-xl sm:text-2xl font-bold text-white tracking-tight">Departments</h1>
                                        <p className="text-sm text-white/50 mt-1">The teams reports are assigned to, and where their notifications go</p>
                                    </div>
                                    <Button
                                        leftIcon={<Plus className="w-4 h-4" />}
                                        onClick={onAdd}
                                        className="w-full sm:w-auto bg-gradient-to-r from-primary-500 to-primary-600 hover:from-primary-400 hover:to-primary-500 shadow-lg shadow-primary-500/25"
                                    >
                                        Add Department
                                    </Button>
                                </div>

                                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                                    <div className="p-4 rounded-2xl bg-gradient-to-br from-blue-500/10 to-blue-600/5 border border-blue-500/20 backdrop-blur-sm shadow-xl">
                                        <div className="flex items-center gap-3.5">
                                            <div className="w-10 h-10 rounded-xl bg-blue-500/20 flex items-center justify-center shrink-0">
                                                <Building2 className="w-5 h-5 text-blue-400" />
                                            </div>
                                            <div>
                                                <p className="text-2xl font-bold text-white">{departments.length}</p>
                                                <p className="text-xs text-blue-300/70">Departments</p>
                                            </div>
                                        </div>
                                    </div>
                                    <div className="p-4 rounded-2xl bg-gradient-to-br from-emerald-500/10 to-emerald-600/5 border border-emerald-500/20 backdrop-blur-sm shadow-xl">
                                        <div className="flex items-center gap-3.5">
                                            <div className="w-10 h-10 rounded-xl bg-emerald-500/20 flex items-center justify-center shrink-0">
                                                <Mail className="w-5 h-5 text-emerald-400" />
                                            </div>
                                            <div>
                                                <p className="text-2xl font-bold text-white">{departments.filter(d => d.routing_email).length}</p>
                                                <p className="text-xs text-emerald-300/70">With routing email</p>
                                            </div>
                                        </div>
                                    </div>
                                    {/* A department with no email is not broken --
                                        reports still route to it in the console --
                                        but nobody is told, so it is worth counting. */}
                                    <div className="p-4 rounded-2xl bg-gradient-to-br from-amber-500/10 to-amber-600/5 border border-amber-500/20 backdrop-blur-sm shadow-xl">
                                        <div className="flex items-center gap-3.5">
                                            <div className="w-10 h-10 rounded-xl bg-amber-500/20 flex items-center justify-center shrink-0">
                                                <AlertCircle className="w-5 h-5 text-amber-400" />
                                            </div>
                                            <div>
                                                <p className="text-2xl font-bold text-white">{departments.filter(d => !d.routing_email).length}</p>
                                                <p className="text-xs text-amber-300/70">No email set</p>
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                                    {departments.map((dept) => (
                                        /* Same card as a service category: the
                                         * 3xl radius, the glow bar, the lift on
                                         * hover, badges, and an action footer
                                         * that is always visible. The previous
                                         * version hid Edit and Delete behind
                                         * hover, which put them out of reach of
                                         * anyone using a keyboard and made the
                                         * two pages read as unrelated. */
                                        <div key={dept.id} className="group">
                                            <div className="surface-card relative p-6 rounded-3xl bg-gradient-to-br from-white/[0.08] via-white/[0.03] to-indigo-950/30 border border-white/15 backdrop-blur-2xl shadow-[0_10px_30px_rgba(0,0,0,0.3)] hover:shadow-[0_20px_50px_rgba(99,102,241,0.2)] hover:border-primary-400/50 hover:-translate-y-1 transition-all duration-300">
                                                <div className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-transparent via-primary-400/40 to-transparent rounded-t-3xl" />

                                                <div className="flex items-center gap-3 mb-4">
                                                    <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-primary-500/25 via-indigo-500/20 to-purple-500/15 border border-white/20 shadow-inner flex items-center justify-center text-primary-300 shrink-0">
                                                        <Building2 className="w-6 h-6" />
                                                    </div>
                                                    <h3 className="font-bold text-white text-lg tracking-tight group-hover:text-primary-200 transition-colors truncate">
                                                        {dept.name}
                                                    </h3>
                                                </div>

                                                <p className="text-xs text-white/65 leading-relaxed min-h-[32px] line-clamp-2">
                                                    {dept.description || 'No description provided.'}
                                                </p>

                                                <div className="flex items-center gap-2 mt-4 flex-wrap">
                                                    {dept.routing_email ? (
                                                        <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-2xl text-xs font-medium bg-white/10 border border-white/15 text-white/90 max-w-full">
                                                            <Mail className="w-3.5 h-3.5 text-white/40 shrink-0" aria-hidden="true" />
                                                            <span className="font-mono truncate">{dept.routing_email}</span>
                                                        </span>
                                                    ) : (
                                                        /* Not a failure -- reports still route to the
                                                         * department in the console -- but nobody is
                                                         * emailed, and that is worth saying on the card
                                                         * rather than leaving to be discovered. */
                                                        <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-2xl text-xs font-semibold bg-gradient-to-r from-amber-500/20 to-orange-500/20 text-amber-300 border border-amber-500/30 shadow-md shadow-amber-950/40">
                                                            <AlertCircle className="w-3.5 h-3.5" aria-hidden="true" />
                                                            No routing email
                                                        </span>
                                                    )}
                                                </div>

                                                <div className="flex items-center justify-end gap-2.5 mt-5 pt-4 border-t border-white/10">
                                                    <Button
                                                        leftIcon={<Edit className="w-4 h-4" />}
                                                        onClick={() => onEdit(dept)}
                                                        className="rounded-2xl px-5"
                                                    >
                                                        Edit Department
                                                    </Button>
                                                    <button
                                                        onClick={() => onDelete(dept.id)}
                                                        className="p-2.5 rounded-2xl bg-white/5 hover:bg-red-500/20 border border-white/10 hover:border-red-500/30 text-white/40 hover:text-red-300 transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-red-400/60"
                                                        aria-label={`Delete ${dept.name} department`}
                                                    >
                                                        <Trash2 className="w-4 h-4" aria-hidden="true" />
                                                    </button>
                                                </div>
                                            </div>
                                        </div>
                                    ))}
                                </div>

                                {departments.length === 0 && (
                                    <div className="premium-card text-center py-10 px-6">
                                        <Building2 className="w-12 h-12 mx-auto text-white/20 mb-3" />
                                        <p className="text-white/60">No departments yet.</p>
                                        <p className="text-white/40 text-sm mt-1">
                                            Add one to organise staff and route reports to the right team.
                                        </p>
                                    </div>
                                )}
                            </div>
    );
}


export default function AdminConsole() {
    const navigate = useNavigate();
    const { user, logout } = useAuth();
    const { settings, refreshSettings } = useSettings();
    const dialog = useDialog();

    const [sidebarOpen, setSidebarOpen] = useState(false);
    const [currentTab, setCurrentTab] = useState<Tab>('branding');
    const [isLoading, setIsLoading] = useState(false);
    const [saveMessage, setSaveMessage] = useState<string | null>(null);
    const [errorMessage, setErrorMessage] = useState<string | null>(null);

    /** Surface a failed action instead of only console.error-ing it.
     *
     * The API client puts the server's `detail` into Error.message, and that
     * detail is usually the actionable sentence -- "Override must be at least
     * 365 days (1 year)", "Unknown state code: ZZ", "managed by your state and
     * can't be changed here". Losing it left the admin pressing a button that
     * appeared to do nothing. */
    const reportError = (context: string, err: unknown) => {
        console.error(`${context}:`, err);
        const detail = err instanceof Error ? err.message : String(err ?? '');
        setErrorMessage(detail ? `${context}: ${detail}` : `${context}. Please try again.`);
        setTimeout(() => setErrorMessage(null), 12000);
    };
    const contentRef = useRef<HTMLDivElement>(null);

    // URL hashing, dynamic titles, and scroll-to-top
    const { updateHash, updateTitle, scrollToTop } = usePageNavigation({
        baseTitle: settings?.township_name ? `Admin Console | ${settings.township_name}` : 'Admin Console',
        scrollContainerRef: contentRef,
    });

    // Update hash and title when tab changes
    useEffect(() => {
        updateHash(currentTab);
        const tabTitles: Record<Tab, string> = {
            branding: 'Branding',
            users: 'User Management',
            departments: 'Departments',
            services: 'Service Categories',
            integration: 'Setup & Integration',
            system: 'System Settings',
            health: 'System Health',
            compliance: 'Compliance',
        };
        updateTitle(tabTitles[currentTab]);
        scrollToTop('instant');
    }, [currentTab, updateHash, updateTitle, scrollToTop]);

    // Branding state
    const [brandingForm, setBrandingForm] = useState<Partial<SystemSettings>>({});

    // Users state
    const [users, setUsers] = useState<User[]>([]);
    const [showUserModal, setShowUserModal] = useState(false);
    const [newUser, setNewUser] = useState({
        username: '',
        email: '',
        full_name: '',
        role: 'staff' as 'staff' | 'admin',
        department_ids: [] as number[],
    });

    /* Editing an existing staff member.
     *
     * Separate from newUser rather than reusing it: the two are genuinely
     * different forms. Creating needs a username and a password; editing must
     * not offer either, because the username is what the audit log and the
     * identity provider key off -- renaming it orphans history instead of
     * correcting it -- and passwords have their own reset flow. */
    const [editingUser, setEditingUser] = useState<User | null>(null);
    const [editUser, setEditUser] = useState({
        email: '',
        full_name: '',
        phone: '',
        role: 'staff' as 'staff' | 'admin' | 'researcher',
        is_active: true,
        department_ids: [] as number[],
    });

    const openEditUser = (u: User) => {
        setEditingUser(u);
        setEditUser({
            email: u.email || '',
            full_name: u.full_name || '',
            phone: (u as any).phone || '',
            role: (u.role as 'staff' | 'admin' | 'researcher') || 'staff',
            is_active: (u as any).is_active !== false,
            department_ids: (u.departments || []).map(d => d.id),
        });
    };

    const handleUpdateUser = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!editingUser) return;
        try {
            await api.updateUser(editingUser.id, {
                email: editUser.email,
                full_name: editUser.full_name,
                phone: editUser.phone,
                role: editUser.role,
                is_active: editUser.is_active,
                department_ids: editUser.department_ids,
            });
            setEditingUser(null);
            loadTabData();
        } catch (err: any) {
            alert(err?.message || 'Could not save changes');
        }
    };

    // Services state
    const [services, setServices] = useState<ServiceDefinition[]>([]);
    const [departments, setDepartments] = useState<Department[]>([]);
    const [showServiceModal, setShowServiceModal] = useState(false);
    const [editingService, setEditingService] = useState<ServiceDefinition | null>(null);
    const [newService, setNewService] = useState({
        service_code: '',
        service_name: '',
        description: '',
        icon: 'AlertCircle',
    });

    // Service routing edit state
    const [showServiceEditModal, setShowServiceEditModal] = useState(false);
    const [serviceRouting, setServiceRouting] = useState({
        routing_mode: 'township' as 'township' | 'third_party' | 'road_based',
        assigned_department_id: null as number | null,
        icon: 'AlertCircle',
        // Optional SLA target in hours; '' means no SLA for this category.
        sla_hours: '' as string,
        routing_config: {
            // Township mode
            route_to: 'all_staff' as 'all_staff' | 'specific_staff',
            staff_ids: [] as number[],
            // Third party mode
            message: '',
            contacts: [] as RoutingContact[],
            // Road-based mode
            // 'township', or a configured agency's name. Not a closed union:
            // narrowing it here is what forced the `as any` that hid the value
            // never matching an agency.
            default_handler: 'township' as string,
            exclusion_list: '', // County roads (when township is default)
            inclusion_list: '', // Township roads (when third party is default)
            third_party_message: '',
            // The shared shape, so the admin state, the API type and the
            // resident-facing notice cannot drift apart again.
            third_party_contacts: [] as RoutingContact[],
            // Custom questions
            custom_questions: [] as { id: string; label: string; type: string; options: string[]; required: boolean; placeholder: string }[],
        },
    });

    // Conflicts in the road rules, checked live. The one that matters most is a
    // road matching nothing: a typo produces a rule that fires never, and until
    // now there was no indication of it anywhere.
    const [routingIssues, setRoutingIssues] = useState<{
        severity: 'error' | 'warning' | 'info'; kind: string; message: string; roads: string[];
    }[]>([]);
    const [routingCanSave, setRoutingCanSave] = useState(true);
    // Stretches the clerk switched off, keyed to the publisher's feature ids so
    // a monthly data refresh keeps the corrections while still picking up newly
    // built sections of the same road.
    const [excludedSegments, setExcludedSegments] = useState<string[]>([]);
    const [corridorMetres, setCorridorMetres] = useState(20);
    // Partial coverage per stretch, as fractions of its length -- see
    // RoadCorridorMap for why fractions rather than coordinates.
    const [segmentTrims, setSegmentTrims] = useState<Record<string, { start: number; end: number }>>({});

    useEffect(() => {
        if (serviceRouting.routing_mode !== 'road_based') {
            setRoutingIssues([]);
            setRoutingCanSave(true);
            return;
        }
        const split = (v: string) => v.split(',').map(r => r.trim()).filter(Boolean);
        const timer = setTimeout(() => {
            api.checkRoutingConfig({
                default_handler: serviceRouting.routing_config.default_handler,
                exclusion_list: split(serviceRouting.routing_config.exclusion_list),
                inclusion_list: split(serviceRouting.routing_config.inclusion_list),
            })
                .then(result => { setRoutingIssues(result.issues); setRoutingCanSave(result.can_save); })
                // A check that cannot run must not lock a clerk out of saving.
                .catch(() => { setRoutingIssues([]); setRoutingCanSave(true); });
        }, 350);
        return () => clearTimeout(timer);
    }, [
        serviceRouting.routing_mode,
        serviceRouting.routing_config.default_handler,
        serviceRouting.routing_config.exclusion_list,
        serviceRouting.routing_config.inclusion_list,
    ]);

    // Department management state
    const [showDepartmentModal, setShowDepartmentModal] = useState(false);
    const [editingDepartment, setEditingDepartment] = useState<Department | null>(null);
    const [newDepartment, setNewDepartment] = useState({
        name: '',
        description: '',
        routing_email: '',
    });

    // Secrets state
    const [secrets, setSecrets] = useState<SystemSecret[]>([]);


    /* Modules state: product features with nothing to configure.
     *
     * `ai_analysis`, `sms_alerts` and `email_notifications` used to be here.
     * They were a second answer to a question the setup page also owned -- and
     * for email and SMS a third, because dispatch read EMAIL_ENABLED and
     * SMS_ENABLED as well. Three switches for one capability is how a town
     * could turn texting off in one place and have it keep sending from
     * another. They live in `capability_switches` now, with the ticks in Setup
     * Instructions; what is left here has no provider, no credentials and
     * nothing to switch off at the dispatch layer. */
    const [modules, setModules] = useState({ research_portal: false, unlisted_reports: false });
    // Town-wide public archival policy, as typed. Held as a string because the
    // input is a text field and "" is a meaningful value: it is how an admin
    // clears the policy, and the server reads "" and 0 as "keep everything".
    const [publicArchiveDays, setPublicArchiveDays] = useState<string>('');
    // Research pack switches: catalog (labels, field lists) from the server so
    // the "contains:" disclosure can never disagree with the export, plus the
    // editable on/off state saved alongside `modules`.
    const [researchPacks, setResearchPacks] = useState<ResearchPackSwitch[]>([]);
    const [packSwitches, setPackSwitches] = useState<Record<string, boolean>>({});

    // Maps tab state
    const [mapsRaw, setMapsRaw] = useState<RawMapsConfig | null>(null);
    const mapConfig = useMemo(() => resolveMapProviderConfig(mapsRaw), [mapsRaw]);
    const mapsReady = mapProviderReady(mapsRaw);
    const [townshipSearch, setTownshipSearch] = useState('');
    const [osmSearchResults, setOsmSearchResults] = useState<Array<{
        osm_id: number;
        display_name: string;
        type: string;
        class: string;
        lat: string;
        lon: string;
    }>>([]);
    const [selectedOsmResult, setSelectedOsmResult] = useState<{
        osm_id: number;
        display_name: string;
        lat: string;
        lon: string;
        geojson?: object;  // Boundary GeoJSON from Nominatim
    } | null>(null);
    const [townshipBoundary, setTownshipBoundary] = useState<object | null>(null);


    const [isSearchingTownship, setIsSearchingTownship] = useState(false);
    const [isFetchingBoundary, setIsFetchingBoundary] = useState(false);


    // Custom map layers state
    const [mapLayers, setMapLayers] = useState<MapLayer[]>([]);
    const [showLayerModal, setShowLayerModal] = useState(false);
    const [editingLayer, setEditingLayer] = useState<MapLayer | null>(null);
    const [newLayer, setNewLayer] = useState({
        name: '',
        description: '',
        layer_type: '' as '' | 'point' | 'polygon', // User must select first
        fill_color: '#3b82f6',
        stroke_color: '#1d4ed8',
        fill_opacity: 0.3,
        stroke_width: 2,
        service_codes: [] as string[],
        geojson: null as object | null,
        // Polygon routing options
        routing_mode: 'log' as 'log' | 'block', // log=log in report, block=redirect to third party
        routing_config: null as { message?: string; contacts?: { name: string; phone: string; url: string }[] } | null,
        visible_on_map: true, // Whether to show the layer visually on the map
    });
    // Nominatim search state for polygon boundaries
    const [nominatimSearch, setNominatimSearch] = useState('');
    // Shaped by /gis/osm/search, which already filters to OSM relations and
    // attaches each boundary's geometry. There is no osm_type here because the
    // backend only returns relations -- a township boundary is always one.
    const [nominatimResults, setNominatimResults] = useState<{
        display_name: string; osm_id: number; geojson?: object;
    }[]>([]);
    const [isSearchingNominatim, setIsSearchingNominatim] = useState(false);


    // SSO users don't have passwords - authentication handled by Auth0


    // SSO users don't have passwords - authentication handled by Auth0

    // Document Retention state
    /* There is no list of states any more. It populated a picker that looked up
       a retention period in a table covering all 51 US jurisdictions — 41 of
       them five years, each citing a different state records authority as its
       source — which nobody had verified. Every municipality has a retention
       schedule approved by its state archives, and its clerk holds it. */
    /* The server's type, not a second copy of it. The local shape declared
       `policy` and `stats` as always present, which is exactly what stopped
       being true: a town that has not set a period has no schedule at all, and
       the screen must say so rather than render something filled in for it. */
    const [retentionPolicy, setRetentionPolicy] =
        useState<import('../services/api').RetentionPolicyConfig | null>(null);
    /* The policy when there actually is one, with the pieces the "Current
       Policy" panel needs proven present rather than assumed. The panel states
       a period and a count of records due for destruction, neither of which
       exists until this town says so. */
    const activeRetention = (retentionPolicy?.configured && retentionPolicy.retention_days && retentionPolicy.stats)
        ? {
            ...retentionPolicy,
            retention_days: retentionPolicy.retention_days,
            stats: retentionPolicy.stats,
        }
        : null;
    const [selectedMode, setSelectedMode] = useState<'redact' | 'purge'>('redact');
    /* The records the next run would touch. Loaded on demand: it is a real
       query and most visits to this page are not about to press the button. */
    const [retentionPreview, setRetentionPreview] = useState<import('../services/api').RetentionPreview | null>(null);
    const [previewLoading, setPreviewLoading] = useState(false);

    /* What a records custodian is releasing. Loaded from the server so the
       picker, the export and the audit entry all describe one catalog.
       Named for what it does rather than for one state's statute: this answers
       a records request under whatever law the town is subject to, and the
       product does not know which one that is. */
    const [exportOpen, setExportOpen] = useState(false);
    const [exportFields, setExportFields] = useState<import('../services/api').PublicRecordsField[]>([]);
    const [exportChosen, setExportChosen] = useState<Set<string>>(new Set());
    const [exportStart, setExportStart] = useState('');
    const [exportEnd, setExportEnd] = useState('');
    const [exportStatuses, setExportStatuses] = useState<Set<string>>(new Set());
    const [exportIds, setExportIds] = useState('');
    const [exportBusy, setExportBusy] = useState(false);
    const [previewError, setPreviewError] = useState<string | null>(null);

    /* Which fields a retention run clears. Null until the server answers --
     * rendering the catalog from a hardcoded copy here is how the screen and
     * the thing it configures drift apart. */
    const [scrubFields, setScrubFields] = useState<import('../services/api').ScrubField[] | null>(null);
    /* How long a closed request is kept. Blank until the town types a number:
       there is no schedule to pre-fill it from, and a pre-filled number here
       is a destruction date nobody chose. */
    const [retentionDays, setRetentionDays] = useState<string>('');
    const [isSavingRetention, setIsSavingRetention] = useState(false);
    const [isRunningRetention, setIsRunningRetention] = useState(false);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const [deletedRequests, setDeletedRequests] = useState<any[]>([]);

    // Legal hold modal state
    const [showLegalHoldModal, setShowLegalHoldModal] = useState(false);
    const [legalHoldRequests, setLegalHoldRequests] = useState<Array<{
        id: number;
        service_request_id: string;
        service_name: string;
        description: string;
        status: string;
        address: string;
        requested_datetime: string;
        closed_datetime: string | null;
    }>>([]);
    const [isLoadingLegalHold, setIsLoadingLegalHold] = useState(false);

    // "Eligible for Archival" is a number nobody can check. A count of records
    // about to be redacted or purged is only trustworthy if you can see which
    // records, the same way the legal-hold count opens the list it counts.
    const [showEligibleModal, setShowEligibleModal] = useState(false);
    const [eligiblePreview, setEligiblePreview] = useState<import('../services/api').RetentionPreview | null>(null);
    const [eligibleError, setEligibleError] = useState<string | null>(null);
    const [isLoadingEligible, setIsLoadingEligible] = useState(false);

    const openEligibleRecords = async () => {
        setIsLoadingEligible(true);
        setEligibleError(null);
        setShowEligibleModal(true);
        try {
            setEligiblePreview(await api.previewRetentionRun(200));
        } catch (err) {
            setEligiblePreview(null);
            setEligibleError(err instanceof Error ? err.message : 'Could not load the eligible records.');
        } finally {
            setIsLoadingEligible(false);
        }
    };

    // Backup management state
    const [backupStatus, setBackupStatus] = useState<{
        configured: boolean;
        message?: string;
        bucket?: string;
        last_backup?: { name: string; size_bytes: number; created_at: string; age_days: number } | null;
        total_backups?: number;
        next_scheduled?: string;
        required_secrets?: string[];
    } | null>(null);
    const [backups, setBackups] = useState<Array<{
        name: string;
        size_bytes: number;
        created_at: string;
        age_days: number;
    }>>([]);
    const [isLoadingBackups, setIsLoadingBackups] = useState(false);
    const [isCreatingBackup, setIsCreatingBackup] = useState(false);

    useEffect(() => {
        if (settings) {
            setBrandingForm({
                township_name: settings.township_name,
                logo_url: settings.logo_url || '',
                favicon_url: settings.favicon_url || '',
                hero_text: settings.hero_text,
                primary_color: settings.primary_color,
                social_links: settings.social_links || [],
            });
            setModules({
                research_portal: settings.modules?.research_portal || false,
                unlisted_reports: settings.modules?.unlisted_reports ?? (settings.modules as any)?.private_reports ?? false,
            });
            setPublicArchiveDays(settings.public_archive_days ? String(settings.public_archive_days) : '');
        }
    }, [settings]);

    useEffect(() => {
        // Pack toggles render regardless of whether the portal module is on —
        // an admin decides what a town releases BEFORE enabling the portal.
        api.getResearchPacks()
            .then((packs) => {
                setResearchPacks(packs);
                setPackSwitches(Object.fromEntries(packs.map((p) => [p.id, p.enabled])));
            })
            .catch((err) => console.error('Failed to load research pack switches:', err));
    }, []);

    useEffect(() => {
        // Always load maps config & township boundary so map features work on any tab
        api.getMapsConfig().then(mapsConfig => {
            setMapsRaw(mapsConfig);
            if (mapsConfig.township_boundary) setTownshipBoundary(mapsConfig.township_boundary);
        }).catch(err => console.warn("Maps config load warning:", err));
    }, []);

    /* A fresh install lands on the setup guide rather than on Branding.
     *
     * Nothing detected one. `SetupIntegrationsPage` opens its guide when setup
     * has not been marked finished -- but it is a tab, and the console opens on
     * Branding, so on a brand new deployment the guide sat behind a click
     * nobody had a reason to make. The first thing a town needs is the thing it
     * was least likely to find.
     *
     * An explicit hash wins. Somebody who typed or was sent `#compliance` asked
     * for compliance, and a redirect over the top of that is the console
     * ignoring a link.
     */
    useEffect(() => {
        if (window.location.hash.slice(1)) return;
        let cancelled = false;
        api.getSetupState()
            .then(state => { if (!cancelled && !state.completed) setCurrentTab('integration'); })
            // Unknown is not "unfinished". A failed request must not move
            // somebody off the tab they opened the console to use.
            .catch(() => undefined);
        return () => { cancelled = true; };
    }, []);

    useEffect(() => {
        loadTabData();
    }, [currentTab]);

    const loadTabData = async () => {
        setIsLoading(true);
        try {
            switch (currentTab) {
                case 'users':
                    const [usersData, userDepts] = await Promise.all([
                        api.getUsers(),
                        api.getDepartments(),
                    ]);
                    setUsers(usersData);
                    setDepartments(userDepts);
                    break;
                case 'departments':
                    const deptsOnly = await api.getDepartments();
                    setDepartments(deptsOnly);
                    break;
                case 'services':
                    const [servicesData, deptsData] = await Promise.all([
                        api.getServices(),
                        api.getDepartments(),
                    ]);
                    setServices(servicesData);
                    setDepartments(deptsData);
                    break;
                case 'integration':
                    // First sync to ensure all default secrets exist
                    try { await api.syncSecrets(); } catch { /* ignore sync errors */ }
                    const secretsData = await api.getSecrets();
                    setSecrets(secretsData);
                    break;
                case 'system':
                    // Load Maps configuration
                    try {
                        const mapsConfig = await api.getMapsConfig();
                        setMapsRaw(mapsConfig);
                        if (mapsConfig.township_boundary) {
                            setTownshipBoundary(mapsConfig.township_boundary);
                        }
                        // Load custom map layers
                        const layers = await api.getAllMapLayers();
                        setMapLayers(layers);
                    } catch (err) {
                        console.error('Failed to load Maps config:', err);
                    }
                    break;
                case 'compliance':
                    try {
                        const [policy, allRequests] = await Promise.all([
                            api.getRetentionPolicy(),
                            api.getRequests(undefined, true) // include_deleted=true for admins
                        ]);
                        setRetentionPolicy(policy);
                        setSelectedMode(policy.mode === 'purge' ? 'purge' : 'redact');
                        setScrubFields(policy.scrub_fields || null);
                        setRetentionDays(policy.retention_days ? String(policy.retention_days) : '');
                        // Filter for deleted requests only
                        const deleted = allRequests.filter((r: { deleted_at?: string | null }) => r.deleted_at != null);
                        setDeletedRequests(deleted);
                    } catch (err) {
                        console.error('Failed to load retention config:', err);
                    }
                    break;

            }

        } catch (err) {
            console.error('Failed to load data:', err);
        } finally {
            setIsLoading(false);
        }
    };

    const handleSaveBranding = async () => {
        setIsLoading(true);
        try {
            await api.updateSettings(brandingForm);
            setSaveMessage('Settings saved!');
            setTimeout(() => setSaveMessage(''), 3000);
        } catch (err) {
            reportError('Could not save branding', err);
        } finally {
            setIsLoading(false);
        }
    };


    // OSM Search and Boundary handlers for Maps tab
    const handleOsmSearch = async () => {
        if (!townshipSearch.trim()) return;

        setIsSearchingTownship(true);
        setOsmSearchResults([]);
        setSelectedOsmResult(null);

        try {
            const response = await api.searchOsmTownship(townshipSearch);
            setOsmSearchResults(response.results);

            if (response.results.length === 0) {
                alert("No matching municipalities found. Try a different search term.");
            }
        } catch (err) {
            console.error('OSM search failed:', err);
            alert("Failed to search for municipality");
        } finally {
            setIsSearchingTownship(false);
        }
    };

    const handleFetchBoundary = async () => {
        if (!selectedOsmResult) return;

        setIsFetchingBoundary(true);

        try {
            // Use GeoJSON from Nominatim search result if available (polygon_geojson=1)
            // Otherwise fall back to fetching from polygons.openstreetmap.fr
            let geojson = selectedOsmResult.geojson;

            if (!geojson) {
                // Fallback to old method if Nominatim didn't return geojson
                const response = await api.fetchOsmBoundary(selectedOsmResult.osm_id);
                geojson = response.geojson;
            }

            if (!geojson) {
                alert("No boundary data available for this location.");
                return;
            }

            // Save the boundary with center coordinates from Nominatim
            const centerLat = parseFloat(selectedOsmResult.lat);
            const centerLng = parseFloat(selectedOsmResult.lon);
            await api.saveTownshipBoundary(geojson, selectedOsmResult.display_name, centerLat, centerLng);
            try { await api.seedRoads(true); } catch (e) { console.warn("Road seed failed:", e); }

            setTownshipBoundary(geojson);
            setSelectedOsmResult(null);
            setSaveMessage('Municipality boundary saved successfully!');
            setTimeout(() => setSaveMessage(null), 3000);
        } catch (err) {
            console.error('Failed to fetch boundary:', err);
            alert("Failed to fetch boundary. The boundary may not be available for this location.");
        } finally {
            setIsFetchingBoundary(false);
        }
    };



    const handleCreateUser = async (e: React.FormEvent) => {
        e.preventDefault();
        try {
            // Clean up data - send proper format for SSO users (no password needed)
            const userData = {
                username: newUser.username,
                email: newUser.email,
                role: newUser.role,
                full_name: newUser.full_name || undefined,
                department_ids: newUser.department_ids.length > 0 ? newUser.department_ids : undefined,
            };
            await api.createUser(userData as any);
            setShowUserModal(false);
            setNewUser({ username: '', email: '', full_name: '', role: 'staff', department_ids: [] });
            loadTabData();
        } catch (err: any) {
            console.error('Failed to create user:', err);
            alert(err.message || 'Failed to create user');
        }
    };

    const handleDeleteUser = async (userId: number) => {
        const confirmed = await dialog.confirm({
            title: 'Delete User',
            message: 'Are you sure you want to delete this user?\n\nThis action cannot be undone.',
            variant: 'danger',
            confirmText: 'Delete',
        });
        if (!confirmed) return;
        try {
            await api.deleteUser(userId);
            loadTabData();
        } catch (err) {
            reportError('Could not delete the user', err);
        }
    };

    const handleCreateService = async (e: React.FormEvent) => {
        e.preventDefault();
        try {
            await api.createService(newService);
            setShowServiceModal(false);
            setNewService({ service_code: '', service_name: '', description: '', icon: 'AlertCircle' });
            loadTabData();
        } catch (err) {
            reportError('Could not create the service category', err);
        }
    };

    const handleDeleteService = async (serviceId: number) => {
        const confirmed = await dialog.confirm({
            title: 'Delete Service',
            message: 'Are you sure you want to delete this service?\n\nThis action cannot be undone.',
            variant: 'danger',
            confirmText: 'Delete',
        });
        if (!confirmed) return;
        try {
            await api.deleteService(serviceId);
            loadTabData();
        } catch (err) {
            reportError('Could not delete the service category', err);
        }
    };

    const handleEditService = (service: ServiceDefinition) => {
        // Load users for staff selection
        api.getUsers().then(setUsers).catch(console.error);
        // Load departments for department selection
        api.getDepartments().then(setDepartments).catch(console.error);

        setEditingService(service);
        const config = service.routing_config || {};
        setServiceRouting({
            routing_mode: service.routing_mode || 'township',
            assigned_department_id: service.assigned_department_id || null,
            icon: service.icon || 'AlertCircle',
            sla_hours: service.sla_hours ? String(service.sla_hours) : '',
            routing_config: {
                // Township mode
                route_to: config.route_to || 'all_staff',
                staff_ids: config.staff_ids || [],
                // Third party mode
                message: config.message || '',
                contacts: config.contacts || [],
                // Road-based mode
                default_handler: config.default_handler || 'township',
                exclusion_list: Array.isArray(config.exclusion_list) ? config.exclusion_list.join(', ') : '',
                inclusion_list: Array.isArray(config.inclusion_list) ? config.inclusion_list.join(', ') : '',
                third_party_message: config.third_party_message || '',
                third_party_contacts: config.third_party_contacts || [],
                // Custom questions
                custom_questions: (config.custom_questions || []).map(q => ({
                    id: q.id || crypto.randomUUID(),
                    label: q.label || '',
                    type: q.type || 'text',
                    options: q.options || [],
                    required: q.required || false,
                    placeholder: q.placeholder || '',
                })),
            },
        });
        // Restore the clerk's per-rule corrections alongside the config itself.
        setExcludedSegments(Array.isArray(config.excluded_segments) ? config.excluded_segments : []);
        setCorridorMetres(typeof config.corridor_metres === 'number' ? config.corridor_metres : 20);
        setSegmentTrims(
            config.segment_trims && typeof config.segment_trims === 'object' ? config.segment_trims : {},
        );
        setShowServiceEditModal(true);
    };

    const handleSaveServiceRouting = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!editingService) return;
        // A road assigned to two agencies would route to whichever was checked
        // first, silently. Refuse rather than save something ambiguous.
        if (!routingCanSave) {
            await dialog.alert({
                title: 'Fix the routing conflicts',
                message: 'A road is assigned to more than one agency. Reports on it would route unpredictably.',
            });
            return;
        }

        try {
            const config: Record<string, any> = {};

            if (serviceRouting.routing_mode === 'township') {
                config.route_to = serviceRouting.routing_config.route_to;
                config.staff_ids = serviceRouting.routing_config.staff_ids;
            } else if (serviceRouting.routing_mode === 'third_party') {
                config.message = serviceRouting.routing_config.message;
                config.contacts = serviceRouting.routing_config.contacts;
            } else if (serviceRouting.routing_mode === 'road_based') {
                config.default_handler = serviceRouting.routing_config.default_handler;
                const rawEx = serviceRouting.routing_config.exclusion_list;
                if (Array.isArray(rawEx)) {
                    config.exclusion_list = rawEx;
                } else if (typeof rawEx === "string") {
                    config.exclusion_list = rawEx.split(",").map((r: string) => r.trim()).filter(Boolean);
                } else {
                    config.exclusion_list = [];
                }

                const rawIn = serviceRouting.routing_config.inclusion_list;
                if (Array.isArray(rawIn)) {
                    config.inclusion_list = rawIn;
                } else if (typeof rawIn === "string") {
                    config.inclusion_list = rawIn.split(",").map((r: string) => r.trim()).filter(Boolean);
                } else {
                    config.inclusion_list = [];
                }
                // Clerk corrections travel with the rule, keyed to publisher
                // feature ids so a data refresh cannot orphan them.
                config.excluded_segments = excludedSegments;
                config.segment_trims = segmentTrims;
                config.corridor_metres = corridorMetres;
                config.third_party_message = serviceRouting.routing_config.third_party_message;
                config.third_party_contacts = serviceRouting.routing_config.third_party_contacts;
                // Specific-person routing for the roads the municipality handles —
                // resolved the same way as Municipality mode (route_to + staff_ids).
                config.route_to = serviceRouting.routing_config.route_to;
                config.staff_ids = serviceRouting.routing_config.staff_ids;
            }

            // Always include custom questions
            config.custom_questions = serviceRouting.routing_config.custom_questions.filter(q => q.label.trim());

            await api.updateService(editingService.id, {
                routing_mode: serviceRouting.routing_mode,
                routing_config: config,
                assigned_department_id: serviceRouting.assigned_department_id || undefined,
                icon: serviceRouting.icon,
                // 0 explicitly clears the SLA; a value sets it.
                sla_hours: serviceRouting.sla_hours ? parseInt(serviceRouting.sla_hours) : 0,
            });

            setShowServiceEditModal(false);
            setEditingService(null);
            loadTabData();
            setSaveMessage('Service routing updated!');
            setTimeout(() => setSaveMessage(''), 3000);
        } catch (err: any) {
            console.error('Failed to update service:', err);
            alert(err.message || 'Failed to update service');
        }
    };

    // Department handlers
    const handleCreateDepartment = async (e: React.FormEvent) => {
        e.preventDefault();
        try {
            if (editingDepartment) {
                await api.updateDepartment(editingDepartment.id, newDepartment);
            } else {
                await api.createDepartment(newDepartment);
            }
            setShowDepartmentModal(false);
            setEditingDepartment(null);
            setNewDepartment({ name: '', description: '', routing_email: '' });
            loadTabData();
        } catch (err) {
            console.error('Failed to save department:', err);
        }
    };

    const handleEditDepartment = (dept: Department) => {
        setEditingDepartment(dept);
        setNewDepartment({
            name: dept.name,
            description: dept.description || '',
            routing_email: dept.routing_email || '',
        });
        setShowDepartmentModal(true);
    };

    const handleDeleteDepartment = async (deptId: number) => {
        const confirmed = await dialog.confirm({
            title: 'Delete Department',
            message: 'Are you sure you want to delete this department?\n\nThis action cannot be undone.',
            variant: 'danger',
            confirmText: 'Delete',
        });
        if (!confirmed) return;
        try {
            await api.deleteDepartment(deptId);
            loadTabData();
        } catch (err) {
            console.error('Failed to delete department:', err);
        }
    };


    const handleSaveSecretDirect = async (keyName: string, value: string) => {
        try {
            await api.updateSecret(keyName, value);
            loadTabData();
        } catch (err) {
            console.error('Failed to update secret:', err);
        }
    };

    const handleSaveModules = async () => {
        setIsLoading(true);
        try {
            // Pack switches ride with the module flags — one Save button for
            // the whole screen. But ONLY once they have actually loaded:
            // `packSwitches` starts {} until GET /research/packs resolves, and
            // sending {} persists "every pack back to its default" — a failed
            // or slow fetch plus one unrelated module save silently re-enabled
            // packs an admin had deliberately switched off. An absent key is
            // the server's "leave them as they are".
            const payload: Record<string, unknown> = { modules };
            if (Object.keys(packSwitches).length > 0) payload.research_packs = packSwitches;
            // Always sent, including as '' -- unlike the pack switches, an empty
            // box here is a deliberate answer ("keep everything listed") and not
            // a fetch that has yet to resolve. The server normalises '' and 0
            // to "no policy".
            payload.public_archive_days = publicArchiveDays === '' ? null : Number(publicArchiveDays);
            await api.updateSettings(payload);
            await refreshSettings();
            setSaveMessage('Modules saved successfully');
            setTimeout(() => setSaveMessage(null), 3000);
        } catch (err) {
            reportError('Could not save module settings', err);
        } finally {
            setIsLoading(false);
        }
    };

    const handleLogout = () => {
        logout();
        navigate('/login');
    };

    // Note: Password reset functionality removed - using Auth0 SSO

    // Tabs are now rendered using SidebarGroup/SidebarItem components in the sidebar

    return (
        <div className="min-h-screen flex">
            {/* The one voluntary outbound call this application makes. Gated on
              * a configured deployment rather than on sign-in: `township_name`
              * only leaves its placeholder once somebody has been through
              * branding, which is the end of installation, and asking for a
              * contact address in the middle of installing is asking at the
              * worst possible moment. Never shown on the setup tab for the same
              * reason. See components/StayInformed.tsx. */}
            <StayInformedHost
                ready={Boolean(settings?.township_name)
                    && settings?.township_name !== 'Your Municipality'
                    && currentTab !== 'integration'}
                /* What the console already knows, so a registration form does
                 * not ask for it again. Which of these actually travel is the
                 * operator's choice, made by which {tokens} their
                 * CONTACT_FORM_URL contains -- the two personal ones below go
                 * nowhere unless they asked for them by name. See
                 * components/contactForm.ts. */
                prefill={{
                    organization: settings?.township_name,
                    contact_name: user?.full_name,
                    contact_email: user?.email,
                    contact_role: user?.role,
                }}
            />

            {/* Mobile sidebar backdrop */}
            <AnimatePresence>
                {sidebarOpen && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        onClick={() => setSidebarOpen(false)}
                        className="fixed inset-0 bg-black/60 z-40 lg:hidden"
                    />
                )}
            </AnimatePresence>

            {/* Sidebar */}
            <aside
                className={`fixed lg:static inset-y-0 left-0 z-50 w-72 glass-sidebar transform transition-transform duration-300 lg:translate-x-0 ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'
                    }`}
                aria-label="Admin console navigation"
            >
                <div className="flex flex-col h-full">
                    {/* Header */}
                    <div className="p-6 border-b border-white/10">
                        <div className="flex items-center justify-between">
                            <button
                                onClick={() => {
                                    setCurrentTab('branding');
                                    window.location.hash = '';
                                }}
                                className="flex items-center gap-3 hover:opacity-80 transition-opacity cursor-pointer"
                                aria-label="Go to admin home"
                            >
                                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-amber-400 to-orange-600 flex items-center justify-center">
                                <Home className="w-5 h-5 text-white" aria-hidden="true" />
                                </div>
                                <div className="text-left" data-no-translate>
                                    <h2 className="font-semibold text-white">Admin Console</h2>
                                    <p className="text-xs text-white/50">{settings?.township_name}</p>
                                </div>
                            </button>
                            <button
                                onClick={() => setSidebarOpen(false)}
                                className="lg:hidden p-2 hover:bg-white/10 rounded-lg"
                                aria-label="Close navigation menu"
                            >
                                <X className="w-5 h-5 text-white/60" aria-hidden="true" />
                            </button>
                        </div>
                    </div>

                    {/* Menu - Grouped Accordion Navigation */}
                    <nav className="flex-1 p-4 space-y-3 overflow-y-auto" aria-label="Admin configuration">
                        {/* Branding & Setup Group */}
                        <SidebarGroup
                            title="Branding & Setup"
                            icon={Palette}
                            isActive={currentTab === 'branding' || currentTab === 'integration'}
                            defaultOpen={currentTab === 'branding' || currentTab === 'integration'}
                        >
                            <SidebarItem
                                icon={Palette}
                                label="Branding"
                                isActive={currentTab === 'branding'}
                                onClick={() => { setCurrentTab('branding'); setSidebarOpen(false); }}
                            />
                            <SidebarItem
                                icon={Terminal}
                                label="Setup & Integration"
                                isActive={currentTab === 'integration'}
                                onClick={() => { setCurrentTab('integration'); setSidebarOpen(false); }}
                            />
                        </SidebarGroup>

                        {/* Organization Group */}
                        <SidebarGroup
                            title="Organization"
                            icon={Users}
                            isActive={currentTab === 'users' || currentTab === 'departments' || currentTab === 'services'}
                            defaultOpen={currentTab === 'users' || currentTab === 'departments' || currentTab === 'services'}
                        >
                            <SidebarItem
                                icon={Users}
                                label="Users"
                                isActive={currentTab === 'users'}
                                onClick={() => { setCurrentTab('users'); setSidebarOpen(false); }}
                            />
                            <SidebarItem
                                icon={Building2}
                                label="Departments"
                                isActive={currentTab === 'departments'}
                                onClick={() => { setCurrentTab('departments'); setSidebarOpen(false); }}
                            />
                            <SidebarItem
                                icon={Grid3X3}
                                label="Service Categories"
                                isActive={currentTab === 'services'}
                                onClick={() => { setCurrentTab('services'); setSidebarOpen(false); }}
                            />
                        </SidebarGroup>

                        {/* System Group */}
                        <SidebarGroup
                            title="System & Compliance"
                            icon={Settings}
                            isActive={currentTab === 'system' || currentTab === 'health' || currentTab === 'compliance'}
                            defaultOpen={currentTab === 'system' || currentTab === 'health' || currentTab === 'compliance'}
                        >
                            <SidebarItem
                                icon={Settings}
                                label="System Settings"
                                isActive={currentTab === 'system'}
                                onClick={() => { setCurrentTab('system'); setSidebarOpen(false); }}
                            />
                            <SidebarItem
                                icon={BarChart3}
                                label="System Health"
                                isActive={currentTab === 'health'}
                                onClick={() => { setCurrentTab('health'); setSidebarOpen(false); }}
                            />
                            <SidebarItem
                                icon={Shield}
                                label="Compliance"
                                isActive={currentTab === 'compliance'}
                                onClick={() => { setCurrentTab('compliance'); setSidebarOpen(false); }}
                            />
                        </SidebarGroup>

                        {/* System Actions */}
                        <div className="pt-4 border-t border-white/10">
                            <p className="text-xs font-medium text-white/40 uppercase tracking-wider px-3 mb-3">
                                Version Control
                            </p>
                            <div className="px-3 mb-4">
                                <VersionSwitcher />
                            </div>

                            <Button
                                variant="primary"
                                size="sm"
                                className="w-full"
                                onClick={() => navigate('/staff')}
                            >
                                Staff Dashboard →
                            </Button>
                        </div>
                    </nav>

                    {/* User Footer - Sticky */}
                    <div className="sticky bottom-0 p-4 border-t border-white/10 bg-slate-900/90 backdrop-blur-md">
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-3">
                                <div className="w-10 h-10 rounded-full bg-amber-500/30 flex items-center justify-center text-white font-medium">
                                    A
                                </div>
                                <div>
                                    <p className="font-medium text-white text-sm">{user?.full_name || 'Administrator'}</p>
                                    <p className="text-xs text-amber-400">Admin</p>
                                </div>
                            </div>
                            <button
                                onClick={handleLogout}
                                className="p-2 hover:bg-white/10 rounded-lg transition-colors"
                                aria-label="Sign out"
                            >
                                <LogOut className="w-5 h-5 text-white/60" aria-hidden="true" />
                            </button>
                        </div>

                        {/* Product credit — quiet, but present on every authenticated
                            screen so the platform is identifiable to staff too. */}
                        <a
                            href="https://pinpoint311.org"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="brand-link group mt-3 pt-3 border-t border-white/5 flex items-center justify-center gap-2"
                        >
                            <span className="text-[10px] uppercase tracking-wider text-white/25 group-hover:text-white/45 transition-colors">
                                Powered by
                            </span>
                            <img
                                src="/pinpoint311_logo_dark_transparent.png"
                                alt="Pinpoint 311"
                                className="h-3.5 w-auto opacity-50 group-hover:opacity-90 transition-opacity"
                            />
                        </a>
                    </div>
                </div>
            </aside>

            {/* Main Content */}
            <div id="main-content" role="main" className="flex-1 flex flex-col min-w-0">
                {/* Mobile Header */}
                <header className="lg:hidden glass-sidebar p-4 flex items-center justify-between sticky top-0 z-30">
                    <button
                        onClick={() => setSidebarOpen(true)}
                        className="p-2 hover:bg-white/10 rounded-lg"
                        aria-label="Open navigation menu"
                    >
                        <Menu className="w-6 h-6 text-white" aria-hidden="true" />
                    </button>
                    <h1 className="font-semibold text-white">Admin Console</h1>
                    <div className="w-10" aria-hidden="true" />
                </header>

                {/* Content */}
                <div ref={contentRef} className="flex-1 p-4 md:p-6 overflow-auto">
                    <div className="max-w-4xl mx-auto">
                        {/* Save message */}
                        <AnimatePresence>
                            {saveMessage && (
                                <motion.div
                                    initial={{ opacity: 0, y: -20 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    exit={{ opacity: 0, y: -20 }}
                                    className="mb-6 flex items-center gap-3 p-4 rounded-xl bg-green-500/20 border border-green-500/30 text-green-300"
                                >
                                    <Check className="w-5 h-5" />
                                    {saveMessage}
                                </motion.div>
                            )}
                            {/* Failures were logged to the browser console and
                                nowhere else, so a rejected save was
                                indistinguishable from a button that did nothing.
                                The server almost always says exactly what is
                                wrong -- "Override must be at least 365 days" --
                                and that sentence never reached the person who
                                could act on it. */}
                            {errorMessage && (
                                <motion.div
                                    initial={{ opacity: 0, y: -20 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    exit={{ opacity: 0, y: -20 }}
                                    className="mb-6 flex items-start gap-3 p-4 rounded-xl bg-red-500/15 border border-red-500/30 text-red-200"
                                    role="alert"
                                >
                                    <AlertTriangle className="w-5 h-5 shrink-0 mt-0.5" aria-hidden="true" />
                                    <span className="min-w-0">{errorMessage}</span>
                                </motion.div>
                            )}
                        </AnimatePresence>

                        {/* Branding Tab */}
                        {currentTab === 'branding' && (
                            <div className="space-y-6">
                                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                                    <h1 className="text-xl sm:text-2xl font-bold text-white">Branding Settings</h1>
                                    <Button className="w-full sm:w-auto" leftIcon={<Save className="w-4 h-4" />} onClick={handleSaveBranding} isLoading={isLoading}>
                                        Save Changes
                                    </Button>
                                </div>

                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-6">
                                    <Card>
                                        <div className="space-y-4">
                                            <Input
                                                label="Municipality Name"
                                                value={brandingForm.township_name || ''}
                                                onChange={(e) => setBrandingForm((p) => ({ ...p, township_name: e.target.value }))}
                                            />
                                            <Input
                                                label="Hero Text"
                                                value={brandingForm.hero_text || ''}
                                                onChange={(e) => setBrandingForm((p) => ({ ...p, hero_text: e.target.value }))}
                                            />
                                            <div>
                                                <label className="block text-sm font-medium text-white/70 mb-2">
                                                    Logo
                                                </label>
                                                <div className="flex items-center gap-3">
                                                    {brandingForm.logo_url && (
                                                        <img src={brandingForm.logo_url} alt="Logo" className="h-10 rounded" />
                                                    )}
                                                    <label className="flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-xl bg-white/5 border border-white/10 hover:bg-white/10 cursor-pointer transition-all">
                                                        <Upload className="w-4 h-4 text-white/60" />
                                                        <span className="text-sm text-white/70">Upload Logo</span>
                                                        <input
                                                            type="file"
                                                            accept="image/*"
                                                            className="hidden"
                                                            onChange={async (e) => {
                                                                const file = e.target.files?.[0];
                                                                if (file) {
                                                                    try {
                                                                        const result = await api.uploadImage(file);
                                                                        setBrandingForm((p) => ({ ...p, logo_url: result.url }));
                                                                    } catch (err) {
                                                                        console.error('Logo upload failed:', err);
                                                                    }
                                                                }
                                                            }}
                                                        />
                                                    </label>
                                                    {brandingForm.logo_url && (
                                                        <button
                                                            onClick={() => setBrandingForm((p) => ({ ...p, logo_url: '' }))}
                                                            className="p-2 rounded-lg bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-colors"
                                                            title="Remove logo"
                                                            aria-label="Remove logo"
                                                        >
                                                            <X className="w-4 h-4" aria-hidden="true" />
                                                        </button>
                                                    )}
                                                </div>
                                            </div>
                                            <div>
                                                <label className="block text-sm font-medium text-white/70 mb-2">
                                                    Favicon
                                                </label>
                                                <div className="flex items-center gap-3">
                                                    {brandingForm.favicon_url && (
                                                        <img src={brandingForm.favicon_url} alt="Favicon" className="h-8 w-8 rounded" />
                                                    )}
                                                    <label className="flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-xl bg-white/5 border border-white/10 hover:bg-white/10 cursor-pointer transition-all">
                                                        <Upload className="w-4 h-4 text-white/60" />
                                                        <span className="text-sm text-white/70">Upload Favicon</span>
                                                        <input
                                                            type="file"
                                                            accept="image/*,.ico"
                                                            className="hidden"
                                                            onChange={async (e) => {
                                                                const file = e.target.files?.[0];
                                                                if (file) {
                                                                    try {
                                                                        const result = await api.uploadImage(file);
                                                                        setBrandingForm((p) => ({ ...p, favicon_url: result.url }));
                                                                    } catch (err) {
                                                                        console.error('Favicon upload failed:', err);
                                                                    }
                                                                }
                                                            }}
                                                        />
                                                    </label>
                                                    {brandingForm.favicon_url && (
                                                        <button
                                                            onClick={() => setBrandingForm((p) => ({ ...p, favicon_url: '' }))}
                                                            className="p-2 rounded-lg bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-colors"
                                                            title="Remove favicon"
                                                            aria-label="Remove favicon"
                                                        >
                                                            <X className="w-4 h-4" aria-hidden="true" />
                                                        </button>
                                                    )}
                                                </div>
                                            </div>
                                        </div>
                                    </Card>

                                    <Card>
                                        <h3 className="text-lg font-semibold text-white mb-4">Preview</h3>
                                        <div className="p-4 rounded-xl bg-black/20 space-y-4">
                                            {brandingForm.logo_url ? (
                                                <img src={brandingForm.logo_url} alt="Logo preview" className="h-16" />
                                            ) : (
                                                <div
                                                    className="w-16 h-16 rounded-2xl flex items-center justify-center"
                                                    style={{ background: 'linear-gradient(135deg, #6366f1, #6366f1dd)' }}
                                                >
                                                    <Sparkles className="w-8 h-8 text-white" />
                                                </div>
                                            )}
                                            <h2 className="text-xl font-bold text-white">{brandingForm.township_name}</h2>
                                            <p className="text-white/60">{brandingForm.hero_text}</p>
                                        </div>
                                    </Card>
                                </div>


                                {/* Domain Connection */}
                                <Card className="mt-6">
                                    <h3 className="text-lg font-semibold text-white mb-4">Custom Domain</h3>
                                    <p className="text-sm text-white/50 mb-4">
                                        Connect your own domain (e.g., 311.yourtownship.gov) to this 311 portal.
                                    </p>

                                    <div className="space-y-4">
                                        <div className="p-4 rounded-xl bg-white/5 border border-white/10">
                                            <p className="text-sm font-medium text-white mb-2">Current URL</p>
                                            <code className="text-primary-300 text-sm bg-black/30 px-2 py-1 rounded break-all">
                                                {window.location.origin}
                                            </code>
                                        </div>

                                        {/* Auto-Configure Domain */}
                                        <div className="p-4 rounded-xl bg-primary-500/10 border border-primary-500/20">
                                            <p className="text-sm font-medium text-primary-300 mb-3">Auto-Configure Domain</p>
                                            <p className="text-xs text-white/60 mb-3">
                                                After setting up your DNS records, enter your domain below and we'll configure Nginx + SSL automatically.
                                            </p>
                                            <div className="flex gap-2">
                                                <Input
                                                    placeholder="311.yourtownship.gov"
                                                    value={(brandingForm as any).custom_domain || ''}
                                                    onChange={(e) => setBrandingForm(p => ({ ...p, custom_domain: e.target.value }))}
                                                    className="flex-1"
                                                />
                                                <Button
                                                    onClick={async () => {
                                                        const domain = (brandingForm as any).custom_domain;
                                                        if (!domain) { alert("Please enter a domain"); return; }
                                                        setIsLoading(true);
                                                        try {
                                                            const result = await api.configureDomain(domain);
                                                            if (result.status === 'success') {
                                                                alert(
                                                                    `✅ ${result.message}\n\n` +
                                                                    `Your site will be available at:\n${result.url}\n\n` +
                                                                    `HTTPS certificate is being automatically provisioned by Caddy.`
                                                                );
                                                            } else if (result.status === 'partial') {
                                                                alert(
                                                                    `⚠️ ${result.message}\n\n` +
                                                                    `Next step: ${result.next_step || 'Restart Caddy container'}\n\n` +
                                                                    `Run on server:\nssh ubuntu@132.226.32.116\ncd ~/WWF-Open-Source-311-Template\ndocker-compose restart caddy`
                                                                );
                                                            } else {
                                                                alert(`Error: ${result.message}`);
                                                            }
                                                        } catch (err: any) { alert(`Error: ${err.message}`); }
                                                        finally { setIsLoading(false); }
                                                    }}
                                                    isLoading={isLoading}
                                                >
                                                    Configure Domain
                                                </Button>
                                            </div>
                                        </div>

                                        <div className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/20">
                                            <p className="text-sm font-medium text-amber-300 mb-3">Step 1: Set up DNS Records first</p>
                                            <ol className="text-sm text-white/70 space-y-2 list-decimal list-inside">
                                                <li>Log into your domain registrar (GoDaddy, Namecheap, etc.)</li>
                                                <li>Add an <strong className="text-white">A Record</strong> pointing to: <code className="text-primary-300 bg-black/30 px-1 rounded">132.226.32.116</code></li>
                                                <li>Wait 5-30 minutes for DNS propagation</li>
                                                <li>Then enter domain above & click "Configure SSL"</li>
                                            </ol>
                                        </div>

                                        <div className="p-4 rounded-xl bg-white/5 border border-white/10">
                                            <p className="text-sm font-medium text-white mb-3">DNS Records to Add</p>
                                            <table className="w-full text-sm">
                                                <thead>
                                                    <tr className="text-white/50">
                                                        <th className="text-left py-1">Type</th>
                                                        <th className="text-left py-1">Host</th>
                                                        <th className="text-left py-1">Value</th>
                                                    </tr>
                                                </thead>
                                                <tbody className="text-white/80">
                                                    <tr>
                                                        <td className="py-1">A</td>
                                                        <td className="py-1">@ or 311</td>
                                                        <td className="py-1"><code className="text-primary-300">132.226.32.116</code></td>
                                                    </tr>
                                                    <tr>
                                                        <td className="py-1">A</td>
                                                        <td className="py-1">www</td>
                                                        <td className="py-1"><code className="text-primary-300">132.226.32.116</code></td>
                                                    </tr>
                                                </tbody>
                                            </table>
                                        </div>
                                    </div>
                                </Card>

                                {/* Social Links */}
                                <Card className="mt-6">
                                    <div className="flex items-center justify-between mb-4">
                                        <div>
                                            <h3 className="text-lg font-semibold text-white">Social Links</h3>
                                            <p className="text-sm text-white/50">Add links to display in the resident portal footer</p>
                                        </div>
                                        <Button
                                            size="sm"
                                            leftIcon={<Plus className="w-4 h-4" />}
                                            onClick={() => {
                                                const currentLinks = (brandingForm as any).social_links || [];
                                                setBrandingForm(p => ({
                                                    ...p,
                                                    social_links: [...currentLinks, { platform: 'website', url: '', icon: 'Globe' }]
                                                }));
                                            }}
                                        >
                                            Add Link
                                        </Button>
                                    </div>

                                    <div className="space-y-3">
                                        {((brandingForm as any).social_links || []).map((link: { platform: string; url: string; icon: string }, index: number) => (
                                            <div key={index} className="flex items-center gap-3 p-3 rounded-xl bg-white/5 border border-white/10">
                                                {/* Platform/Icon selector */}
                                                <div className="w-10 h-10 rounded-lg bg-white/10 flex items-center justify-center">
                                                    {link.icon === 'Globe' && <Globe className="w-5 h-5 text-blue-400" />}
                                                    {link.icon === 'Facebook' && <Facebook className="w-5 h-5 text-blue-500" />}
                                                    {link.icon === 'Instagram' && <Instagram className="w-5 h-5 text-pink-500" />}
                                                    {link.icon === 'Youtube' && <Youtube className="w-5 h-5 text-red-500" />}
                                                    {link.icon === 'Twitter' && <Twitter className="w-5 h-5 text-sky-400" />}
                                                    {link.icon === 'Linkedin' && <Linkedin className="w-5 h-5 text-blue-600" />}
                                                </div>
                                                <Select
                                                    value={link.platform}
                                                    onChange={(e) => {
                                                        const currentLinks = [...((brandingForm as any).social_links || [])];
                                                        const iconMap: Record<string, string> = {
                                                            website: 'Globe',
                                                            facebook: 'Facebook',
                                                            instagram: 'Instagram',
                                                            youtube: 'Youtube',
                                                            twitter: 'Twitter',
                                                            linkedin: 'Linkedin',
                                                        };
                                                        currentLinks[index] = {
                                                            ...currentLinks[index],
                                                            platform: e.target.value,
                                                            icon: iconMap[e.target.value] || 'Globe'
                                                        };
                                                        setBrandingForm(p => ({ ...p, social_links: currentLinks }));
                                                    }}
                                                    className="w-36"
                                                    options={[
                                                        { value: 'website', label: 'Website' },
                                                        { value: 'facebook', label: 'Facebook' },
                                                        { value: 'instagram', label: 'Instagram' },
                                                        { value: 'youtube', label: 'YouTube' },
                                                        { value: 'twitter', label: 'X (Twitter)' },
                                                        { value: 'linkedin', label: 'LinkedIn' },
                                                    ]}
                                                />
                                                <Input
                                                    placeholder="https://..."
                                                    value={link.url}
                                                    onChange={(e) => {
                                                        const currentLinks = [...((brandingForm as any).social_links || [])];
                                                        currentLinks[index] = { ...currentLinks[index], url: e.target.value };
                                                        setBrandingForm(p => ({ ...p, social_links: currentLinks }));
                                                    }}
                                                    className="flex-1"
                                                />
                                                <button
                                                    onClick={() => {
                                                        const currentLinks = [...((brandingForm as any).social_links || [])];
                                                        currentLinks.splice(index, 1);
                                                        setBrandingForm(p => ({ ...p, social_links: currentLinks }));
                                                    }}
                                                    className="p-2 rounded-lg bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-colors"
                                                    title="Remove link"
                                                >
                                                    <X className="w-4 h-4" />
                                                </button>
                                            </div>
                                        ))}
                                        {((brandingForm as any).social_links || []).length === 0 && (
                                            <div className="text-center py-8 text-white/40">
                                                <Globe className="w-8 h-8 mx-auto mb-2 opacity-50" />
                                                <p className="text-sm">No social links configured</p>
                                                <p className="text-xs mt-1">Click "Add Link" to add your social media profiles</p>
                                            </div>
                                        )}
                                    </div>
                                </Card>

                                {/* Legal Documents */}
                                <Card className="mt-6">
                                    <div className="mb-4">
                                        <h3 className="text-lg font-semibold text-white">Legal Documents</h3>
                                        <p className="text-sm text-white/50">Customize your Privacy Policy, Terms of Service, and Accessibility Statement</p>
                                    </div>

                                    <div className="space-y-6">
                                        {/* Privacy Policy */}
                                        <div>
                                            <div className="flex items-center justify-between mb-2">
                                                <label className="text-sm font-medium text-white/70">Privacy Policy</label>
                                                <a href="/privacy" target="_blank" className="text-xs text-primary-400 hover:text-primary-300">Preview →</a>
                                            </div>
                                            <textarea
                                                value={(brandingForm as any).privacy_policy || ''}
                                                onChange={(e) => setBrandingForm(p => ({ ...p, privacy_policy: e.target.value || null }))}
                                                placeholder="Leave blank to use default privacy policy with 311 best practices..."
                                                className="w-full h-32 px-4 py-3 rounded-xl bg-white/5 border border-white/10 text-white placeholder-white/30 focus:outline-none focus:ring-2 focus:ring-primary-500/50 font-mono text-sm resize-y"
                                            />
                                            <p className="text-xs text-white/40 mt-1">Supports Markdown formatting. Leave blank for sensible defaults.</p>
                                        </div>

                                        {/* Terms of Service */}
                                        <div>
                                            <div className="flex items-center justify-between mb-2">
                                                <label className="text-sm font-medium text-white/70">Terms of Service</label>
                                                <a href="/terms" target="_blank" className="text-xs text-primary-400 hover:text-primary-300">Preview →</a>
                                            </div>
                                            <textarea
                                                value={(brandingForm as any).terms_of_service || ''}
                                                onChange={(e) => setBrandingForm(p => ({ ...p, terms_of_service: e.target.value || null }))}
                                                placeholder="Leave blank to use default terms with non-emergency disclaimer..."
                                                className="w-full h-32 px-4 py-3 rounded-xl bg-white/5 border border-white/10 text-white placeholder-white/30 focus:outline-none focus:ring-2 focus:ring-primary-500/50 font-mono text-sm resize-y"
                                            />
                                            <p className="text-xs text-white/40 mt-1">Default includes prominent non-emergency disclaimer. Supports Markdown.</p>
                                        </div>

                                        {/* Accessibility Statement */}
                                        <div>
                                            <div className="flex items-center justify-between mb-2">
                                                <label className="text-sm font-medium text-white/70">Accessibility Statement</label>
                                                <a href="/accessibility" target="_blank" className="text-xs text-primary-400 hover:text-primary-300">Preview →</a>
                                            </div>
                                            <textarea
                                                value={(brandingForm as any).accessibility_statement || ''}
                                                onChange={(e) => setBrandingForm(p => ({ ...p, accessibility_statement: e.target.value || null }))}
                                                placeholder="Leave blank to use default WCAG/ADA compliant accessibility statement..."
                                                className="w-full h-32 px-4 py-3 rounded-xl bg-white/5 border border-white/10 text-white placeholder-white/30 focus:outline-none focus:ring-2 focus:ring-primary-500/50 font-mono text-sm resize-y"
                                            />
                                            <p className="text-xs text-white/40 mt-1">Default covers WCAG 2.1 AA, Section 508, and ADA requirements.</p>
                                        </div>
                                    </div>
                                </Card>
                            </div>
                        )}

                        {/* Users Tab */}
                        {currentTab === 'users' && (
                            <div className="space-y-6">
                                {/* Premium Header */}
                                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                                    <div>
                                        <h1 className="text-xl sm:text-2xl font-bold text-white">User Management</h1>
                                        <p className="text-sm text-white/50 mt-1">Manage staff and administrator accounts</p>
                                    </div>
                                    <Button
                                        leftIcon={<Plus className="w-4 h-4" />}
                                        onClick={() => setShowUserModal(true)}
                                        className="w-full sm:w-auto bg-gradient-to-r from-primary-500 to-primary-600 hover:from-primary-400 hover:to-primary-500 shadow-lg shadow-primary-500/25"
                                    >
                                        Add User
                                    </Button>
                                </div>

                                {/* Stats Cards */}
                                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                                    <div className="p-4 rounded-2xl bg-gradient-to-br from-blue-500/10 to-blue-600/5 border border-blue-500/20 backdrop-blur-sm">
                                        <div className="flex items-center gap-3">
                                            <div className="w-10 h-10 rounded-xl bg-blue-500/20 flex items-center justify-center">
                                                <UserIcon className="w-5 h-5 text-blue-400" />
                                            </div>
                                            <div>
                                                <p className="text-2xl font-bold text-white">{users.length}</p>
                                                <p className="text-xs text-blue-300/70">Total Users</p>
                                            </div>
                                        </div>
                                    </div>
                                    <div className="p-4 rounded-2xl bg-gradient-to-br from-amber-500/10 to-amber-600/5 border border-amber-500/20 backdrop-blur-sm">
                                        <div className="flex items-center gap-3">
                                            <div className="w-10 h-10 rounded-xl bg-amber-500/20 flex items-center justify-center">
                                                <Shield className="w-5 h-5 text-amber-400" />
                                            </div>
                                            <div>
                                                <p className="text-2xl font-bold text-white">{users.filter(u => u.role === 'admin').length}</p>
                                                <p className="text-xs text-amber-300/70">Administrators</p>
                                            </div>
                                        </div>
                                    </div>
                                    <div className="p-4 rounded-2xl bg-gradient-to-br from-emerald-500/10 to-emerald-600/5 border border-emerald-500/20 backdrop-blur-sm">
                                        <div className="flex items-center gap-3">
                                            <div className="w-10 h-10 rounded-xl bg-emerald-500/20 flex items-center justify-center">
                                                <Users className="w-5 h-5 text-emerald-400" />
                                            </div>
                                            <div>
                                                <p className="text-2xl font-bold text-white">{users.filter(u => u.role === 'staff').length}</p>
                                                <p className="text-xs text-emerald-300/70">Staff Members</p>
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                {/* Premium Table */}
                                <div className="rounded-2xl bg-white/[0.03] backdrop-blur-xl border border-white/10 overflow-hidden shadow-2xl">
                                    {/* Table Header - Hidden on mobile */}
                                    <div className="hidden md:block px-6 py-4 border-b border-white/10 bg-gradient-to-r from-white/[0.05] to-transparent">
                                        <div className="grid grid-cols-12 gap-4 items-center">
                                            <div className="col-span-4 text-xs font-semibold text-white/50 uppercase tracking-wider">User</div>
                                            <div className="col-span-3 text-xs font-semibold text-white/50 uppercase tracking-wider">Email</div>
                                            <div className="col-span-2 text-xs font-semibold text-white/50 uppercase tracking-wider text-center">Role</div>
                                            <div className="col-span-2 text-xs font-semibold text-white/50 uppercase tracking-wider">Department</div>
                                            <div className="col-span-1 text-xs font-semibold text-white/50 uppercase tracking-wider text-right">Actions</div>
                                        </div>
                                    </div>

                                    {/* Table Body */}
                                    <div className="divide-y divide-white/5">
                                        {users.map((u, index) => (
                                            <motion.div
                                                key={u.id}
                                                initial={{ opacity: 0, y: 10 }}
                                                animate={{ opacity: 1, y: 0 }}
                                                transition={{ delay: index * 0.05 }}
                                                className="px-4 md:px-6 py-4 hover:bg-white/[0.03] transition-all duration-200 group"
                                            >
                                                {/* Mobile: Card Layout */}
                                                <div className="md:hidden space-y-3">
                                                    <div className="flex items-center justify-between">
                                                        <div className="flex items-center gap-3">
                                                            <div className={`relative w-10 h-10 rounded-xl flex items-center justify-center font-bold text-sm ring-1 ring-white/10 shadow-xl ${u.role === 'admin'
                                                                ? 'bg-gradient-to-br from-amber-300 to-orange-500 text-amber-950 drop-shadow-sm shadow-orange-900/50'
                                                                : 'bg-gradient-to-br from-primary-400 to-primary-600 text-white shadow-primary-900/60'
                                                                }`}>
                                                                {u.full_name ? u.full_name.charAt(0).toUpperCase() : u.username.charAt(0).toUpperCase()}
                                                                <div className="absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full bg-emerald-500 border-2 border-slate-900 shadow-md shadow-emerald-900/70" />
                                                            </div>
                                                            <div>
                                                                <p className="font-semibold text-white text-sm">{u.full_name || u.username}</p>
                                                                <p className="text-xs text-white/40">@{u.username}</p>
                                                            </div>
                                                        </div>
                                                        {u.role === 'admin' ? (
                                                            <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-semibold bg-gradient-to-r from-amber-500/20 to-orange-500/20 text-amber-300 border border-amber-500/30">
                                                                <Shield className="w-3 h-3" />
                                                                Admin
                                                            </span>
                                                        ) : (
                                                            <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-semibold bg-gradient-to-r from-blue-500/20 to-cyan-500/20 text-blue-300 border border-blue-500/30">
                                                                <UserIcon className="w-3 h-3" />
                                                                Staff
                                                            </span>
                                                        )}
                                                    </div>
                                                    <p className="text-xs text-white/50 truncate">{u.email}</p>
                                                    <div className="flex items-center justify-between">
                                                        <div className="flex flex-wrap gap-1">
                                                            {u.departments && u.departments.length > 0 ? (
                                                                u.departments.slice(0, 2).map((dept) => (
                                                                    <span key={dept.id} className="px-2 py-0.5 text-xs rounded-lg bg-white/5 text-white/70 border border-white/10">
                                                                        {dept.name}
                                                                    </span>
                                                                ))
                                                            ) : (
                                                                <span className="text-xs text-white/25 italic">No department</span>
                                                            )}
                                                        </div>
                                                        <button
                                                            onClick={() => openEditUser(u)}
                                                            className="p-2 rounded-lg hover:bg-white/10 text-white/40 hover:text-white transition-colors"
                                                            title={`Edit ${u.full_name || u.username}`}
                                                        >
                                                            <Pencil className="w-4 h-4" />
                                                        </button>
                                                        <button
                                                            onClick={() => handleDeleteUser(u.id)}
                                                            disabled={u.id === user?.id}
                                                            className={`p-2 rounded-lg ${u.id === user?.id
                                                                ? 'text-white/20 cursor-not-allowed'
                                                                : 'hover:bg-red-500/20 text-white/40 hover:text-red-400'
                                                                }`}
                                                            title={u.id === user?.id ? "Cannot delete yourself" : "Delete user"}
                                                        >
                                                            <Trash2 className="w-4 h-4" />
                                                        </button>
                                                    </div>
                                                </div>

                                                {/* Desktop: Grid Layout */}
                                                <div className="hidden md:grid grid-cols-12 gap-4 items-center">
                                                    {/* User Info */}
                                                    <div className="col-span-4 flex items-center gap-4">
                                                        <div className={`relative w-12 h-12 rounded-xl flex items-center justify-center font-bold text-lg ring-1 ring-white/10 shadow-xl ${u.role === 'admin'
                                                            ? 'bg-gradient-to-br from-amber-400 to-orange-500 text-white shadow-orange-900/50'
                                                            : 'bg-gradient-to-br from-primary-400 to-primary-600 text-white shadow-primary-900/60'
                                                            }`}>
                                                            {u.full_name ? u.full_name.charAt(0).toUpperCase() : u.username.charAt(0).toUpperCase()}
                                                            {/* Online indicator */}
                                                            <div className="absolute -bottom-0.5 -right-0.5 w-3.5 h-3.5 rounded-full bg-emerald-500 border-2 border-slate-900 shadow-md shadow-emerald-900/70" />
                                                        </div>
                                                        <div>
                                                            <p className="font-semibold text-white group-hover:text-primary-300 transition-colors">{u.full_name || u.username}</p>
                                                            <p className="text-sm text-white/40">@{u.username}</p>
                                                        </div>
                                                    </div>

                                                    {/* Email */}
                                                    <div className="col-span-3">
                                                        <p className="text-sm text-white/60 truncate">{u.email}</p>
                                                    </div>

                                                    {/* Role Badge */}
                                                    <div className="col-span-2 flex justify-center">
                                                        {u.role === 'admin' ? (
                                                            <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold bg-gradient-to-r from-amber-500/20 to-orange-500/20 text-amber-300 border border-amber-500/30 shadow-lg shadow-amber-500/10">
                                                                <Shield className="w-3 h-3" />
                                                                Admin
                                                            </span>
                                                        ) : (
                                                            <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold bg-gradient-to-r from-blue-500/20 to-cyan-500/20 text-blue-300 border border-blue-500/30 shadow-lg shadow-blue-500/10">
                                                                <UserIcon className="w-3 h-3" />
                                                                Staff
                                                            </span>
                                                        )}
                                                    </div>

                                                    {/* Departments */}
                                                    <div className="col-span-2">
                                                        {u.departments && u.departments.length > 0 ? (
                                                            <div className="flex flex-wrap gap-1.5">
                                                                {u.departments.slice(0, 2).map((dept) => (
                                                                    <span
                                                                        key={dept.id}
                                                                        className="px-2.5 py-1 text-xs font-medium rounded-lg bg-white/5 text-white/70 border border-white/10"
                                                                    >
                                                                        {dept.name}
                                                                    </span>
                                                                ))}
                                                                {u.departments.length > 2 && (
                                                                    <span className="px-2 py-1 text-xs text-white/40">
                                                                        +{u.departments.length - 2}
                                                                    </span>
                                                                )}
                                                            </div>
                                                        ) : (
                                                            <span className="text-sm text-white/25 italic">No department</span>
                                                        )}
                                                    </div>

                                                    {/* Actions */}
                                                    <div className="col-span-1 flex justify-end">
                                                        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                                                            <button
                                                                onClick={() => openEditUser(u)}
                                                                className="p-2 rounded-lg hover:bg-white/10 text-white/40 hover:text-white transition-colors"
                                                                title={`Edit ${u.full_name || u.username}`}
                                                            >
                                                                <Pencil className="w-4 h-4" />
                                                            </button>
                                                            <button
                                                                onClick={() => handleDeleteUser(u.id)}
                                                                disabled={u.id === user?.id}
                                                                className={`p-2 rounded-lg transition-all ${u.id === user?.id
                                                                    ? 'text-white/20 cursor-not-allowed'
                                                                    : 'hover:bg-red-500/20 text-white/40 hover:text-red-400'
                                                                    }`}
                                                                title={u.id === user?.id ? "Cannot delete yourself" : "Delete user"}
                                                            >
                                                                <Trash2 className="w-4 h-4" />
                                                            </button>
                                                        </div>
                                                    </div>
                                                </div>
                                            </motion.div>
                                        ))}
                                    </div>

                                    {/* Empty State */}
                                    {users.length === 0 && (
                                        <div className="px-6 py-16 text-center">
                                            <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-white/5 flex items-center justify-center">
                                                <Users className="w-8 h-8 text-white/20" />
                                            </div>
                                            <p className="text-white/50 mb-2">No users found</p>
                                            <p className="text-sm text-white/30">Add your first user to get started</p>
                                        </div>
                                    )}
                                </div>
                            </div>
                        )}
                        {/* Departments Tab */}
                        {currentTab === 'departments' && (
                            <DepartmentsTab
                                departments={departments}
                                onAdd={() => {
                                    setEditingDepartment(null);
                                    setNewDepartment({ name: '', description: '', routing_email: '' });
                                    setShowDepartmentModal(true);
                                }}
                                onEdit={handleEditDepartment}
                                onDelete={handleDeleteDepartment}
                            />
                        )}

                        {/* Services Tab */}
                        {currentTab === 'services' && (
                            <ServiceCategoriesTab
                                services={services}
                                setServices={setServices}
                                loadTabData={loadTabData}
                                setShowServiceModal={setShowServiceModal}
                                handleEditService={handleEditService}
                                handleDeleteService={handleDeleteService}
                            />
                        )}


                        {/* Integrations Tab - Card-based Setup & Integrations */}
                        {currentTab === 'integration' && (
                            <SetupIntegrationsPage
                                secrets={secrets}
                                onSaveSecret={handleSaveSecretDirect}
                                onRefresh={loadTabData}
                            />
                        )}


                        {/* System Settings Tab */}
                        {currentTab === 'system' && (
                            <div className="space-y-8">
                                {/* Premium Header */}
                                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                                    <div>
                                        <h1 className="text-xl sm:text-2xl font-bold text-white">System Settings</h1>
                                        <p className="text-sm text-white/50 mt-1">Configure feature modules and integrations</p>
                                    </div>
                                    <Button
                                        className="w-full sm:w-auto bg-gradient-to-r from-primary-500 to-primary-600 hover:from-primary-400 hover:to-primary-500 shadow-lg shadow-primary-500/25"
                                        leftIcon={<Save className="w-4 h-4" />}
                                        onClick={handleSaveModules}
                                        isLoading={isLoading}
                                    >
                                        Save Changes
                                    </Button>
                                </div>

                                {/* Feature Modules - Premium Container */}
                                <div className="rounded-2xl bg-white/[0.03] backdrop-blur-xl border border-white/10 overflow-hidden shadow-2xl">
                                    {/* Section Header */}
                                    <div className="px-5 sm:px-6 py-4 border-b border-white/10 bg-gradient-to-r from-white/[0.05] to-transparent">
                                        <div className="flex items-center gap-3">
                                            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-primary-500/20 to-primary-600/10 border border-primary-500/20 flex items-center justify-center">
                                                <Settings className="w-4 h-4 text-primary-400" />
                                            </div>
                                            <div>
                                                <h2 className="text-sm font-semibold text-white">Feature Modules</h2>
                                                <p className="text-xs text-white/40">Enable or disable platform capabilities</p>
                                            </div>
                                        </div>
                                    </div>

                                    {/* Module Rows */}
                                    <div className="divide-y divide-white/[0.06]">
                                        {[
                                            /* AI Analysis, SMS Alerts and Email
                                               Notifications were here. They are
                                               integrations with credentials, a
                                               provider and a card, and this
                                               screen was a second place to
                                               switch them -- one that knew
                                               nothing about whether they were
                                               set up. They are ticks in Setup &
                                               Integrations now. */
                                            { key: 'research_portal' as const, label: 'Research Portal', desc: 'Enable researcher access to anonymized data exports', icon: FlaskConical, color: 'violet' },
                                            { key: 'unlisted_reports' as const, label: 'Unlisted Reports', desc: 'Let residents keep a report off the public map and feed. The tracking link still works and staff always see it', icon: EyeOff, color: 'slate' },
                                        ].map((mod, idx) => {
                                            const Icon = mod.icon;
                                            const isOn = modules[mod.key];
                                            const colorMap: Record<string, string> = {
                                                blue: 'from-blue-500/20 to-blue-600/10 border-blue-500/20 text-blue-400',
                                                green: 'from-emerald-500/20 to-emerald-600/10 border-emerald-500/20 text-emerald-400',
                                                sky: 'from-sky-500/20 to-sky-600/10 border-sky-500/20 text-sky-400',
                                                violet: 'from-violet-500/20 to-violet-600/10 border-violet-500/20 text-violet-400',
                                                slate: 'from-slate-500/20 to-slate-600/10 border-slate-500/20 text-slate-300',
                                            };
                                            const colors = colorMap[mod.color];
                                            return (
                                                <motion.div
                                                    key={mod.key}
                                                    initial={{ opacity: 0, y: 10 }}
                                                    animate={{ opacity: 1, y: 0 }}
                                                    transition={{ delay: idx * 0.05 }}
                                                    className="px-5 sm:px-6 py-5 hover:bg-white/[0.02] transition-all duration-200"
                                                >
                                                    <div className="flex items-center justify-between gap-4">
                                                        <div className="flex items-center gap-4 flex-1 min-w-0">
                                                            <div className={`w-11 h-11 rounded-xl bg-gradient-to-br border flex items-center justify-center shrink-0 ${colors}`}>
                                                                <Icon className="w-5 h-5" />
                                                            </div>
                                                            <div className="min-w-0">
                                                                <h3 className="text-sm font-semibold text-white">{mod.label}</h3>
                                                                <p className="text-xs text-white/50 mt-0.5 truncate">{mod.desc}</p>
                                                            </div>
                                                        </div>
                                                        <button
                                                            onClick={() => setModules((p) => ({ ...p, [mod.key]: !p[mod.key] }))}
                                                            className={`relative inline-flex items-center rounded-full transition-colors duration-300 shrink-0 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/50 ${isOn ? 'bg-primary-500 shadow-lg shadow-primary-500/30' : 'bg-slate-600'}`}
                                                            style={{ width: 44, height: 24, minHeight: 24, maxHeight: 24, padding: 0 }}
                                                            role="switch"
                                                            aria-checked={isOn}
                                                            aria-label={`Toggle ${mod.label}`}
                                                        >
                                                            <span
                                                                className={`inline-block rounded-full bg-white shadow-md transition-transform duration-300 ${isOn ? 'translate-x-6' : 'translate-x-1'}`}
                                                                style={{ width: 16, height: 16 }}
                                                                aria-hidden="true"
                                                            />
                                                        </button>
                                                    </div>

                                                    {/* Per-pack export switches, under the Research Portal
                                                        toggle they govern. Field lists come from the server's
                                                        COLUMN_DICTIONARY, so the disclosure cannot drift from
                                                        what the export actually ships; enforcement is
                                                        server-side at row build, this is just the switch. */}
                                                    {mod.key === 'research_portal' && researchPacks.length > 0 && (
                                                        <div className="mt-4 space-y-2 border-l-2 border-violet-500/20 pl-4 ml-1">
                                                            <p className="text-[11px] font-semibold text-white/40 uppercase tracking-wider">Research field packs</p>
                                                            {researchPacks.map((pack) => {
                                                                const packOn = packSwitches[pack.id] ?? pack.enabled;
                                                                return (
                                                                    <div key={pack.id} className="flex items-start justify-between gap-3 py-1.5">
                                                                        <div className="min-w-0">
                                                                            <p className="text-xs font-medium text-white/80">{pack.label}</p>
                                                                            <p className="text-[11px] text-white/40">
                                                                                contains: {pack.contains.join(', ')}
                                                                            </p>
                                                                            {pack.why_default_off && (
                                                                                <p className="text-[11px] text-amber-300/80 mt-0.5">
                                                                                    Off by default: {pack.why_default_off}
                                                                                </p>
                                                                            )}
                                                                        </div>
                                                                        <button
                                                                            onClick={() => setPackSwitches((p) => ({ ...p, [pack.id]: !packOn }))}
                                                                            className={`relative inline-flex items-center rounded-full transition-colors duration-300 shrink-0 mt-0.5 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/50 ${packOn ? 'bg-primary-500 shadow-lg shadow-primary-500/30' : 'bg-slate-600'}`}
                                                                            style={{ width: 36, height: 20, minHeight: 20, maxHeight: 20, padding: 0 }}
                                                                            role="switch"
                                                                            aria-checked={packOn}
                                                                            aria-label={`Toggle ${pack.label}`}
                                                                        >
                                                                            <span
                                                                                className={`inline-block rounded-full bg-white shadow-md transition-transform duration-300 ${packOn ? 'translate-x-5' : 'translate-x-1'}`}
                                                                                style={{ width: 13, height: 13 }}
                                                                                aria-hidden="true"
                                                                            />
                                                                        </button>
                                                                    </div>
                                                                );
                                                            })}
                                                        </div>
                                                    )}
                                                </motion.div>
                                            );
                                        })}
                                    </div>
                                </div>

                                {/* Public tracker housekeeping. Sits under the modules it
                                    reads like, and saves with the same button. */}
                                <div className="rounded-2xl bg-white/[0.03] backdrop-blur-xl border border-white/10 overflow-hidden shadow-2xl">
                                    <div className="px-5 sm:px-6 py-4 border-b border-white/10 bg-gradient-to-r from-white/[0.05] to-transparent">
                                        <div className="flex items-center gap-3">
                                            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-slate-500/20 to-slate-600/10 border border-slate-500/20 flex items-center justify-center">
                                                <EyeOff className="w-4 h-4 text-slate-300" />
                                            </div>
                                            <div>
                                                <h2 className="text-sm font-semibold text-white">Public Tracker Archival</h2>
                                                <p className="text-xs text-white/40">Keep the public map and tracker readable as the years add up</p>
                                            </div>
                                        </div>
                                    </div>

                                    <div className="px-5 sm:px-6 py-5">
                                        <label className="block text-sm font-medium text-white/70 mb-2" htmlFor="public-archive-days">
                                            Remove closed reports after (days)
                                        </label>
                                        <input
                                            id="public-archive-days"
                                            type="number"
                                            value={publicArchiveDays}
                                            onChange={(e) => setPublicArchiveDays(e.target.value)}
                                            placeholder="e.g. 180"
                                            min="1"
                                            max="36500"
                                            className="w-full bg-white/10 border border-white/20 rounded-lg px-4 py-3 text-white focus:outline-none focus:ring-2 focus:ring-primary-500/50 placeholder:text-white/40"
                                            aria-describedby="public-archive-days-help"
                                        />
                                        <p id="public-archive-days-help" className="text-white/50 text-xs mt-2">
                                            Remove closed reports from the public tracker and map after N days. They stay available by direct link and to staff. Leave empty to keep everything listed.
                                        </p>
                                        <p className="text-white/40 text-xs mt-2">
                                            {publicArchiveDays && Number(publicArchiveDays) > 0
                                                ? `Closed reports drop off the public tracker and map ${publicArchiveDays} days after they are closed. Nothing is deleted, staff keep seeing everything, tracking links keep working, and research exports still include them \u2014 raise or clear this number and the reports come straight back.`
                                                : 'Not set \u2014 every report a resident has not chosen to unlist stays on the public tracker and map.'}
                                        </p>
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* Maps Configuration - part of System Settings */}
                        {currentTab === 'system' && (
                          <>
                            <div className="mt-8 rounded-2xl bg-white/[0.03] backdrop-blur-xl border border-white/10 overflow-hidden shadow-2xl">
                                {/* Section Header */}
                                <div className="px-5 sm:px-6 py-4 border-b border-white/10 bg-gradient-to-r from-white/[0.05] to-transparent">
                                    <div className="flex items-center gap-3">
                                        <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-emerald-500/20 to-emerald-600/10 border border-emerald-500/20 flex items-center justify-center">
                                            <MapPin className="w-4 h-4 text-emerald-400" />
                                        </div>
                                        <div>
                                            <h2 className="text-sm font-semibold text-white">Maps Configuration</h2>
                                            <p className="text-xs text-white/40">Configure map settings and township boundary</p>
                                        </div>
                                    </div>
                                </div>

                                <div className="divide-y divide-white/10 [&>*]:bg-white/[0.015]">
                                {!mapsReady ? (
                                    <div className="px-5 sm:px-6 py-5">
                                        <div className="p-4 rounded-xl bg-gradient-to-br from-yellow-500/10 to-yellow-600/5 border border-yellow-500/20">
                                            <div className="flex items-center gap-3">
                                                <div className="w-9 h-9 rounded-xl bg-yellow-500/20 flex items-center justify-center shrink-0">
                                                    <AlertTriangle className="w-4 h-4 text-yellow-400" />
                                                </div>
                                                <p className="text-sm text-yellow-300">
                                                    A map provider must be configured first — see Service Providers → Maps.
                                                </p>
                                            </div>
                                        </div>
                                    </div>
                                ) : (
                                    <>
                                        {/* Municipality Boundary */}
                                        <div className="px-5 sm:px-6 py-5">
                                            <div className="flex items-center gap-4 mb-5">
                                                <div className="w-11 h-11 rounded-xl bg-gradient-to-br from-blue-500/20 to-blue-600/10 border border-blue-500/20 flex items-center justify-center shrink-0">
                                                    <Globe className="w-5 h-5 text-blue-400" />
                                                </div>
                                                <div className="min-w-0">
                                                    <p className="text-[10px] font-semibold uppercase tracking-wider text-blue-300/70 mb-0.5">Jurisdiction</p>
                                                    <h3 className="text-sm font-semibold text-white">Municipality Boundary</h3>
                                                    <p className="text-xs text-white/50 mt-0.5">Search for your municipality to auto-fetch its boundary polygon</p>
                                                </div>
                                            </div>

                                            {/* Boundary configured status */}
                                            {townshipBoundary && (
                                                <motion.div
                                                    initial={{ opacity: 0, y: 10 }}
                                                    animate={{ opacity: 1, y: 0 }}
                                                    className="p-4 rounded-xl bg-gradient-to-br from-emerald-500/10 to-emerald-600/5 border border-emerald-500/20 mb-5 space-y-4"
                                                >
                                                    <div className="flex items-center justify-between">
                                                        <div className="flex items-center gap-3">
                                                            <div className="w-8 h-8 rounded-lg bg-emerald-500/20 flex items-center justify-center">
                                                                <CircleCheck className="w-4 h-4 text-emerald-400" />
                                                            </div>
                                                            <div>
                                                                <p className="text-sm font-medium text-emerald-300">Boundary Configured</p>
                                                                <p className="text-xs text-white/50 mt-0.5">Displayed on the resident portal map</p>
                                                            </div>
                                                        </div>
                                                        <div className="flex items-center gap-1">
                                                            <Button
                                                                size="sm"
                                                                variant="ghost"
                                                                aria-label="Download boundary GeoJSON"
                                                                onClick={() => {
                                                                    const dataStr = JSON.stringify(townshipBoundary, null, 2);
                                                                    const blob = new Blob([dataStr], { type: 'application/json' });
                                                                    const url = URL.createObjectURL(blob);
                                                                    const a = document.createElement("a");
                                                                    a.href = url;
                                                                    a.download = 'township-boundary.geojson';
                                                                    document.body.appendChild(a);
                                                                    a.click();
                                                                    document.body.removeChild(a);
                                                                    URL.revokeObjectURL(url);
                                                                }}
                                                            >
                                                                <Download className="w-4 h-4" />
                                                            </Button>
                                                            <Button
                                                                size="sm"
                                                                variant="ghost"
                                                                aria-label="Clear municipality boundary"
                                                                onClick={async () => {
                                                                    if (confirm('Are you sure you want to clear the municipality boundary?')) {
                                                                        try {
                                                                            await api.saveTownshipBoundary({});
                                                                            setTownshipBoundary(null);
                                                                            setSaveMessage('Boundary cleared');
                                                                            setTimeout(() => setSaveMessage(null), 3000);
                                                                        } catch (err) {
                                                                            alert("Failed to clear boundary");
                                                                        }
                                                                    }
                                                                }}
                                                            >
                                                                <Trash2 className="w-4 h-4 text-red-400" />
                                                            </Button>
                                                        </div>
                                                    </div>
                                                </motion.div>
                                            )}

                                            {/* Search Input */}
                                            <div className="flex gap-2 mb-4">
                                                <div className="relative flex-1">
                                                    <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-white/30 pointer-events-none z-10" />
                                                    <input
                                                        type="text"
                                                        placeholder="Search municipality (e.g. West Windsor Township, NJ)"
                                                        aria-label="Search for your municipality"
                                                        value={townshipSearch}
                                                        onChange={(e) => setTownshipSearch(e.target.value)}
                                                        onKeyDown={(e) => {
                                                            if (e.key === 'Enter') {
                                                                e.preventDefault();
                                                                handleOsmSearch();
                                                            }
                                                        }}
                                                        className="w-full h-11 pl-11 pr-4 rounded-xl bg-white/[0.06] border border-white/10 text-sm text-white placeholder-white/30 focus:outline-none focus:border-primary-500/50 focus:ring-2 focus:ring-primary-500/20 focus:bg-white/[0.08] transition-all"
                                                        disabled={isSearchingTownship}
                                                    />
                                                </div>
                                                <Button
                                                    onClick={handleOsmSearch}
                                                    isLoading={isSearchingTownship}
                                                    disabled={!townshipSearch.trim()}
                                                    className="bg-gradient-to-r from-primary-500 to-primary-600 hover:from-primary-400 hover:to-primary-500 shadow-lg shadow-primary-500/20"
                                                    leftIcon={<Search className="w-4 h-4" />}
                                                >
                                                    Search
                                                </Button>
                                            </div>

                                            {/* Search Results */}
                                            {osmSearchResults.length > 0 && (
                                                <div className="mb-4 space-y-1.5">
                                                    <p className="text-xs font-medium text-white/40 uppercase tracking-wider mb-2">Results</p>
                                                    {osmSearchResults.map((result, idx) => (
                                                        <motion.button
                                                            key={result.osm_id}
                                                            initial={{ opacity: 0, y: 8 }}
                                                            animate={{ opacity: 1, y: 0 }}
                                                            transition={{ delay: idx * 0.04 }}
                                                            onClick={() => {
                                                                setSelectedOsmResult(result);
                                                                setOsmSearchResults([]);
                                                            }}
                                                            className="w-full px-4 py-3 rounded-xl text-left transition-all bg-white/[0.04] hover:bg-white/[0.08] border border-white/[0.06] hover:border-white/15 group"
                                                        >
                                                            <div className="flex items-center gap-3">
                                                                <div className="w-8 h-8 rounded-lg bg-white/[0.06] flex items-center justify-center shrink-0 group-hover:bg-primary-500/20 transition-colors">
                                                                    <MapPin className="w-3.5 h-3.5 text-white/40 group-hover:text-primary-400 transition-colors" />
                                                                </div>
                                                                <div className="min-w-0">
                                                                    <p className="text-sm text-white font-medium truncate">{result.display_name}</p>
                                                                    <p className="text-xs text-white/30 mt-0.5">
                                                                        ID {result.osm_id} &middot; {result.type}
                                                                    </p>
                                                                </div>
                                                            </div>
                                                        </motion.button>
                                                    ))}
                                                </div>
                                            )}

                                            {/* Selected Municipality */}
                                            {selectedOsmResult && (
                                                <motion.div
                                                    initial={{ opacity: 0, scale: 0.98 }}
                                                    animate={{ opacity: 1, scale: 1 }}
                                                    className="p-4 rounded-xl bg-gradient-to-br from-blue-500/10 to-blue-600/5 border border-blue-500/20 mb-4"
                                                >
                                                    <div className="flex items-center justify-between gap-3">
                                                        <div className="flex items-center gap-3 min-w-0">
                                                            <div className="w-9 h-9 rounded-xl bg-blue-500/20 flex items-center justify-center shrink-0">
                                                                <MapPin className="w-4 h-4 text-blue-400" />
                                                            </div>
                                                            <div className="min-w-0">
                                                                <p className="text-sm font-medium text-white truncate">{selectedOsmResult.display_name}</p>
                                                                <p className="text-xs text-white/40 mt-0.5">OSM ID: {selectedOsmResult.osm_id}</p>
                                                            </div>
                                                        </div>
                                                        <Button
                                                            size="sm"
                                                            onClick={handleFetchBoundary}
                                                            isLoading={isFetchingBoundary}
                                                            className="bg-gradient-to-r from-blue-500 to-blue-600 hover:from-blue-400 hover:to-blue-500 shadow-lg shadow-blue-500/20 shrink-0"
                                                        >
                                                            Fetch Boundary
                                                        </Button>
                                                    </div>
                                                </motion.div>
                                            )}

                                            {/* Upload Divider */}
                                            <div className="flex items-center gap-4 my-5">
                                                <div className="flex-1 h-px bg-white/[0.06]"></div>
                                                <span className="text-xs text-white/25 uppercase tracking-wider">or upload GeoJSON</span>
                                                <div className="flex-1 h-px bg-white/[0.06]"></div>
                                            </div>

                                            {/* GeoJSON Upload */}
                                            <label className="flex items-center gap-4 p-4 rounded-xl bg-white/[0.03] border border-dashed border-white/10 hover:border-white/20 hover:bg-white/[0.05] transition-all cursor-pointer group">
                                                <div className="w-10 h-10 rounded-xl bg-white/[0.06] flex items-center justify-center shrink-0 group-hover:bg-primary-500/15 transition-colors">
                                                    <Upload className="w-4 h-4 text-white/40 group-hover:text-primary-400 transition-colors" />
                                                </div>
                                                <div className="min-w-0">
                                                    <p className="text-sm font-medium text-white/70 group-hover:text-white/90 transition-colors">Upload boundary file</p>
                                                    <p className="text-xs text-white/30">.geojson or .json</p>
                                                </div>
                                                <input
                                                    type="file"
                                                    accept=".geojson,.json"
                                                    aria-label="Upload GeoJSON municipality boundary file"
                                                    className="hidden"
                                                    onChange={async (e) => {
                                                        const file = e.target.files?.[0];
                                                        if (!file) return;
                                                        try {
                                                            const text = await file.text();
                                                            const geojson = JSON.parse(text);
                                                            if (!geojson.type) throw new Error('Invalid GeoJSON format');
                                                            await api.saveTownshipBoundary(geojson, file.name);
                                                            try { await api.seedRoads(true); } catch (e) { console.warn("Road seed failed:", e); }
                                                            setTownshipBoundary(geojson);
                                                            setSelectedOsmResult(null);
                                                            setSaveMessage('GeoJSON boundary uploaded successfully!');
                                                            setTimeout(() => setSaveMessage(null), 3000);
                                                        } catch (err) {
                                                            console.error('Failed to upload GeoJSON:', err);
                                                            alert("Failed to upload GeoJSON. Make sure the file is valid JSON.");
                                                        }
                                                        e.target.value = '';
                                                    }}
                                                />
                                            </label>
                                        </div>

                                        </>
                                    )}
                                </div>
                            </div>

                            {/* Custom Map Layers — its own card/section, matching Maps Configuration above */}
                            {mapsReady && (
                                <div className="mt-6 rounded-2xl bg-white/[0.03] backdrop-blur-xl border border-white/10 overflow-hidden shadow-2xl">
                                    <div className="px-5 sm:px-6 py-4 border-b border-white/10 bg-gradient-to-r from-white/[0.05] to-transparent flex items-center justify-between gap-4">
                                                <div className="flex items-center gap-3">
                                                    <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-violet-500/20 to-violet-600/10 border border-violet-500/20 flex items-center justify-center shrink-0">
                                                        <Layers className="w-4 h-4 text-violet-400" />
                                                    </div>
                                                    <div className="min-w-0">
                                                        <h2 className="text-sm font-semibold text-white">Custom Map Layers</h2>
                                                        <p className="text-xs text-white/40 mt-0.5">Infrastructure assets displayed on the portal map</p>
                                                    </div>
                                                </div>
                                                <Button
                                                    size="sm"
                                                    className="shrink-0 bg-gradient-to-r from-violet-500 to-violet-600 hover:from-violet-400 hover:to-violet-500 shadow-lg shadow-violet-500/20"
                                                    onClick={() => {
                                                        setEditingLayer(null);
                                                        setNewLayer({
                                                            name: '',
                                                            description: '',
                                                            fill_color: '#3b82f6',
                                                            stroke_color: '#1d4ed8',
                                                            fill_opacity: 0.3,
                                                            stroke_width: 2,
                                                            service_codes: [],
                                                            geojson: null,
                                                            layer_type: '',
                                                            routing_mode: 'log',
                                                            routing_config: null,
                                                            visible_on_map: true,
                                                        });
                                                        api.getServices().then(setServices).catch(console.error);
                                                        setShowLayerModal(true);
                                                    }}
                                                    leftIcon={<Plus className="w-4 h-4" />}
                                                >
                                                    Add Layer
                                                </Button>
                                    </div>
                                    <div className="px-5 sm:px-6 py-5">
                                            {mapLayers.length === 0 ? (
                                                <div className="flex flex-col items-center justify-center py-12 rounded-xl border border-dashed border-white/[0.08] bg-white/[0.02]">
                                                    <div className="w-14 h-14 rounded-2xl bg-white/[0.04] flex items-center justify-center mb-3">
                                                        <Layers className="w-6 h-6 text-white/20" />
                                                    </div>
                                                    <p className="text-sm text-white/40 font-medium">No layers configured</p>
                                                    <p className="text-xs text-white/25 mt-1">Upload GeoJSON files to add infrastructure layers</p>
                                                </div>
                                            ) : (
                                                <div className="space-y-2">
                                                    {mapLayers.map((layer, idx) => (
                                                        <motion.div
                                                            key={layer.id}
                                                            initial={{ opacity: 0, y: 10 }}
                                                            animate={{ opacity: 1, y: 0 }}
                                                            transition={{ delay: idx * 0.05 }}
                                                            className="px-4 py-3.5 rounded-xl bg-white/[0.04] border border-white/[0.06] hover:bg-white/[0.06] transition-all group"
                                                        >
                                                            <div className="flex items-center gap-3">
                                                                <div
                                                                    className="w-9 h-9 rounded-lg border-2 shrink-0 shadow-lg"
                                                                    style={{
                                                                        backgroundColor: layer.fill_color + Math.round(layer.fill_opacity * 255).toString(16).padStart(2, '0'),
                                                                        borderColor: layer.stroke_color,
                                                                    }}
                                                                />
                                                                <div className="flex-1 min-w-0">
                                                                    <div className="flex items-center gap-2">
                                                                        <span className="text-sm font-semibold text-white">{layer.name}</span>
                                                                        {layer.layer_type && (
                                                                            <span className="px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider rounded-full bg-white/[0.06] text-white/40 border border-white/[0.06]">
                                                                                {layer.layer_type}
                                                                            </span>
                                                                        )}
                                                                        {!layer.is_active && (
                                                                            <span className="px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider rounded-full bg-red-500/10 text-red-400/60 border border-red-500/10">
                                                                                Disabled
                                                                            </span>
                                                                        )}
                                                                    </div>
                                                                    {layer.description && (
                                                                        <p className="text-xs text-white/35 mt-0.5 truncate">{layer.description}</p>
                                                                    )}
                                                                </div>
                                                                <div className="flex items-center gap-1 shrink-0">
                                                                    <button
                                                                        onClick={async () => {
                                                                            try {
                                                                                await api.updateMapLayer(layer.id, {
                                                                                    visible_on_map: !((layer as any).visible_on_map ?? true),
                                                                                });
                                                                                loadTabData();
                                                                            } catch (err) {
                                                                                console.error('Failed to update layer:', err);
                                                                            }
                                                                        }}
                                                                        className={`p-2 rounded-lg transition-all ${
                                                                            (layer as any).visible_on_map ?? true
                                                                                ? 'text-emerald-400 hover:bg-emerald-500/15'
                                                                                : 'text-white/20 hover:bg-white/5 hover:text-white/40'
                                                                        }`}
                                                                        title={(layer as any).visible_on_map ?? true ? 'Visible on map' : 'Hidden from map'}
                                                                    >
                                                                        {(layer as any).visible_on_map ?? true ? <Eye className="w-4 h-4" /> : <EyeOff className="w-4 h-4" />}
                                                                    </button>
                                                                    <button
                                                                        onClick={() => {
                                                                            setEditingLayer(layer);
                                                                            setNewLayer({
                                                                                name: layer.name,
                                                                                description: layer.description || '',
                                                                                layer_type: (layer as any).layer_type || 'polygon',
                                                                                fill_color: layer.fill_color,
                                                                                stroke_color: layer.stroke_color,
                                                                                fill_opacity: layer.fill_opacity,
                                                                                stroke_width: layer.stroke_width,
                                                                                service_codes: layer.service_codes || [],
                                                                                geojson: layer.geojson,
                                                                                routing_mode: ((layer as any).routing_mode === 'block' ? 'block' : 'log') as 'log' | 'block',
                                                                                routing_config: (layer as any).routing_config || null,
                                                                                visible_on_map: (layer as any).visible_on_map ?? true,
                                                                            });
                                                                            api.getServices().then(setServices).catch(console.error);
                                                                            setShowLayerModal(true);
                                                                        }}
                                                                        aria-label={`Edit ${layer.name}`}
                                                                        className="p-2 rounded-lg text-white/30 hover:text-white/60 hover:bg-white/5 transition-all"
                                                                    >
                                                                        <Edit className="w-4 h-4" />
                                                                    </button>
                                                                    <button
                                                                        onClick={async () => {
                                                                            if (!confirm(`Delete layer "${layer.name}"?`)) return;
                                                                            try {
                                                                                await api.deleteMapLayer(layer.id);
                                                                                loadTabData();
                                                                                setSaveMessage('Layer deleted');
                                                                                setTimeout(() => setSaveMessage(null), 3000);
                                                                            } catch (err) {
                                                                                console.error('Failed to delete layer:', err);
                                                                            }
                                                                        }}
                                                                        aria-label={`Delete ${layer.name}`}
                                                                        className="p-2 rounded-lg text-white/20 hover:text-red-400 hover:bg-red-500/10 transition-all"
                                                                    >
                                                                        <Trash2 className="w-4 h-4" />
                                                                    </button>
                                                                </div>
                                                            </div>
                                                        </motion.div>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                )}
                            </>
                        )}

                        {/* Document Retention Tab */}
                        {currentTab === 'compliance' && (
                            <div className="space-y-6">
                                <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
                                    <h2 className="text-2xl font-bold text-white mb-2">Document Retention</h2>
                                    <p className="text-white/60">
                                        Set how long this town keeps closed requests, and what is removed
                                        when that period is up.
                                    </p>
                                </motion.div>

                                {/* Nothing is running, and somebody has to keep being told.

                                    This tab used to headline "OPRA · 7 years" at every town,
                                    because the state code defaulted to New Jersey and sat in
                                    front of a table of periods nobody had verified. Retention
                                    waits for an answer now, which is safe but silent — and the
                                    silence has a cost of its own: with no period, nothing is
                                    ever deleted, so every name, phone number and description a
                                    resident has submitted is still on the record. That is what
                                    this says, rather than "not configured". */}
                                {retentionPolicy && !retentionPolicy.configured && (
                                    <motion.div
                                        initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
                                        role="status"
                                        className="rounded-xl border border-amber-400/40 bg-amber-500/10 p-5"
                                    >
                                        <div className="flex gap-3 items-start">
                                            <AlertTriangle className="w-5 h-5 text-amber-300 flex-shrink-0 mt-0.5" aria-hidden="true" />
                                            <div className="space-y-2">
                                                <h3 className="text-white font-semibold">
                                                    Resident personal data is being kept indefinitely
                                                </h3>
                                                <p className="text-white/75 text-sm leading-relaxed">
                                                    {retentionPolicy.detail}
                                                </p>
                                                <p className="text-white/60 text-sm leading-relaxed">
                                                    Nothing has been deleted or redacted in the meantime, so no
                                                    record has been lost to this — closed requests are all still
                                                    here, including any past the date your published policy gives.
                                                    That is the safer of the two failures, and it is still a
                                                    failure: keeping personal data longer than you need it is its
                                                    own obligation.
                                                </p>
                                                <p className="text-white/50 text-sm leading-relaxed">
                                                    Set a retention period below to start it.
                                                </p>
                                            </div>
                                        </div>
                                    </motion.div>
                                )}

                                {/* Current Policy Status */}
                                {activeRetention && (
                                    <AccordionSection
                                        title="Current Policy"
                                        subtitle={`${formatYears(activeRetention.retention_days)} • ${activeRetention.mode === 'purge' ? 'Purge mode' : 'Redact mode'}`}
                                        icon={Shield}
                                        iconClassName="text-green-400"
                                        badge={<Badge variant="success">Active</Badge>}
                                        defaultOpen={true}
                                    >
                                        {/* Two panels, not three. The third headlined the
                                            public-records law this town was said to be complying
                                            with, from a table the product had made up. */}
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
                                            <div className="bg-white/5 rounded-lg p-4">
                                                <div className="text-white/60 text-sm">Retention Period</div>
                                                <div className="text-2xl font-bold text-amber-400">{formatYears(activeRetention.retention_days)}</div>
                                                <div className="text-white/40 text-sm">
                                                    {activeRetention.retention_days.toLocaleString()} days • set by this town
                                                </div>
                                            </div>
                                            <div className="bg-white/5 rounded-lg p-4">
                                                <div className="text-white/60 text-sm">Mode</div>
                                                <div className="text-2xl font-bold text-white capitalize">{activeRetention.mode}</div>
                                                <div className="text-white/40 text-sm">{activeRetention.mode === 'purge' ? 'Every field cleared, row kept' : 'Chosen fields cleared, row kept'}</div>
                                            </div>
                                        </div>

                                        {/* Archival Stats */}
                                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                            <button
                                                onClick={openEligibleRecords}
                                                className="bg-blue-500/10 border border-blue-500/30 rounded-lg p-4 text-left hover:bg-blue-500/20 hover:border-blue-500/50 transition-all cursor-pointer"
                                            >
                                                <div className="text-blue-300 text-sm">Eligible for Archival</div>
                                                <div className="text-2xl font-bold text-blue-300">{activeRetention.stats.eligible_for_archival}</div>
                                                <div className="text-blue-200 text-xs mt-1">Click to view →</div>
                                            </button>
                                            <button
                                                onClick={async () => {
                                                    setIsLoadingLegalHold(true);
                                                    try {
                                                        const result = await api.getLegalHoldRequests();
                                                        setLegalHoldRequests(result.requests);
                                                        setShowLegalHoldModal(true);
                                                    } catch (err) {
                                                        console.error('Failed to load legal hold requests:', err);
                                                    } finally {
                                                        setIsLoadingLegalHold(false);
                                                    }
                                                }}
                                                className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-4 text-left hover:bg-amber-500/20 hover:border-amber-500/50 transition-all cursor-pointer"
                                            >
                                                <div className="text-amber-400 text-sm">Under Legal Hold</div>
                                                <div className="text-2xl font-bold text-amber-400">{activeRetention.stats.under_legal_hold}</div>
                                                <div className="text-amber-300 text-xs mt-1">Click to view →</div>
                                            </button>
                                            <div className="bg-green-500/10 border border-green-500/30 rounded-lg p-4">
                                                <div className="text-green-400 text-sm">Already Archived</div>
                                                <div className="text-2xl font-bold text-green-400">{activeRetention.stats.already_archived}</div>
                                            </div>
                                        </div>
                                        {/* One period for every closed request. A real records
                                            schedule sets different periods per class of record;
                                            exceptions are handled with a legal hold. Kept as a
                                            code comment rather than on-screen copy. */}
                                    </AccordionSection>
                                )}


                                {/* Soft-Deleted Requests */}
                                {deletedRequests.length > 0 && (
                                    <AccordionSection
                                        title={`Soft-Deleted Requests`}
                                        subtitle="Requests removed by administrators but recoverable"
                                        icon={Trash2}
                                        iconClassName="text-red-400"
                                        badge={<Badge variant="danger">{deletedRequests.length}</Badge>}
                                    >
                                        <p className="text-white/60 mb-4 text-sm">
                                            These requests have been soft-deleted by administrators and are no longer visible to staff.
                                        </p>
                                        <div className="space-y-3 max-h-64 overflow-y-auto">
                                            {deletedRequests.map((req) => (
                                                <div key={req.id} className="bg-red-500/10 border border-red-500/30 rounded-lg p-3">
                                                    <div className="flex justify-between items-start">
                                                        <div>
                                                            <span className="text-white font-medium">{req.service_request_id}</span>
                                                            <p className="text-white/60 text-sm mt-1 line-clamp-1">{req.description}</p>
                                                        </div>
                                                        <span className="text-red-400 text-xs">
                                                            {req.deleted_at ? new Date(req.deleted_at).toLocaleDateString() : 'N/A'}
                                                        </span>
                                                    </div>
                                                    {req.delete_justification && (
                                                        <div className="mt-2 bg-black/20 rounded p-2">
                                                            <span className="text-xs text-white/50">Deletion Reason:</span>
                                                            <p className="text-sm text-red-300">{req.delete_justification}</p>
                                                        </div>
                                                    )}
                                                    <div className="flex items-center justify-between mt-2">
                                                        <p className="text-xs text-white/40">
                                                            Deleted by: {req.deleted_by || 'Unknown'}
                                                        </p>
                                                        <button
                                                            onClick={async () => {
                                                                if (window.confirm(`Restore request ${req.service_request_id}? This will make it visible to staff again.`)) {
                                                                    try {
                                                                        await api.restoreRequest(req.service_request_id);
                                                                        // Remove from deleted list
                                                                        setDeletedRequests(prev => prev.filter(r => r.id !== req.id));
                                                                    } catch (err) {
                                                                        console.error('Failed to restore:', err);
                                                                        alert("Failed to restore request");
                                                                    }
                                                                }
                                                            }}
                                                            className="px-3 py-1 bg-green-500/20 text-green-400 rounded text-xs hover:bg-green-500/30 transition-colors flex items-center gap-1"
                                                        >
                                                            <RotateCcw className="w-3 h-3" />
                                                            Restore
                                                        </button>
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    </AccordionSection>
                                )}

                                {/* The two answers that make a policy */}
                                <AccordionSection
                                    title="Retention Policy Configuration"
                                    subtitle="How long records are kept, and what is removed when that is up"
                                    icon={Clock}
                                    iconClassName="text-amber-400"
                                >
                                    {/* No state picker, and no default period. It looked up a
                                        retention period in a table of all 51 US jurisdictions
                                        that nobody had checked — 41 of them five years, each
                                        citing that state's records authority as its source,
                                        which is what made it look researched. Both answers
                                        below come from the town. */}

                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                        <div>
                                            <label className="block text-sm font-medium text-white/70 mb-2" htmlFor="retention-days">
                                                Keep closed requests for (days)
                                            </label>
                                            <input
                                                id="retention-days"
                                                type="number"
                                                value={retentionDays}
                                                onChange={(e) => setRetentionDays(e.target.value)}
                                                placeholder="e.g. 2555"
                                                min="1"
                                                max="36500"
                                                className="w-full bg-white/10 border border-white/20 rounded-lg px-4 py-3 text-white focus:outline-none focus:ring-2 focus:ring-amber-400 placeholder:text-white/40"
                                            />
                                            {/* Shown in years as it is typed. A schedule states
                                                "7 years" and this field takes days, so 7 typed
                                                here is a week — and a week is a policy that
                                                destroys almost everything on its first run. */}
                                            <p className="text-white/50 text-xs mt-1">
                                                {retentionDays && parseInt(retentionDays) > 0
                                                    ? `${formatYears(parseInt(retentionDays))} — records are cleared once they have been closed this long.`
                                                    : 'Not set, so nothing is ever cleared. Enter the period in days: 5 years is 1825, 7 years is 2555.'}
                                            </p>
                                        </div>

                                        <div>
                                            <label className="block text-sm font-medium text-white/70 mb-2">Archival Mode</label>
                                            <select
                                                value={selectedMode}
                                                onChange={(e) => setSelectedMode(e.target.value as 'redact' | 'purge')}
                                                className="w-full bg-white/10 border border-white/20 rounded-lg px-4 py-3 text-white focus:outline-none focus:ring-2 focus:ring-amber-400"
                                                aria-label="Select archival mode"
                                            >
                                                {/* Not "anonymise". Anonymising means removing what
                                                    ties data to a person; this also clears the
                                                    description and the staff notes, which is
                                                    redaction. The two are not interchangeable when
                                                    the difference is what a town tells a judge. */}
                                                <option value="redact" className="bg-slate-800">Redact — clear the fields you choose, keep the record</option>
                                                {/* Not deletion. It could never have worked --
                                                    NOT NULL foreign keys from the audit log and the
                                                    comments, with no cascade -- and making it work
                                                    meant deleting audit rows that form the
                                                    tamper-evident chain. Clearing every field
                                                    removes the personal data and leaves a row that
                                                    still counts. */}
                                                <option value="purge" className="bg-slate-800">Purge — clear every field, keep the row for counting</option>
                                            </select>
                                        </div>
                                    </div>

                                    {/* What a run actually clears.
                                        This was fixed in code -- names, email, phone, description,
                                        staff notes and photos, always -- and when it became a
                                        catalog those same seven arrived pre-ticked. Inherited that
                                        way they were never anybody's decision, and the decision
                                        they stood in for is which of a resident's details this
                                        town destroys permanently. Nothing is ticked now. */}
                                    {selectedMode !== 'purge' && scrubFields && (
                                        <div className="mt-6 rounded-2xl border border-white/10 bg-white/[0.03] p-5">
                                            <h4 className="font-semibold text-white">What gets cleared</h4>
                                            <p className="text-white/55 text-xs mt-1 mb-4">
                                                The record stays and still counts in your statistics. Only the
                                                fields ticked here are emptied, and nothing is ticked until you
                                                tick it — this list is what a run destroys for good.
                                            </p>
                                            {/* auto-rows-fr plus h-full: every card in a row is
                                                the height of the tallest, so a two-line
                                                description does not leave the card beside it
                                                floating in a short box. */}
                                            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 auto-rows-fr">
                                                {scrubFields.map(field => (
                                                    <label
                                                        key={field.id}
                                                        className={`group flex h-full items-start gap-3 rounded-xl border p-3.5 cursor-pointer transition-colors ${field.selected
                                                            ? 'bg-amber-500/10 border-amber-400/30'
                                                            : 'bg-white/[0.02] border-white/10 hover:border-white/20'}`}
                                                    >
                                                        {/* Pinned to the title's cap height rather
                                                            than centred on the text block. Centring
                                                            put the box beside the title on a
                                                            one-line card and beside the description
                                                            on a two-line one, so no two rows lined
                                                            up and the grid read as ragged. */}
                                                        <input
                                                            type="checkbox"
                                                            checked={field.selected}
                                                            onChange={(e) => setScrubFields(prev => (prev || []).map(f =>
                                                                f.id === field.id ? { ...f, selected: e.target.checked } : f))}
                                                            className="mt-[3px] w-4 h-4 shrink-0 self-start accent-amber-400"
                                                        />
                                                        <span className="min-w-0 flex-1">
                                                            <span className="block text-sm font-medium text-white/90 leading-5">{field.label}</span>
                                                            <span className="block text-xs text-white/60 mt-1 leading-relaxed">{field.detail}</span>
                                                        </span>
                                                    </label>
                                                ))}
                                            </div>
                                            {!scrubFields.some(f => f.selected) && (
                                                <p className="text-amber-300 text-xs mt-3">
                                                    Nothing is ticked, so retention will not run at all. A run that
                                                    cleared nothing would still mark records archived, which takes
                                                    them out of every future run — the personal data would stay on
                                                    them permanently, with nothing left to notice.
                                                </p>
                                            )}
                                        </div>
                                    )}

                                    {/* What the two answers add up to, in a sentence, before
                                        anything is saved. */}
                                    {(() => {
                                        const days = parseInt(retentionDays) || 0;
                                        const chosen = (scrubFields || []).filter(f => f.selected);
                                        const willRun = days > 0 && (selectedMode === 'purge' || chosen.length > 0);
                                        return (
                                            <div className={`mt-4 p-4 rounded-lg border ${willRun
                                                ? 'bg-amber-500/10 border-amber-500/30'
                                                : 'bg-white/[0.03] border-white/15'}`}>
                                                <div className="flex items-start gap-3">
                                                    <Clock className={`w-5 h-5 mt-0.5 ${willRun ? 'text-amber-400' : 'text-white/40'}`} aria-hidden="true" />
                                                    <div>
                                                        {willRun ? (
                                                            <>
                                                                <p className="text-amber-400 font-medium">
                                                                    Closed requests are kept {formatYears(days)}, then{' '}
                                                                    {selectedMode === 'purge'
                                                                        ? 'every field on them is cleared'
                                                                        : `${chosen.length} ${chosen.length === 1 ? 'field is' : 'fields are'} cleared`}.
                                                                </p>
                                                                {selectedMode !== 'purge' && (
                                                                    <p className="text-white/70 text-sm mt-1">
                                                                        Cleared: {chosen.map(f => f.label).join(', ')}.
                                                                    </p>
                                                                )}
                                                                <p className="text-white/45 text-xs mt-1">
                                                                    One period for every request, whatever it was about.
                                                                    Anything you must keep longer needs a legal hold on it.
                                                                </p>
                                                            </>
                                                        ) : (
                                                            <p className="text-white/70 text-sm">
                                                                {days > 0
                                                                    ? 'A period is set but nothing is ticked, so retention will not run.'
                                                                    : 'No period is set, so retention will not run and resident personal data is kept indefinitely.'}
                                                            </p>
                                                        )}
                                                    </div>
                                                </div>
                                            </div>
                                        );
                                    })()}

                                    <div className="flex gap-4 mt-6">
                                        <Button
                                            onClick={async () => {
                                                setIsSavingRetention(true);
                                                try {
                                                    const saved = await api.updateRetentionPolicy({
                                                        mode: selectedMode,
                                                        scrub_fields: (scrubFields || [])
                                                            .filter(f => f.selected).map(f => f.id),
                                                        // 0 explicitly clears the period, which stops
                                                        // retention running. Omitting it would leave a
                                                        // previously-set period stuck with no way to remove it.
                                                        retention_days: retentionDays ? parseInt(retentionDays) : 0
                                                    });
                                                    await loadTabData();
                                                    /* The server decides whether this is a policy, and says
                                                       so. "Updated successfully" over a save that left
                                                       retention switched off is the quiet the warning above
                                                       exists to break. */
                                                    setSaveMessage(saved.configured
                                                        ? 'Retention policy saved and now in force'
                                                        : `Saved, but retention still will not run. ${saved.detail || ''}`);
                                                    setTimeout(() => setSaveMessage(null), 8000);
                                                } catch (err) {
                                                    reportError('Could not apply the retention policy', err);
                                                } finally {
                                                    setIsSavingRetention(false);
                                                }
                                            }}
                                            variant="secondary"
                                            disabled={isSavingRetention}
                                            className="bg-gradient-to-r from-amber-500 to-orange-600 hover:from-amber-400 hover:to-orange-500 text-white font-semibold border-amber-400/40 shadow-lg shadow-orange-900/30"
                                        >
                                            {isSavingRetention ? 'Saving...' : 'Apply Policy'}
                                        </Button>

                                        <Button
                                            variant="secondary"
                                            onClick={async () => {
                                                /* Show the numbers, then ask.
                                                 *
                                                 * This button used to run
                                                 * immediately. In delete mode
                                                 * that permanently destroys
                                                 * resident records, and the
                                                 * response came back before
                                                 * the task had touched
                                                 * anything, so nothing on
                                                 * screen could say what had
                                                 * happened. */
                                                setIsRunningRetention(true);
                                                let plan;
                                                try {
                                                    plan = await api.previewRetentionRun();
                                                } catch (err) {
                                                    setIsRunningRetention(false);
                                                    reportError('Could not work out what this would affect', err);
                                                    return;
                                                }
                                                setIsRunningRetention(false);

                                                if (plan.blocked === 'legal_hold') {
                                                    await dialog.alert({
                                                        title: 'Legal hold is on',
                                                        message: 'Nothing will be archived or deleted while the instance-wide legal hold is active. Lift it first if this run is meant to happen.',
                                                        variant: 'info',
                                                    });
                                                    return;
                                                }
                                                /* Before the "nothing is due" branch below, which would
                                                   otherwise report an empty run as a quiet success —
                                                   "no records have passed the null-day retention
                                                   period" is not the reason nothing would happen. */
                                                if (plan.blocked === 'unconfigured') {
                                                    await dialog.alert({
                                                        title: 'No retention schedule is set',
                                                        message: plan.detail
                                                            || 'Set how long records are kept and what a run removes before running retention. Until both are set there is nothing to measure a record against.',
                                                        variant: 'warning',
                                                    });
                                                    return;
                                                }
                                                if (!plan.eligible) {
                                                    await dialog.alert({
                                                        title: 'Nothing is due yet',
                                                        message: `No closed records have passed the ${plan.retention_days}-day retention period, so this run would do nothing.`,
                                                        variant: 'info',
                                                    });
                                                    return;
                                                }

                                                const purging = plan.mode === 'purge';
                                                const ok = await dialog.confirm({
                                                    title: purging ? 'Clear every field on these records' : 'Redact these records',
                                                    variant: purging ? 'danger' : 'warning',
                                                    confirmText: purging ? 'Clear them' : 'Redact them',
                                                    requireTyped: plan.confirmation_required || undefined,
                                                    message: (
                                                        <div className="space-y-3">
                                                            <p>
                                                                <strong className="text-white">{plan.will_act_on ?? plan.eligible}</strong>{' '}
                                                                closed {(plan.will_act_on ?? plan.eligible) === 1 ? 'record has' : 'records have'} passed
                                                                the {plan.retention_days}-day retention period this town set.
                                                            </p>
                                                            <p>
                                                                {purging
                                                                    ? 'Every field is emptied — names, contact details, what the resident wrote, photos, comments and the map pin. The rows stay so your totals do not change, but nothing about the people is recoverable.'
                                                                    : 'Only the fields you ticked are cleared. The rows stay, so the counts and anything you have not ticked are untouched.'}
                                                            </p>
                                                            {/* Named, from the server's own list. A
                                                                clerk approving this should not have
                                                                to remember which boxes are ticked on
                                                                a screen they cannot see right now. */}
                                                            {!!plan.scrub_fields?.length && (
                                                                <p className="text-white/70 text-sm">
                                                                    Cleared: {plan.scrub_fields.join(', ')}.
                                                                </p>
                                                            )}
                                                            {!!plan.on_legal_hold && (
                                                                <p className="text-amber-300">
                                                                    {plan.on_legal_hold} flagged {plan.on_legal_hold === 1 ? 'record is' : 'records are'} under
                                                                    legal hold and will be left alone.
                                                                </p>
                                                            )}
                                                            <p className="text-slate-400 text-sm">
                                                                It works through every eligible record, not the first hundred.
                                                            </p>
                                                        </div>
                                                    ),
                                                });
                                                if (!ok) return;

                                                setIsRunningRetention(true);
                                                try {
                                                    const result = await api.runRetentionNow(plan.confirmation_required || undefined);
                                                    setSaveMessage(`Retention task started: ${result.message}`);
                                                    setTimeout(() => {
                                                        setSaveMessage(null);
                                                        loadTabData();
                                                    }, 3000);
                                                } catch (err) {
                                                    reportError('Could not start the retention task', err);
                                                } finally {
                                                    setIsRunningRetention(false);
                                                }
                                            }}
                                            disabled={isRunningRetention}
                                        >
                                            {isRunningRetention ? 'Running...' : 'Run Retention Now'}
                                        </Button>

                                        {/* Read before you press. A count cannot
                                            show that the oldest eligible record is
                                            four years past its date because the
                                            policy has never actually run, nor that
                                            something assumed exempt is in the list
                                            because nobody set the hold. */}
                                        <Button
                                            variant="secondary"
                                            disabled={previewLoading}
                                            onClick={async () => {
                                                setPreviewLoading(true);
                                                setPreviewError(null);
                                                try {
                                                    setRetentionPreview(await api.previewRetentionRun(50));
                                                } catch (err) {
                                                    setRetentionPreview(null);
                                                    setPreviewError(err instanceof Error ? err.message : 'Could not load the preview');
                                                } finally {
                                                    setPreviewLoading(false);
                                                }
                                            }}
                                        >
                                            {previewLoading ? 'Checking…' : 'Review what would be archived'}
                                        </Button>

                                        <Button
                                            variant="secondary"
                                            onClick={async () => {
                                                setExportOpen(o => !o);
                                                if (!exportFields.length) {
                                                    try {
                                                        const { fields } = await api.getPublicRecordsFields();
                                                        setExportFields(fields);
                                                        setExportChosen(new Set(fields.filter(f => f.selected).map(f => f.id)));
                                                    } catch (err) {
                                                        reportError('Could not load the export options', err);
                                                    }
                                                }
                                            }}
                                        >
                                            {/* Named for the job, not for a statute. The label used
                                                to read "Export for OPRA" everywhere, from the same
                                                unverified table. */}
                                            Export for a records request
                                        </Button>
                                    </div>

                                    {/* Pick what is being released.
                                        A custodian is answering a specific
                                        request. Over-disclosure is the failure
                                        that matters: a phone number released in
                                        answer to a request that did not cover it
                                        cannot be recalled, and the person whose
                                        number it was never knew it was in scope. */}
                                    {exportOpen && (
                                        <div className="mt-4 rounded-xl border border-white/10 bg-white/[0.03] p-4 space-y-4">
                                            <div className="grid gap-3 sm:grid-cols-2">
                                                <label className="text-sm">
                                                    <span className="block text-white/60 mb-1">Submitted from</span>
                                                    <input type="date" value={exportStart} onChange={e => setExportStart(e.target.value)}
                                                        className="glass-input w-full" />
                                                </label>
                                                <label className="text-sm">
                                                    <span className="block text-white/60 mb-1">Submitted to</span>
                                                    <input type="date" value={exportEnd} onChange={e => setExportEnd(e.target.value)}
                                                        className="glass-input w-full" />
                                                    <span className="block text-white/40 text-xs mt-1">Includes the whole of that day.</span>
                                                </label>
                                            </div>

                                            <div>
                                                <span className="block text-white/60 text-sm mb-2">Status</span>
                                                <div className="flex flex-wrap gap-3">
                                                    {['open', 'in_progress', 'closed'].map(v => (
                                                        <label key={v} className="flex items-center gap-2 text-sm text-white/80">
                                                            <input type="checkbox" checked={exportStatuses.has(v)}
                                                                onChange={e => setExportStatuses(prev => {
                                                                    const next = new Set(prev);
                                                                    e.target.checked ? next.add(v) : next.delete(v);
                                                                    return next;
                                                                })}
                                                                className="w-4 h-4 rounded border-white/20 bg-transparent" />
                                                            {v === 'in_progress' ? 'In progress' : v.charAt(0).toUpperCase() + v.slice(1)}
                                                        </label>
                                                    ))}
                                                    <span className="text-white/40 text-xs self-center">None ticked means all.</span>
                                                </div>
                                            </div>

                                            <label className="block text-sm">
                                                <span className="block text-white/60 mb-1">Specific request IDs (optional)</span>
                                                <input type="text" value={exportIds} onChange={e => setExportIds(e.target.value)}
                                                    placeholder="REQ-20260101-AB12CD34, REQ-…"
                                                    className="glass-input w-full" />
                                            </label>

                                            <div>
                                                <span className="block text-white/60 text-sm mb-2">Fields to include</span>
                                                <div className="grid gap-2 sm:grid-cols-2">
                                                    {exportFields.map(f => (
                                                        <label key={f.id} className="flex items-start gap-2 text-sm">
                                                            <input type="checkbox" checked={exportChosen.has(f.id)}
                                                                onChange={e => setExportChosen(prev => {
                                                                    const next = new Set(prev);
                                                                    e.target.checked ? next.add(f.id) : next.delete(f.id);
                                                                    return next;
                                                                })}
                                                                className="w-4 h-4 mt-0.5 rounded border-white/20 bg-transparent" />
                                                            <span>
                                                                <span className={f.sensitive ? 'text-amber-200' : 'text-white/80'}>
                                                                    {f.label}
                                                                </span>
                                                                {f.sensitive && (
                                                                    <span className="ml-1 text-[10px] uppercase tracking-wider text-amber-300/80">
                                                                        identifies the reporter
                                                                    </span>
                                                                )}
                                                                {f.note && <span className="block text-white/45 text-xs">{f.note}</span>}
                                                            </span>
                                                        </label>
                                                    ))}
                                                </div>
                                            </div>

                                            {/* Said before the download, not after. */}
                                            {exportFields.some(f => f.sensitive && exportChosen.has(f.id)) && (
                                                <p role="alert" className="text-sm text-amber-200 bg-amber-500/10 border border-amber-500/30 rounded-lg p-3">
                                                    This export will identify the people who filed these reports. Release
                                                    it only if the request covers their details. It is recorded in the
                                                    audit log either way.
                                                </p>
                                            )}

                                            <div className="flex items-center gap-3">
                                                <Button
                                                    disabled={exportBusy || exportChosen.size === 0}
                                                    onClick={async () => {
                                                        setExportBusy(true);
                                                        try {
                                                            await api.exportForPublicRecords({
                                                                startDate: exportStart || undefined,
                                                                endDate: exportEnd || undefined,
                                                                statuses: exportStatuses.size ? [...exportStatuses] : undefined,
                                                                requestIds: exportIds.split(',').map(v => v.trim()).filter(Boolean) || undefined,
                                                                fields: [...exportChosen],
                                                            });
                                                            setSaveMessage('Export downloaded');
                                                            setTimeout(() => setSaveMessage(null), 3000);
                                                        } catch (err) {
                                                            reportError('The export failed', err);
                                                        } finally {
                                                            setExportBusy(false);
                                                        }
                                                    }}
                                                >
                                                    {exportBusy ? 'Preparing…' : 'Download export'}
                                                </Button>
                                                {exportChosen.size === 0 && (
                                                    <span className="text-white/50 text-sm">Choose at least one field.</span>
                                                )}
                                            </div>
                                        </div>
                                    )}

                                    {previewError && (
                                        <div role="alert" className="mt-4 rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-200">
                                            {previewError}
                                        </div>
                                    )}

                                    {retentionPreview && (
                                        <div className="mt-4 rounded-xl border border-white/10 bg-white/[0.03] overflow-hidden">
                                            {retentionPreview.blocked === 'legal_hold' ? (
                                                <p className="p-4 text-sm text-amber-200">
                                                    The instance-wide legal hold is on, so nothing is eligible. Lift it
                                                    first if this run is meant to happen.
                                                </p>
                                            ) : retentionPreview.blocked === 'unconfigured' ? (
                                                /* Not "nothing is eligible". Nothing has been *measured* —
                                                   there is no retention period at all, and every closed
                                                   record is still here regardless of age. */
                                                <p className="p-4 text-sm text-amber-200">
                                                    {retentionPreview.detail
                                                        || 'No retention period is set, so there is nothing to measure records against and nothing can run.'}
                                                </p>
                                            ) : !retentionPreview.summary || retentionPreview.summary.total === 0 ? (
                                                <p className="p-4 text-sm text-white/70">
                                                    Nothing has passed the {(retentionPreview.retention_days ?? 0).toLocaleString()}-day
                                                    retention period yet. This run would do nothing.
                                                </p>
                                            ) : (
                                                <>
                                                    <div className="p-4 border-b border-white/10">
                                                        <p className="text-sm text-white">
                                                            <strong>{retentionPreview.summary.total.toLocaleString()}</strong>{' '}
                                                            {retentionPreview.summary.total === 1 ? 'record is' : 'records are'} eligible.
                                                            {retentionPreview.summary.oldest_age_days != null && (
                                                                <> Closed between{' '}
                                                                    {Math.floor((retentionPreview.summary.newest_age_days ?? 0) / 365)} and{' '}
                                                                    {Math.floor(retentionPreview.summary.oldest_age_days / 365)} years ago.</>
                                                            )}
                                                        </p>
                                                        {/* Said plainly. A list of fifty presented as
                                                            the whole answer, when it is fifty of four
                                                            thousand, is the undercount this panel is
                                                            here to prevent. */}
                                                        {retentionPreview.summary.truncated && (
                                                            <p className="text-xs text-amber-200/90 mt-1">
                                                                Showing the {retentionPreview.summary.showing} oldest. The run
                                                                works through all {retentionPreview.summary.total.toLocaleString()}.
                                                            </p>
                                                        )}
                                                        {!!retentionPreview.scrub_fields?.length && (
                                                            <p className="text-xs text-white/60 mt-2">
                                                                Cleared on each: {retentionPreview.scrub_fields!.join(', ')}.
                                                            </p>
                                                        )}
                                                    </div>
                                                    <div className="max-h-80 overflow-y-auto">
                                                        <table className="w-full text-sm">
                                                            <thead className="sticky top-0 bg-slate-900/95 backdrop-blur">
                                                                <tr className="text-left text-white/55 text-xs uppercase tracking-wider">
                                                                    <th className="px-4 py-2 font-medium">Request</th>
                                                                    <th className="px-4 py-2 font-medium">Category</th>
                                                                    <th className="px-4 py-2 font-medium">Closed</th>
                                                                    <th className="px-4 py-2 font-medium text-right">Age</th>
                                                                    <th className="px-4 py-2 font-medium text-right">Past due</th>
                                                                </tr>
                                                            </thead>
                                                            <tbody>
                                                                {retentionPreview.records.map(r => (
                                                                    <tr key={r.service_request_id} className="border-t border-white/5">
                                                                        <td className="px-4 py-2 font-mono text-xs text-white/80">{r.service_request_id}</td>
                                                                        <td className="px-4 py-2 text-white/70 truncate max-w-[16rem]">{r.service_name}</td>
                                                                        <td className="px-4 py-2 text-white/70">
                                                                            {r.closed_datetime
                                                                                ? new Date(r.closed_datetime).toLocaleDateString(undefined, {
                                                                                    year: 'numeric', month: 'short', day: 'numeric',
                                                                                    timeZone: retentionPreview.timezone || undefined,
                                                                                })
                                                                                : '—'}
                                                                        </td>
                                                                        <td className="px-4 py-2 text-right text-white/70">
                                                                            {r.age_days != null ? `${(r.age_days / 365).toFixed(1)} yrs` : '—'}
                                                                        </td>
                                                                        <td className="px-4 py-2 text-right text-amber-200/90">
                                                                            {r.days_past_retention != null ? `${r.days_past_retention.toLocaleString()} d` : '—'}
                                                                        </td>
                                                                    </tr>
                                                                ))}
                                                            </tbody>
                                                        </table>
                                                    </div>
                                                </>
                                            )}
                                        </div>
                                    )}
                                </AccordionSection>

                                {/* The "All State Policies" reference table is gone. It
                                    listed a retention period and a records authority for each
                                    of 51 jurisdictions, presented as a reference guide, and
                                    the numbers were not researched: 41 of the 51 said five
                                    years. A clerk checking their own schedule against it would
                                    have been checking it against nothing. */}

                                {/* Database Backups */}
                                <AccordionSection
                                    title="Database Backups"
                                    subtitle="Encrypted S3-compatible backup management"
                                    icon={Upload}
                                    iconClassName="text-blue-400"
                                >
                                    {backupStatus === null ? (
                                        <div className="text-center py-4">
                                            <Button
                                                variant="ghost"
                                                onClick={async () => {
                                                    setIsLoadingBackups(true);
                                                    try {
                                                        const [status, list] = await Promise.all([
                                                            api.getBackupStatus(),
                                                            api.listBackups()
                                                        ]);
                                                        setBackupStatus(status);
                                                        setBackups(list.backups || []);
                                                    } catch (err) {
                                                        console.error('Failed to load backups:', err);
                                                    } finally {
                                                        setIsLoadingBackups(false);
                                                    }
                                                }}
                                                disabled={isLoadingBackups}
                                            >
                                                <RefreshCw className={`w-4 h-4 mr-2 ${isLoadingBackups ? 'animate-spin' : ''}`} />
                                                {isLoadingBackups ? 'Loading...' : 'Load Backup Status'}
                                            </Button>
                                        </div>
                                    ) : !backupStatus.configured ? (
                                        <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-4">
                                            <p className="text-amber-400 mb-3">
                                                {backupStatus.message || 'Backup not configured'}
                                            </p>
                                            {backupStatus.required_secrets && (
                                                <div className="text-white/60 text-sm">
                                                    <p className="mb-2">Add these secrets in the Secrets tab:</p>
                                                    <ul className="list-disc list-inside space-y-1">
                                                        {backupStatus.required_secrets.map(s => (
                                                            <li key={s} className="font-mono text-xs">{s}</li>
                                                        ))}
                                                    </ul>
                                                </div>
                                            )}
                                        </div>
                                    ) : (
                                        <div className="space-y-4">
                                            {/* Status Summary */}
                                            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                                                <div className="bg-white/5 rounded-lg p-3">
                                                    <div className="text-white/50 text-xs">Storage</div>
                                                    <div className="text-white font-medium truncate">{backupStatus.bucket}</div>
                                                </div>
                                                <div className="bg-white/5 rounded-lg p-3">
                                                    <div className="text-white/50 text-xs">Total Backups</div>
                                                    <div className="text-white font-bold text-lg">{backupStatus.total_backups || 0}</div>
                                                </div>
                                                <div className="bg-white/5 rounded-lg p-3">
                                                    <div className="text-white/50 text-xs">Last Backup</div>
                                                    <div className="text-white font-medium">
                                                        {backupStatus.last_backup
                                                            ? `${backupStatus.last_backup.age_days}d ago`
                                                            : 'Never'}
                                                    </div>
                                                </div>
                                                <div className="bg-white/5 rounded-lg p-3">
                                                    <div className="text-white/50 text-xs">Schedule</div>
                                                    <div className="text-white font-medium text-sm">{backupStatus.next_scheduled || 'Daily'}</div>
                                                </div>
                                            </div>

                                            {/* Actions */}
                                            <div className="flex gap-3">
                                                <Button
                                                    onClick={async () => {
                                                        if (!confirm('Create a new database backup now?')) return;
                                                        setIsCreatingBackup(true);
                                                        try {
                                                            const result = await api.createBackup();
                                                            if (result.status === 'success') {
                                                                alert(`Backup created: ${result.backup_name}`);
                                                                // Refresh list
                                                                const list = await api.listBackups();
                                                                setBackups(list.backups || []);
                                                                const status = await api.getBackupStatus();
                                                                setBackupStatus(status);
                                                            }
                                                        } catch (err) {
                                                            alert('Backup failed: ' + (err as Error).message);
                                                        } finally {
                                                            setIsCreatingBackup(false);
                                                        }
                                                    }}
                                                    disabled={isCreatingBackup}
                                                    className="bg-blue-600 hover:bg-blue-700"
                                                >
                                                    {isCreatingBackup ? (
                                                        <>
                                                            <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
                                                            Creating...
                                                        </>
                                                    ) : (
                                                        <>
                                                            <Upload className="w-4 h-4 mr-2" />
                                                            Create Backup Now
                                                        </>
                                                    )}
                                                </Button>
                                                <Button
                                                    variant="ghost"
                                                    onClick={async () => {
                                                        setIsLoadingBackups(true);
                                                        try {
                                                            const list = await api.listBackups();
                                                            setBackups(list.backups || []);
                                                        } catch (err) {
                                                            console.error(err);
                                                        } finally {
                                                            setIsLoadingBackups(false);
                                                        }
                                                    }}
                                                >
                                                    <RefreshCw className={`w-4 h-4 mr-2 ${isLoadingBackups ? 'animate-spin' : ''}`} />
                                                    Refresh
                                                </Button>
                                            </div>

                                            {/* Backup List */}
                                            {backups.length > 0 && (
                                                <div className="mt-4">
                                                    <h4 className="text-white/70 text-sm font-medium mb-2">Recent Backups</h4>
                                                    <div className="space-y-2 max-h-48 overflow-y-auto">
                                                        {backups.slice(0, 10).map((backup) => (
                                                            <div key={backup.name} className="flex items-center justify-between bg-white/5 rounded-lg p-3">
                                                                <div>
                                                                    <div className="text-white font-mono text-sm">{backup.name}</div>
                                                                    <div className="text-white/50 text-xs">
                                                                        {new Date(backup.created_at).toLocaleString()} · {(backup.size_bytes / 1024 / 1024).toFixed(2)} MB
                                                                    </div>
                                                                </div>
                                                                <span className={`text-xs px-2 py-1 rounded ${backup.age_days === 0 ? 'bg-green-500/20 text-green-400' :
                                                                    backup.age_days < 7 ? 'bg-blue-500/20 text-blue-400' :
                                                                        'bg-white/10 text-white/60'
                                                                    }`}>
                                                                    {backup.age_days === 0 ? 'Today' : `${backup.age_days}d ago`}
                                                                </span>
                                                            </div>
                                                        ))}
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </AccordionSection>
                            </div>
                        )}

                        {/* System Health Tab */}
                        {currentTab === 'health' && (
                            <div className="space-y-6">
                                <OperationsPanel />
                                {/* The other half of the error screen's promise.
                                    Everything above is what the server knows
                                    about itself; this is what broke in
                                    somebody's browser, which nothing in the
                                    product surfaced before. */}
                                <ClientErrorPanel />
                            </div>
                        )}

                        {/* Audit Logs - part of Compliance */}
                        {currentTab === 'compliance' && (
                            <div className="mt-8">
                                <AuditLogViewer />
                            </div>
                        )}

                    </div>
                </div>
            </div>

            {/* Add/Edit Department Modal */}
            <Modal
                isOpen={showDepartmentModal}
                onClose={() => {
                    setShowDepartmentModal(false);
                    setEditingDepartment(null);
                }}
                title={editingDepartment ? 'Edit Department' : 'Add New Department'}
            >
                <form onSubmit={handleCreateDepartment} className="space-y-4">
                    <Input
                        label="Department Name"
                        value={newDepartment.name}
                        onChange={(e) => setNewDepartment((p) => ({ ...p, name: e.target.value }))}
                        placeholder="e.g., Public Works"
                        required
                    />
                    <Input
                        label="Description"
                        value={newDepartment.description}
                        onChange={(e) => setNewDepartment((p) => ({ ...p, description: e.target.value }))}
                        placeholder="Handles roads, parks, infrastructure..."
                    />
                    <Input
                        label="Routing Email"
                        type="email"
                        value={newDepartment.routing_email}
                        onChange={(e) => setNewDepartment((p) => ({ ...p, routing_email: e.target.value }))}
                        placeholder="publicworks@township.gov"
                    />
                    <div className="flex justify-end gap-3 pt-4">
                        <Button variant="ghost" onClick={() => setShowDepartmentModal(false)}>Cancel</Button>
                        <Button type="submit">{editingDepartment ? 'Save Changes' : 'Create Department'}</Button>
                    </div>
                </form>
            </Modal>

            {/* Edit User Modal.

                Username and password are absent on purpose. The username keys
                the audit log and the identity provider, so renaming it orphans
                history rather than correcting it; passwords have their own
                reset action. Everything else about a staff member changes over
                time -- people move department, change surname, get a new phone,
                leave -- and none of it was editable before this. */}
            <Modal isOpen={!!editingUser} onClose={() => setEditingUser(null)}
                title={`Edit ${editingUser?.full_name || editingUser?.username || 'user'}`}>
                <form onSubmit={handleUpdateUser} className="space-y-4">
                    <div className="rounded-lg bg-white/5 border border-white/10 px-3 py-2">
                        <p className="text-xs text-white/40">Username</p>
                        <p className="text-sm text-white/70 font-mono">@{editingUser?.username}</p>
                        <p className="text-[11px] text-white/35 mt-1">
                            Cannot be changed — it is how this person&apos;s history is recorded.
                        </p>
                    </div>
                    <Input
                        label="Full name"
                        value={editUser.full_name}
                        onChange={(e) => setEditUser((p) => ({ ...p, full_name: e.target.value }))}
                        placeholder="Jane Doe"
                    />
                    <Input
                        label="Email"
                        type="email"
                        value={editUser.email}
                        onChange={(e) => setEditUser((p) => ({ ...p, email: e.target.value }))}
                        placeholder="jane@township.gov"
                    />
                    <Input
                        label="Phone (for text alerts)"
                        value={editUser.phone}
                        onChange={(e) => setEditUser((p) => ({ ...p, phone: e.target.value }))}
                        placeholder="+1 555 010 0000"
                    />
                    <Select
                        label="Role"
                        value={editUser.role}
                        onChange={(e) => setEditUser((p) => ({ ...p, role: e.target.value as 'staff' | 'admin' | 'researcher' }))}
                        options={[
                            { value: 'staff', label: 'Staff — works reports' },
                            { value: 'admin', label: 'Admin — full access, including this page' },
                            { value: 'researcher', label: 'Researcher — read-only, anonymised data' },
                        ]}
                    />
                    <div>
                        <p className="text-sm text-white/60 mb-1.5">Departments</p>
                        <div className="space-y-1.5 max-h-40 overflow-y-auto">
                            {departments.map((dept) => (
                                <label key={dept.id} className="flex items-center gap-2 text-sm text-white/80 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={editUser.department_ids.includes(dept.id)}
                                        onChange={(e) => setEditUser((p) => e.target.checked
                                            ? { ...p, department_ids: [...p.department_ids, dept.id] }
                                            : { ...p, department_ids: p.department_ids.filter(id => id !== dept.id) })}
                                        className="rounded"
                                    />
                                    {dept.name}
                                </label>
                            ))}
                        </div>
                    </div>
                    {/* Deactivating rather than deleting is what a records system
                        wants: the person keeps their history and simply cannot
                        sign in. Deleting is still available on the row. */}
                    <label className="flex items-start gap-2.5 text-sm text-white/80 cursor-pointer rounded-lg bg-white/[0.03] border border-white/10 px-3 py-2.5">
                        <input
                            type="checkbox"
                            checked={editUser.is_active}
                            onChange={(e) => setEditUser((p) => ({ ...p, is_active: e.target.checked }))}
                            className="rounded mt-0.5"
                            disabled={editingUser?.id === user?.id}
                        />
                        <span>
                            Active
                            <span className="block text-[11px] text-white/40">
                                {editingUser?.id === user?.id
                                    ? 'You cannot deactivate your own account.'
                                    : 'Unticking keeps all their history but stops them signing in.'}
                            </span>
                        </span>
                    </label>
                    <div className="flex justify-end gap-3 pt-4">
                        <Button variant="ghost" onClick={() => setEditingUser(null)}>Cancel</Button>
                        <Button type="submit">Save Changes</Button>
                    </div>
                </form>
            </Modal>

            {/* Add User Modal */}
            <Modal isOpen={showUserModal} onClose={() => setShowUserModal(false)} title="Add New User">
                <form onSubmit={handleCreateUser} className="space-y-4">
                    <Input
                        label="Username"
                        value={newUser.username}
                        onChange={(e) => setNewUser((p) => ({ ...p, username: e.target.value }))}
                        required
                    />
                    <Input
                        label="Full Name"
                        value={newUser.full_name}
                        onChange={(e) => setNewUser((p) => ({ ...p, full_name: e.target.value }))}
                    />
                    <Input
                        label="Email"
                        type="email"
                        value={newUser.email}
                        onChange={(e) => setNewUser((p) => ({ ...p, email: e.target.value }))}
                        required
                    />

                    {/* SSO Info */}
                    <div className="p-3 rounded-lg bg-blue-500/10 border border-blue-500/20 text-sm text-blue-200">
                        <p className="font-medium">🔐 SSO Authentication</p>
                        <p className="text-blue-200/70 mt-1">
                            Users log in via Auth0 SSO using their email address. No password is required.
                        </p>
                    </div>

                    <Select
                        label="Role"
                        options={[
                            { value: 'staff', label: 'Staff' },
                            { value: 'admin', label: 'Admin' },
                        ]}
                        value={newUser.role}
                        onChange={(e) => setNewUser((p) => ({ ...p, role: e.target.value as 'staff' | 'admin' }))}
                    />

                    {/* Department Assignment */}
                    {newUser.role === 'staff' && departments.length > 0 && (
                        <div className="space-y-2">
                            <label className="block text-sm font-medium text-white/70">Assign to Departments</label>
                            <div className="grid grid-cols-2 gap-2 max-h-32 overflow-y-auto p-2 rounded-lg bg-white/5 border border-white/10">
                                {departments.map((dept) => (
                                    <label key={dept.id} className="flex items-center gap-2 cursor-pointer hover:bg-white/5 p-1 rounded">
                                        <input
                                            type="checkbox"
                                            checked={newUser.department_ids.includes(dept.id)}
                                            onChange={(e) => {
                                                if (e.target.checked) {
                                                    setNewUser((p) => ({ ...p, department_ids: [...p.department_ids, dept.id] }));
                                                } else {
                                                    setNewUser((p) => ({ ...p, department_ids: p.department_ids.filter(id => id !== dept.id) }));
                                                }
                                            }}
                                            className="w-4 h-4 rounded border-white/20 bg-white/10 text-primary-500 focus:ring-primary-500"
                                        />
                                        <span className="text-sm text-white/80">{dept.name}</span>
                                    </label>
                                ))}
                            </div>
                        </div>
                    )}

                    <div className="flex justify-end gap-3 pt-4">
                        <Button variant="ghost" onClick={() => setShowUserModal(false)}>Cancel</Button>
                        <Button type="submit">Create User</Button>
                    </div>
                </form>
            </Modal>

            {/* Records eligible for archival */}
            <Modal
                isOpen={showEligibleModal}
                onClose={() => setShowEligibleModal(false)}
                title="Records Eligible for Archival"
            >
                <div className="space-y-4">
                    {isLoadingEligible ? (
                        <div className="text-center py-8 text-white/60">Loading...</div>
                    ) : eligibleError ? (
                        <div role="alert" className="rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-200">
                            {eligibleError}
                        </div>
                    ) : eligiblePreview?.blocked === 'legal_hold' ? (
                        <p className="text-sm text-amber-200">
                            The instance-wide legal hold is on, so nothing can be archived until it is lifted.
                        </p>
                    ) : !eligiblePreview?.summary || eligiblePreview.summary.total === 0 ? (
                        <div className="text-center py-8 text-white/60">
                            Nothing has passed the retention period yet.
                        </div>
                    ) : (
                        <>
                            <p className="text-white/60 text-sm">
                                These closed records have passed the{' '}
                                {eligiblePreview.summary.retention_days.toLocaleString()}-day retention period.
                                A retention run would {eligiblePreview.mode === 'purge' ? 'clear every field on' : 'redact'} them.
                            </p>
                            {/* Fifty of four thousand, presented as the whole
                                answer, is the undercount this list exists to
                                prevent. */}
                            {eligiblePreview.summary.truncated && (
                                <p className="text-xs text-amber-200/90">
                                    Showing the {eligiblePreview.summary.showing} oldest of{' '}
                                    {eligiblePreview.summary.total.toLocaleString()}. A run works through all of them.
                                </p>
                            )}
                            <div className="space-y-3 max-h-[60vh] overflow-y-auto">
                                {eligiblePreview.records.map((r) => (
                                    <button
                                        key={r.service_request_id}
                                        onClick={() => {
                                            setShowEligibleModal(false);
                                            navigate(`/staff#resolved/request/${r.service_request_id}`);
                                        }}
                                        className="w-full text-left bg-blue-500/10 border border-blue-500/30 rounded-lg p-4 hover:bg-blue-500/20 transition-colors"
                                    >
                                        <div className="flex justify-between items-start mb-2">
                                            <span className="font-mono text-blue-300 font-semibold">
                                                {r.service_request_id}
                                            </span>
                                            {r.days_past_retention != null && (
                                                <span className="text-xs px-2 py-1 rounded bg-amber-500/20 text-amber-300">
                                                    {r.days_past_retention.toLocaleString()} days past due
                                                </span>
                                            )}
                                        </div>
                                        <div className="text-white font-medium">{r.service_name}</div>
                                        {r.address && (
                                            <div className="text-white/50 text-xs mt-2 flex items-center gap-1">
                                                <MapPin className="w-3 h-3" />
                                                {r.address}
                                            </div>
                                        )}
                                        <div className="text-white/40 text-xs mt-2">
                                            Closed:{' '}
                                            {r.closed_datetime
                                                ? new Date(r.closed_datetime).toLocaleDateString(undefined, {
                                                    year: 'numeric', month: 'short', day: 'numeric',
                                                    timeZone: eligiblePreview.timezone || undefined,
                                                })
                                                : '—'}
                                            {r.age_days != null && <> · {(r.age_days / 365).toFixed(1)} yrs old</>}
                                        </div>
                                    </button>
                                ))}
                            </div>
                        </>
                    )}
                </div>
            </Modal>

            {/* Legal Hold Requests Modal */}
            <Modal
                isOpen={showLegalHoldModal}
                onClose={() => setShowLegalHoldModal(false)}
                title="Requests Under Legal Hold"
            >
                <div className="space-y-4">
                    {isLoadingLegalHold ? (
                        <div className="text-center py-8 text-white/60">Loading...</div>
                    ) : legalHoldRequests.length === 0 ? (
                        <div className="text-center py-8 text-white/60">
                            No requests are currently under legal hold.
                        </div>
                    ) : (
                        <>
                            <p className="text-white/60 text-sm">
                                These requests have been flagged and are protected from automatic archival or deletion.
                            </p>
                            <div className="space-y-3 max-h-[60vh] overflow-y-auto">
                                {legalHoldRequests.map((req) => (
                                    <button
                                        key={req.id}
                                        onClick={() => {
                                            setShowLegalHoldModal(false);
                                            // Map status to URL path segment
                                            const statusPath = req.status === 'open' ? 'active' :
                                                req.status === 'in_progress' ? 'in_progress' :
                                                    req.status === 'closed' ? 'resolved' : 'active';
                                            navigate(`/staff#${statusPath}/request/${req.service_request_id}`);
                                        }}
                                        className="w-full text-left bg-amber-500/10 border border-amber-500/30 rounded-lg p-4 hover:bg-amber-500/20 transition-colors"
                                    >
                                        <div className="flex justify-between items-start mb-2">
                                            <span className="font-mono text-amber-400 font-semibold">
                                                {req.service_request_id}
                                            </span>
                                            <span className={`text-xs px-2 py-1 rounded capitalize ${req.status === 'open' ? 'bg-emerald-500/20 text-emerald-400' :
                                                req.status === 'in_progress' ? 'bg-blue-500/20 text-blue-400' :
                                                    'bg-gray-500/20 text-gray-400'
                                                }`}>
                                                {req.status.replace('_', ' ')}
                                            </span>
                                        </div>
                                        <div className="text-white font-medium">{req.service_name}</div>
                                        {req.description && (
                                            <div className="text-white/60 text-sm mt-1 line-clamp-2">{req.description}</div>
                                        )}
                                        {req.address && (
                                            <div className="text-white/50 text-xs mt-2 flex items-center gap-1">
                                                <MapPin className="w-3 h-3" />
                                                {req.address}
                                            </div>
                                        )}
                                        <div className="text-white/40 text-xs mt-2">
                                            Submitted: {new Date(req.requested_datetime).toLocaleDateString()}
                                        </div>
                                    </button>
                                ))}
                            </div>
                        </>
                    )}
                    <div className="flex justify-end pt-4 border-t border-white/10">
                        <Button variant="ghost" onClick={() => setShowLegalHoldModal(false)}>Close</Button>
                    </div>
                </div>
            </Modal>

            {/* Add Service Modal */}
            <Modal isOpen={showServiceModal} size="lg" panelClassName="bg-slate-900/95 border border-white/20 shadow-[0_20px_80px_rgba(0,0,0,0.6)] backdrop-blur-3xl rounded-3xl" headerClassName="bg-slate-800/90 border-b border-white/15 p-6" onClose={() => setShowServiceModal(false)} title="Add Service Category">
                <form onSubmit={handleCreateService} className="space-y-4">
                    <Input
                        label="Service Name"
                        value={newService.service_name}
                        onChange={(e) => setNewService((p) => ({ ...p, service_name: e.target.value }))}
                        required
                    />
                    <Input
                        label="Service Code"
                        placeholder="POTHOLE, STREETLIGHT, etc."
                        value={newService.service_code}
                        onChange={(e) => setNewService((p) => ({ ...p, service_code: e.target.value.toUpperCase() }))}
                        required
                    />
                    <Input
                        label="Description"
                        value={newService.description}
                        onChange={(e) => setNewService((p) => ({ ...p, description: e.target.value }))}
                    />
                    <div className="flex justify-end gap-3 pt-4">
                        <Button variant="ghost" onClick={() => setShowServiceModal(false)}>Cancel</Button>
                        <Button type="submit">Create Category</Button>
                    </div>
                </form>
            </Modal>

            {/* Service Routing Edit Modal */}
            <Modal
                isOpen={showServiceEditModal}
                size="xl"
                panelClassName="bg-slate-900/95 border border-white/20 shadow-[0_20px_90px_rgba(0,0,0,0.7)] backdrop-blur-3xl rounded-3xl overflow-hidden"
                headerClassName="bg-gradient-to-r from-slate-800 via-slate-850 to-slate-800 border-b border-white/15 p-6"
                onClose={() => {
                    setShowServiceEditModal(false);
                    setEditingService(null);
                }}
                title={`Configure Routing: ${editingService?.service_name || ''}`}
            >
                <form onSubmit={handleSaveServiceRouting} className="space-y-5 max-h-[70vh] overflow-y-auto pr-2">
                    {/* Routing Mode */}
                    <div className="space-y-2.5">
                        <label className="block text-xs font-bold uppercase tracking-wider text-white/60">Routing Mode</label>
                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                            {[
                                { value: 'township', label: 'Municipality Handles', desc: 'Processed by town staff', icon: Building2, color: 'text-emerald-400' },
                                { value: 'third_party', label: '3rd Party Only', desc: 'Redirect to outside portal', icon: ExternalLink, color: 'text-purple-400' },
                                { value: 'road_based', label: 'Road-Based Routing', desc: 'Route by GIS jurisdiction', icon: GitFork, color: 'text-amber-400' },
                            ].map(mode => {
                                const IconComponent = mode.icon;
                                const isSelected = serviceRouting.routing_mode === mode.value;
                                return (
                                    <button
                                        type="button"
                                        key={mode.value}
                                        onClick={() => setServiceRouting(p => ({ ...p, routing_mode: mode.value as any }))}
                                        className={`p-4 rounded-2xl border text-left transition-all duration-200 ${isSelected
                                            ? 'bg-gradient-to-br from-primary-500/20 via-indigo-500/15 to-purple-500/10 border-primary-400 text-white shadow-[0_0_25px_rgba(99,102,241,0.2)] ring-1 ring-primary-400/40'
                                            : 'bg-white/[0.03] border-white/10 text-white/70 hover:border-white/25 hover:bg-white/[0.06]'
                                            }`}
                                    >
                                        <div className="flex items-center gap-2.5 mb-1.5">
                                            <IconComponent className={`w-4 h-4 ${isSelected ? 'text-primary-300' : mode.color}`} />
                                            <div className="font-semibold text-sm text-white tracking-tight">{mode.label}</div>
                                        </div>
                                        <div className="text-xs text-white/50 leading-relaxed">{mode.desc}</div>
                                    </button>
                                );
                            })}
                        </div>
                    </div>

                    {/* Icon Picker */}
                    <div className="space-y-2.5 p-4 rounded-3xl bg-slate-800/50 border border-white/20 shadow-xl backdrop-blur-xl">
                        <label className="block text-xs font-bold uppercase tracking-wider text-white/70">Category Icon</label>
                        <div className="grid grid-cols-10 gap-2 p-2.5 rounded-2xl bg-white/[0.04] border border-white/10 max-h-28 overflow-y-auto">
                            {ICON_LIBRARY.map(({ name, icon: IconComponent }) => (
                                <button
                                    type="button"
                                    key={name}
                                    onClick={() => setServiceRouting(p => ({ ...p, icon: name }))}
                                    className={`p-2.5 rounded-2xl transition-all duration-200 flex items-center justify-center ${serviceRouting.icon === name
                                        ? 'bg-gradient-to-r from-primary-500 to-indigo-600 text-white shadow-lg shadow-primary-500/40 scale-110 ring-2 ring-white/30'
                                        : 'bg-white/5 text-white/60 hover:bg-white/15 hover:text-white'
                                        }`}
                                    title={name}
                                >
                                    <IconComponent className="w-4 h-4" />
                                </button>
                            ))}
                        </div>
                    </div>

                    {/* Optional SLA target */}
                    <div className="space-y-2 p-4 rounded-lg bg-sky-500/10 border border-sky-500/20">
                        <div className="flex items-center gap-2">
                            <Clock className="w-4 h-4 text-sky-300" />
                            <label htmlFor="sla-hours" className="text-sm font-medium text-white">
                                Service Level Target <span className="text-white/40 font-normal">(optional)</span>
                            </label>
                        </div>
                        <p className="text-xs text-white/50">
                            Target time from submission to closure for this category. Leave blank for no
                            target — categories without one are simply excluded from SLA reporting.
                        </p>
                        <div className="flex items-center gap-2">
                            <input
                                id="sla-hours"
                                type="number"
                                min={1}
                                max={8760}
                                inputMode="numeric"
                                placeholder="e.g. 72"
                                value={serviceRouting.sla_hours}
                                onChange={(e) => setServiceRouting(p => ({ ...p, sla_hours: e.target.value }))}
                                className="w-32 h-10 rounded-lg bg-white/10 border border-white/20 text-white px-3"
                            />
                            <span className="text-sm text-white/60">hours</span>
                            {serviceRouting.sla_hours && Number(serviceRouting.sla_hours) > 0 && (
                                <span className="text-xs text-sky-300 ml-1">
                                    = {formatSlaTarget(Number(serviceRouting.sla_hours))}
                                </span>
                            )}
                            {serviceRouting.sla_hours && (
                                <button
                                    type="button"
                                    onClick={() => setServiceRouting(p => ({ ...p, sla_hours: '' }))}
                                    className="text-xs text-white/40 hover:text-white/70 underline underline-offset-2 ml-auto"
                                >
                                    Clear
                                </button>
                            )}
                        </div>
                    </div>

                    {/* Township Mode Config */}
                    {serviceRouting.routing_mode === 'township' && (
                        <div className="space-y-4 p-5 rounded-3xl bg-gradient-to-br from-emerald-500/15 via-teal-900/10 to-slate-950/50 border border-emerald-500/30 shadow-xl">
                            <h4 className="font-bold text-emerald-300 flex items-center gap-2 text-sm tracking-wide">
                                <Check className="w-4 h-4 text-emerald-400" /> Municipality Handles Requests in House
                            </h4>

                            <div className="space-y-2">
                                <label className="block text-xs font-bold uppercase tracking-wider text-white/70">Assign to Department</label>
                                <select
                                    value={serviceRouting.assigned_department_id || ''}
                                    onChange={(e) => setServiceRouting(p => ({
                                        ...p,
                                        assigned_department_id: e.target.value ? parseInt(e.target.value) : null,
                                        routing_config: { ...p.routing_config, staff_ids: [] }
                                    }))}
                                    className="w-full h-11 rounded-2xl bg-white/[0.08] border border-white/20 text-white px-4 text-sm focus:outline-none focus:border-emerald-400"
                                    aria-label="Assign to department"
                                >
                                    <option value="">Select department...</option>
                                    {departments.map(d => (
                                        <option key={d.id} value={d.id}>{d.name}</option>
                                    ))}
                                </select>
                            </div>

                            {serviceRouting.assigned_department_id && (
                                <div className="space-y-2">
                                    <label className="block text-sm font-medium text-white/70">Route To</label>
                                    <div className="grid grid-cols-2 gap-2">
                                        <button
                                            type="button"
                                            onClick={() => setServiceRouting(p => ({
                                                ...p,
                                                routing_config: { ...p.routing_config, route_to: 'all_staff', staff_ids: [] }
                                            }))}
                                            className={`p-3 rounded-lg border text-center ${serviceRouting.routing_config.route_to === 'all_staff'
                                                ? 'bg-primary-500/20 border-primary-500 text-white'
                                                : 'bg-white/5 border-white/10 text-white/70'
                                                }`}
                                        >
                                            <Users className="w-5 h-5 mx-auto mb-1" />
                                            <div className="text-sm font-medium">All Staff in Dept</div>
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => setServiceRouting(p => ({
                                                ...p,
                                                routing_config: { ...p.routing_config, route_to: 'specific_staff' }
                                            }))}
                                            className={`p-3 rounded-lg border text-center ${serviceRouting.routing_config.route_to === 'specific_staff'
                                                ? 'bg-primary-500/20 border-primary-500 text-white'
                                                : 'bg-white/5 border-white/10 text-white/70'
                                                }`}
                                        >
                                            <UserCheck className="w-5 h-5 mx-auto mb-1" />
                                            <div className="text-sm font-medium">Specific Staff</div>
                                        </button>
                                    </div>
                                    {serviceRouting.routing_config.route_to === 'specific_staff' && (
                                        <div className="space-y-2 mt-3">
                                            <label className="block text-xs text-white/50">Select staff members:</label>
                                            <div className="max-h-32 overflow-y-auto p-2 rounded-lg bg-white/5 border border-white/10">
                                                {users
                                                    .filter(u => u.role === 'staff' || u.role === 'admin')
                                                    .map(u => (
                                                        <label key={u.id} className="flex items-center gap-2 p-1.5 hover:bg-white/5 rounded cursor-pointer">
                                                            <input
                                                                type="checkbox"
                                                                checked={(serviceRouting.routing_config.staff_ids || []).includes(u.id)}
                                                                onChange={(e) => {
                                                                    const currentIds = serviceRouting.routing_config.staff_ids || [];
                                                                    const newIds = e.target.checked
                                                                        ? [...currentIds, u.id]
                                                                        : currentIds.filter((id: number) => id !== u.id);
                                                                    setServiceRouting(p => ({
                                                                        ...p,
                                                                        routing_config: { ...p.routing_config, staff_ids: newIds }
                                                                    }));
                                                                }}
                                                                className="w-4 h-4 rounded border-white/20 bg-white/10 text-primary-500"
                                                            />
                                                            <span className="text-sm text-white/80">{u.full_name || u.username}</span>
                                                            <span className="text-xs text-white/40">@{u.username}</span>
                                                        </label>
                                                    ))}
                                            </div>
                                            {(serviceRouting.routing_config.staff_ids || []).length === 0 && (
                                                <p className="text-xs text-amber-400">No staff selected - will route to all staff</p>
                                            )}
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    )}

                    {/* Third Party Config */}
                    {serviceRouting.routing_mode === 'third_party' && (
                        <div className="space-y-4 p-5 rounded-3xl bg-gradient-to-br from-purple-500/15 via-indigo-900/10 to-slate-950/50 border border-purple-500/30 shadow-xl">
                            <h4 className="font-bold text-purple-300 flex items-center gap-2 text-sm tracking-wide">
                                <AlertTriangle className="w-4 h-4 text-purple-400" /> Third Party Only (Blocks Portal Submission)
                            </h4>
                            <p className="text-xs text-white/60">Residents cannot submit requests directly in portal; they are provided redirection links & contacts.</p>

                            <div className="space-y-2">
                                <label className="block text-sm font-medium text-white/70">Message to Display</label>
                                <textarea
                                    rows={3}
                                    placeholder="This service is handled by the County..."
                                    value={serviceRouting.routing_config.message}
                                    onChange={(e) => setServiceRouting(p => ({
                                        ...p,
                                        routing_config: { ...p.routing_config, message: e.target.value }
                                    }))}
                                    className="w-full rounded-lg bg-white/10 border border-white/20 text-white px-3 py-2"
                                    aria-label="Message to display to users"
                                />
                            </div>

                            <div className="space-y-3">
                                <label className="block text-sm font-medium text-white/70">Contact Information</label>
                                {serviceRouting.routing_config.contacts.map((contact, idx) => (
                                    <div key={idx} className="p-3 rounded-lg bg-white/5 border border-white/10 space-y-2">
                                        <div className="flex justify-between items-center">
                                            <span className="text-xs text-white/40">Contact {idx + 1}</span>
                                            <button type="button" aria-label="Remove contact" onClick={() => {
                                                const c = serviceRouting.routing_config.contacts.filter((_, i) => i !== idx);
                                                setServiceRouting(p => ({ ...p, routing_config: { ...p.routing_config, contacts: c } }));
                                            }} className="p-1 text-red-400 hover:text-red-300"><X className="w-3 h-3" /></button>
                                        </div>
                                        <input aria-label="Contact name" placeholder="Name (e.g., Mercer County Roads)" value={contact.name} onChange={(e) => {
                                            const c = [...serviceRouting.routing_config.contacts];
                                            c[idx] = { ...c[idx], name: e.target.value };
                                            setServiceRouting(p => ({ ...p, routing_config: { ...p.routing_config, contacts: c } }));
                                        }} className="w-full h-9 rounded-lg bg-white/10 border border-white/20 text-white px-3 text-sm" />
                                        <div className="grid grid-cols-2 gap-2">
                                            <input aria-label="Contact phone" placeholder="Phone" value={contact.phone} onChange={(e) => {
                                                const c = [...serviceRouting.routing_config.contacts];
                                                c[idx] = { ...c[idx], phone: e.target.value };
                                                setServiceRouting(p => ({ ...p, routing_config: { ...p.routing_config, contacts: c } }));
                                            }} className="w-full h-9 rounded-lg bg-white/10 border border-white/20 text-white px-3 text-sm" />
                                            <input aria-label="Contact website URL" placeholder="Website URL" value={contact.url} onChange={(e) => {
                                                const c = [...serviceRouting.routing_config.contacts];
                                                c[idx] = { ...c[idx], url: e.target.value };
                                                setServiceRouting(p => ({ ...p, routing_config: { ...p.routing_config, contacts: c } }));
                                            }} className="w-full h-9 rounded-lg bg-white/10 border border-white/20 text-white px-3 text-sm" />
                                        </div>
                                    </div>
                                ))}
                                <button type="button" onClick={() => setServiceRouting(p => ({
                                    ...p,
                                    routing_config: { ...p.routing_config, contacts: [...p.routing_config.contacts, { name: '', phone: '', url: '' }] }
                                }))} className="text-sm text-primary-400 hover:text-primary-300 flex items-center gap-1">
                                    <Plus className="w-4 h-4" /> Add Contact
                                </button>
                            </div>
                        </div>
                    )}

                    {/* Road-Based Config */}
                    {serviceRouting.routing_mode === 'road_based' && (
                        <div className="space-y-4 p-4 rounded-lg bg-amber-500/10 border border-amber-500/20">
                            <h4 className="font-medium text-amber-300 flex items-center gap-2">
                                <Route className="w-4 h-4" /> Road-Based Routing
                            </h4>

                            {/* Default Handler.

                                This used to offer a generic "Third party handles by
                                default", which stored the literal string "third_party".
                                The backend resolves this setting by matching it against
                                the configured agencies' names, so it matched nothing and
                                every road quietly stayed with the town -- the exact
                                opposite of what was selected. Naming the agency makes the
                                setting mean something, and with several agencies it is
                                the only way to say which one gets an unlisted road. */}
                            <div className="space-y-2">
                                <label className="block text-xs font-bold uppercase tracking-wider text-white/70">
                                    Who handles a road that is not listed below?
                                </label>
                                <select
                                    value={serviceRouting.routing_config.default_handler}
                                    onChange={(e) => setServiceRouting(p => ({
                                        ...p,
                                        routing_config: { ...p.routing_config, default_handler: e.target.value }
                                    }))}
                                    className="w-full h-11 rounded-2xl bg-white/[0.08] border border-white/20 text-white px-4 text-sm focus:outline-none focus:border-amber-400"
                                    aria-label="Who handles a road that is not listed"
                                >
                                    <option value="township">The municipality (roads below are the exceptions)</option>
                                    {(serviceRouting.routing_config.third_party_contacts || [])
                                        .map(a => (a?.name || '').trim())
                                        .filter(Boolean)
                                        .map(name => (
                                            <option key={name} value={name}>
                                                {name} (the municipality keeps only its own roads)
                                            </option>
                                        ))}
                                    {/* Keep a saved value selectable even if that agency was
                                        since renamed, so opening the modal cannot silently
                                        reset the setting to "municipality" on the next save. */}
                                    {serviceRouting.routing_config.default_handler
                                        && serviceRouting.routing_config.default_handler !== 'township'
                                        && !(serviceRouting.routing_config.third_party_contacts || [])
                                            .some(a => (a?.name || '').trim() === serviceRouting.routing_config.default_handler) && (
                                            <option value={serviceRouting.routing_config.default_handler}>
                                                {serviceRouting.routing_config.default_handler === 'third_party'
                                                    ? 'A third party (not yet named — pick an agency above)'
                                                    : `${serviceRouting.routing_config.default_handler} (no longer configured)`}
                                            </option>
                                        )}
                                </select>
                                <p className="text-xs text-white/45">
                                    Pick an agency to invert the rule: they maintain everything
                                    except the roads you list as the municipality's.
                                </p>
                            </div>

                            {/* Municipality Department */}
                            <div className="space-y-2">
                                <label className="block text-sm font-medium text-white/70">Municipality Department</label>
                                <select
                                    value={serviceRouting.assigned_department_id || ''}
                                    onChange={(e) => setServiceRouting(p => ({
                                        ...p,
                                        assigned_department_id: e.target.value ? parseInt(e.target.value) : null
                                    }))}
                                    className="w-full h-10 rounded-lg bg-white/10 border border-white/20 text-white px-3"
                                    aria-label="Municipality department"
                                >
                                    <option value="">Select department...</option>
                                    {departments.map(d => (
                                        <option key={d.id} value={d.id}>{d.name}</option>
                                    ))}
                                </select>
                            </div>

                            {/* Route To (specific staff) — mirrors Municipality mode so a
                                road-based request the town handles can go to a named person. */}
                            {serviceRouting.assigned_department_id && (
                                <div className="space-y-2">
                                    <label className="block text-sm font-medium text-white/70">Route To</label>
                                    <div className="grid grid-cols-2 gap-2">
                                        <button
                                            type="button"
                                            onClick={() => setServiceRouting(p => ({
                                                ...p,
                                                routing_config: { ...p.routing_config, route_to: 'all_staff', staff_ids: [] }
                                            }))}
                                            className={`p-3 rounded-lg border text-center ${serviceRouting.routing_config.route_to === 'all_staff'
                                                ? 'bg-primary-500/20 border-primary-500 text-white'
                                                : 'bg-white/5 border-white/10 text-white/70'
                                                }`}
                                        >
                                            <Users className="w-5 h-5 mx-auto mb-1" />
                                            <div className="text-sm font-medium">All Staff in Dept</div>
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => setServiceRouting(p => ({
                                                ...p,
                                                routing_config: { ...p.routing_config, route_to: 'specific_staff' }
                                            }))}
                                            className={`p-3 rounded-lg border text-center ${serviceRouting.routing_config.route_to === 'specific_staff'
                                                ? 'bg-primary-500/20 border-primary-500 text-white'
                                                : 'bg-white/5 border-white/10 text-white/70'
                                                }`}
                                        >
                                            <UserCheck className="w-5 h-5 mx-auto mb-1" />
                                            <div className="text-sm font-medium">Specific Staff</div>
                                        </button>
                                    </div>
                                    {serviceRouting.routing_config.route_to === 'specific_staff' && (
                                        <div className="space-y-2 mt-3">
                                            <label className="block text-xs text-white/50">Select staff members:</label>
                                            <div className="max-h-32 overflow-y-auto p-2 rounded-lg bg-white/5 border border-white/10">
                                                {users
                                                    .filter(u => u.role === 'staff' || u.role === 'admin')
                                                    .map(u => (
                                                        <label key={u.id} className="flex items-center gap-2 p-1.5 hover:bg-white/5 rounded cursor-pointer">
                                                            <input
                                                                type="checkbox"
                                                                checked={(serviceRouting.routing_config.staff_ids || []).includes(u.id)}
                                                                onChange={(e) => {
                                                                    const currentIds = serviceRouting.routing_config.staff_ids || [];
                                                                    const newIds = e.target.checked
                                                                        ? [...currentIds, u.id]
                                                                        : currentIds.filter((id: number) => id !== u.id);
                                                                    setServiceRouting(p => ({
                                                                        ...p,
                                                                        routing_config: { ...p.routing_config, staff_ids: newIds }
                                                                    }));
                                                                }}
                                                                className="w-4 h-4 rounded border-white/20 bg-white/10 text-primary-500"
                                                            />
                                                            <span className="text-sm text-white/80">{u.full_name || u.username}</span>
                                                            <span className="text-xs text-white/40">@{u.username}</span>
                                                        </label>
                                                    ))}
                                            </div>
                                            {(serviceRouting.routing_config.staff_ids || []).length === 0 && (
                                                <p className="text-xs text-amber-400">No staff selected - will route to all staff</p>
                                            )}
                                        </div>
                                    )}
                                </div>
                            )}





                            {routingIssues.length > 0 && (
                                <ul className="space-y-2" aria-label="Routing configuration issues">
                                    {routingIssues.map((issue, index) => {
                                        const tone = issue.severity === 'error'
                                            ? 'bg-red-500/10 border-red-500/30 text-red-200'
                                            : issue.severity === 'warning'
                                                ? 'bg-amber-500/10 border-amber-500/30 text-amber-200'
                                                : 'bg-white/[0.04] border-white/10 text-white/60';
                                        const Icon = issue.severity === 'info' ? Info : AlertTriangle;
                                        return (
                                            <li
                                                key={`${issue.kind}-${index}`}
                                                className={`flex items-start gap-2.5 rounded-xl border px-3.5 py-2.5 text-sm ${tone}`}
                                                role={issue.severity === 'error' ? 'alert' : 'status'}
                                            >
                                                <Icon className="w-4 h-4 mt-0.5 shrink-0" aria-hidden="true" />
                                                <span className="leading-relaxed">{issue.message}</span>
                                            </li>
                                        );
                                    })}
                                </ul>
                            )}

                            {/* 3rd Party Agencies & Roads */}
                            <div className="space-y-4 pt-4 border-t border-white/10">
                                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                                    <div>
                                        <label className="block text-xs font-bold uppercase tracking-wider text-white">3rd Party Agencies & Contacts</label>
                                        <p className="text-xs text-white/50">Add 3rd party agencies (e.g. PennDOT, Mercer County DPW) with their contact info & road lists.</p>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={() => setServiceRouting(p => ({
                                            ...p,
                                            routing_config: {
                                                ...p.routing_config,
                                                third_party_contacts: [
                                                    ...(p.routing_config.third_party_contacts || []),
                                                    { name: '', phone: '', email: '', url: '', message: '', road_input: '', roads: [] }
                                                ]
                                            }
                                        }))}
                                        className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-gradient-to-r from-amber-500/20 to-orange-500/20 border border-amber-500/30 text-xs font-bold text-amber-300 hover:bg-amber-500/30 transition-all shadow-md shrink-0"
                                    >
                                        <Plus className="w-4 h-4" /> Add 3rd Party Agency
                                    </button>
                                </div>

                                {(serviceRouting.routing_config.third_party_contacts || []).map((agency: any, idx: number) => {
                                    return (
                                        <div key={idx} className="p-5 rounded-3xl bg-white/[0.04] border border-white/15 space-y-4 shadow-xl">
                                            <div className="flex items-center justify-between border-b border-white/10 pb-2">
                                                <span className="text-xs font-bold uppercase tracking-wider text-amber-300 flex items-center gap-2">
                                                    <Building2 className="w-4 h-4 text-amber-400" /> Agency #{idx + 1}: {agency.name || 'Unnamed Agency'}
                                                </span>
                                                <button
                                                    type="button"
                                                    aria-label="Remove agency"
                                                    onClick={() => {
                                                        const updated = (serviceRouting.routing_config.third_party_contacts || []).filter((_: any, i: number) => i !== idx);
                                                        setServiceRouting(p => ({ ...p, routing_config: { ...p.routing_config, third_party_contacts: updated } }));
                                                    }}
                                                    className="p-1.5 text-red-400 hover:text-red-300 hover:bg-red-500/10 rounded-lg transition-colors flex items-center gap-1 text-xs"
                                                >
                                                    <Trash2 className="w-4 h-4" /> Remove Agency
                                                </button>
                                            </div>

                                            {/* Agency Name & Contact Info */}
                                            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                                                <div>
                                                    <label className="block text-[11px] font-semibold text-white/70 mb-1">Agency Name</label>
                                                    <input
                                                        placeholder="e.g. PennDOT / State Highways"
                                                        value={agency.name || ''}
                                                        onChange={(e) => {
                                                            const updated = [...(serviceRouting.routing_config.third_party_contacts || [])];
                                                            updated[idx] = { ...updated[idx], name: e.target.value };
                                                            setServiceRouting(p => ({ ...p, routing_config: { ...p.routing_config, third_party_contacts: updated } }));
                                                        }}
                                                        className="w-full h-9 rounded-xl bg-white/10 border border-white/20 text-white px-3 text-xs focus:outline-none focus:border-amber-400"
                                                    />
                                                </div>
                                                <div>
                                                    <label className="block text-[11px] font-semibold text-white/70 mb-1">Phone Number</label>
                                                    <input
                                                        placeholder="e.g. 1-800-FIX-ROAD"
                                                        value={agency.phone || ''}
                                                        onChange={(e) => {
                                                            const updated = [...(serviceRouting.routing_config.third_party_contacts || [])];
                                                            updated[idx] = { ...updated[idx], phone: e.target.value };
                                                            setServiceRouting(p => ({ ...p, routing_config: { ...p.routing_config, third_party_contacts: updated } }));
                                                        }}
                                                        className="w-full h-9 rounded-xl bg-white/10 border border-white/20 text-white px-3 text-xs focus:outline-none focus:border-amber-400"
                                                    />
                                                </div>
                                                <div>
                                                    <label className="block text-[11px] font-semibold text-white/70 mb-1">Email Address</label>
                                                    <input
                                                        placeholder="e.g. customercare@penndot.gov"
                                                        value={agency.email || ''}
                                                        onChange={(e) => {
                                                            const updated = [...(serviceRouting.routing_config.third_party_contacts || [])];
                                                            updated[idx] = { ...updated[idx], email: e.target.value };
                                                            setServiceRouting(p => ({ ...p, routing_config: { ...p.routing_config, third_party_contacts: updated } }));
                                                        }}
                                                        className="w-full h-9 rounded-xl bg-white/10 border border-white/20 text-white px-3 text-xs focus:outline-none focus:border-amber-400"
                                                    />
                                                </div>
                                            </div>

                                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                                <div>
                                                    <label className="block text-[11px] font-semibold text-white/70 mb-1">Website / Portal URL</label>
                                                    <input
                                                        placeholder="e.g. https://customercare.penndot.gov"
                                                        value={agency.url || ''}
                                                        onChange={(e) => {
                                                            const updated = [...(serviceRouting.routing_config.third_party_contacts || [])];
                                                            updated[idx] = { ...updated[idx], url: e.target.value };
                                                            setServiceRouting(p => ({ ...p, routing_config: { ...p.routing_config, third_party_contacts: updated } }));
                                                        }}
                                                        className="w-full h-9 rounded-xl bg-white/10 border border-white/20 text-white px-3 text-xs focus:outline-none focus:border-amber-400"
                                                    />
                                                </div>
                                                <div>
                                                    <label className="block text-[11px] font-semibold text-white/70 mb-1">Custom Redirect Message (Optional)</label>
                                                    <input
                                                        placeholder="e.g. State-maintained routes require filing directly with PennDOT."
                                                        value={agency.message || ''}
                                                        onChange={(e) => {
                                                            const updated = [...(serviceRouting.routing_config.third_party_contacts || [])];
                                                            updated[idx] = { ...updated[idx], message: e.target.value };
                                                            setServiceRouting(p => ({ ...p, routing_config: { ...p.routing_config, third_party_contacts: updated } }));
                                                        }}
                                                        className="w-full h-9 rounded-xl bg-white/10 border border-white/20 text-white px-3 text-xs focus:outline-none focus:border-amber-400"
                                                    />
                                                </div>
                                            </div>

                                            {/* Exact RoadListInput Component From Screenshot for this Agency */}
                                            <div className="pt-2">
                                                <RoadListInput
                                                    id={`agency-roads-${idx}`}
                                                    label={`Roads handled by ${agency.name || 'this agency'}`}
                                                    tone="danger"
                                                    hint={`Reports on these roads are redirected to ${agency.name || 'this agency'} instead of filed.`}
                                                    value={agency.road_list || ''}
                                                    onChange={(val) => {
                                                        const updated = [...(serviceRouting.routing_config.third_party_contacts || [])];
                                                        updated[idx] = { ...updated[idx], road_list: val };
                                                        
                                                        // Sync combined road lists into exclusion_list for GIS map matching
                                                        const allRoads = updated.map((a: any) => a.road_list || '').filter(Boolean).join(', ');
                                                        setServiceRouting(p => ({
                                                            ...p,
                                                            routing_config: {
                                                                ...p.routing_config,
                                                                third_party_contacts: updated,
                                                                exclusion_list: allRoads || p.routing_config.exclusion_list
                                                            }
                                                        }));
                                                    }}
                                                />
                                            </div>
                                        </div>
                                    );
                                })}

                                {(serviceRouting.routing_config.third_party_contacts || []).length === 0 && (
                                    <div className="p-5 rounded-3xl bg-white/[0.02] border border-dashed border-white/15 text-center text-xs text-white/40">
                                        No 3rd party agencies added yet. Click "+ Add 3rd Party Agency" above to list agencies like PennDOT or Mercer County DPW.
                                    </div>
                                )}

                                {/* Coverage preview, directly beneath the agency cards it describes.
                                    Typing a road name claims every segment the data files under that
                                    name, which is not always the stretch the agency actually
                                    maintains -- a spur, a block the town keeps, or a continuation
                                    past the border get swept in, and the rule looks correct while
                                    covering the wrong thing. Corrections made here are stored as a
                                    diff against the road name, so a monthly data refresh still picks
                                    up a newly built block. */}
                                {(serviceRouting.routing_config.third_party_contacts || []).length > 0 && (
                                    <div className="pt-2">
                                        <RoadCorridorMap
                                            roads={
                                                (serviceRouting.routing_config.third_party_contacts || [])
                                                    .map((a: any) => (typeof a?.road_list === 'string'
                                                        ? a.road_list
                                                        : (Array.isArray(a?.roads) ? a.roads.join(', ') : '')))
                                                    .filter(Boolean)
                                                    .join(', ')
                                            }
                                            townshipBoundary={townshipBoundary}
                                            excludedFeatureIds={excludedSegments}
                                            onExcludedChange={setExcludedSegments}
                                            trims={segmentTrims}
                                            onTrimsChange={setSegmentTrims}
                                            corridorMetres={corridorMetres}
                                            onCorridorMetresChange={setCorridorMetres}
                                            config={mapConfig}
                                        />
                                    </div>
                                )}
                            </div>

                            {/* Default Fallback Redirect Message */}

                        </div>
                    )}

                    {/* Custom Questions Builder - Only for township and road_based */}
                    {serviceRouting.routing_mode !== 'third_party' && (
                        <div className="space-y-4 p-4 rounded-lg bg-purple-500/10 border border-purple-500/20">
                            <div className="flex items-center justify-between">
                                <h4 className="font-medium text-purple-300 flex items-center gap-2">
                                    <AlertCircle className="w-4 h-4" /> Custom Follow-Up Questions
                                </h4>
                                <button
                                    type="button"
                                    onClick={() => setServiceRouting(p => ({
                                        ...p,
                                        routing_config: {
                                            ...p.routing_config,
                                            custom_questions: [
                                                ...p.routing_config.custom_questions,
                                                { id: crypto.randomUUID(), label: '', type: 'text', options: [], required: false, placeholder: '' }
                                            ]
                                        }
                                    }))}
                                    className="text-sm text-purple-400 hover:text-purple-300 flex items-center gap-1"
                                >
                                    <Plus className="w-4 h-4" /> Add Question
                                </button>
                            </div>

                            {serviceRouting.routing_config.custom_questions.length === 0 ? (
                                <p className="text-sm text-white/40 italic">No custom questions. Add questions to collect additional info from residents.</p>
                            ) : (
                                <div className="space-y-3">
                                    {serviceRouting.routing_config.custom_questions.map((q, idx) => (
                                        <div key={q.id} className="p-3 rounded-lg bg-white/5 border border-white/10 space-y-2">
                                            <div className="flex justify-between items-center">
                                                <span className="text-xs text-white/40">Question {idx + 1}</span>
                                                <button type="button" aria-label="Remove question" onClick={() => {
                                                    const newQs = serviceRouting.routing_config.custom_questions.filter((_, i) => i !== idx);
                                                    setServiceRouting(p => ({ ...p, routing_config: { ...p.routing_config, custom_questions: newQs } }));
                                                }} className="p-1 text-red-400 hover:text-red-300"><X className="w-3 h-3" /></button>
                                            </div>

                                            {/* Question Label */}
                                            <input
                                                aria-label="Question text"
                                                placeholder="Question text..."
                                                value={q.label}
                                                onChange={(e) => {
                                                    const newQs = [...serviceRouting.routing_config.custom_questions];
                                                    newQs[idx] = { ...newQs[idx], label: e.target.value };
                                                    setServiceRouting(p => ({ ...p, routing_config: { ...p.routing_config, custom_questions: newQs } }));
                                                }}
                                                className="w-full h-9 rounded-lg bg-white/10 border border-white/20 text-white px-3 text-sm"
                                            />

                                            {/* Type and Required Row */}
                                            <div className="flex gap-2 items-center">
                                                <select
                                                    aria-label="Question type"
                                                    value={q.type}
                                                    onChange={(e) => {
                                                        const newQs = [...serviceRouting.routing_config.custom_questions];
                                                        newQs[idx] = { ...newQs[idx], type: e.target.value };
                                                        setServiceRouting(p => ({ ...p, routing_config: { ...p.routing_config, custom_questions: newQs } }));
                                                    }}
                                                    className="flex-1 h-9 rounded-lg bg-white/10 border border-white/20 text-white px-2 text-sm"
                                                >
                                                    <option value="text">Text (short)</option>
                                                    <option value="textarea">Text (long)</option>
                                                    <option value="number">Number</option>
                                                    <option value="date">Date</option>
                                                    <option value="yes_no">Yes / No</option>
                                                    <option value="select">Dropdown</option>
                                                    <option value="radio">Radio Buttons</option>
                                                    <option value="checkbox">Checkboxes</option>
                                                </select>
                                                <label className="flex items-center gap-2 text-sm text-white/70">
                                                    <input
                                                        type="checkbox"
                                                        checked={q.required}
                                                        onChange={(e) => {
                                                            const newQs = [...serviceRouting.routing_config.custom_questions];
                                                            newQs[idx] = { ...newQs[idx], required: e.target.checked };
                                                            setServiceRouting(p => ({ ...p, routing_config: { ...p.routing_config, custom_questions: newQs } }));
                                                        }}
                                                        className="rounded"
                                                    />
                                                    Required
                                                </label>
                                            </div>

                                            {/* Options (for select/radio/checkbox) */}
                                            {['select', 'radio', 'checkbox'].includes(q.type) && (
                                                <input
                                                    aria-label="Question options"
                                                    placeholder="Options (comma-separated): Option A, Option B, Option C"
                                                    value={q.options?.join(', ') || ''}
                                                    onChange={(e) => {
                                                        const newQs = [...serviceRouting.routing_config.custom_questions];
                                                        newQs[idx] = { ...newQs[idx], options: e.target.value.split(",").map(o => o.trim()) };
                                                        setServiceRouting(p => ({ ...p, routing_config: { ...p.routing_config, custom_questions: newQs } }));
                                                    }}
                                                    className="w-full h-9 rounded-lg bg-white/10 border border-white/20 text-white px-3 text-sm"
                                                />
                                            )}

                                            {/* Placeholder (for text types) */}
                                            {['text', 'textarea', 'number'].includes(q.type) && (
                                                <input
                                                    aria-label="Question placeholder"
                                                    placeholder="Placeholder text (optional)"
                                                    value={q.placeholder}
                                                    onChange={(e) => {
                                                        const newQs = [...serviceRouting.routing_config.custom_questions];
                                                        newQs[idx] = { ...newQs[idx], placeholder: e.target.value };
                                                        setServiceRouting(p => ({ ...p, routing_config: { ...p.routing_config, custom_questions: newQs } }));
                                                    }}
                                                    className="w-full h-9 rounded-lg bg-white/10 border border-white/20 text-white/60 px-3 text-sm"
                                                />
                                            )}
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}

                    <div className="flex justify-end gap-3 pt-4 border-t border-white/10">
                        <Button variant="ghost" onClick={() => setShowServiceEditModal(false)}>Cancel</Button>
                        <Button type="submit">Save Configuration</Button>
                    </div>
                </form>
            </Modal>

            {/* Layer Upload/Edit Modal */}
            <Modal
                isOpen={showLayerModal}
                onClose={() => setShowLayerModal(false)}
                title={editingLayer ? `Edit Layer: ${editingLayer.name}` : 'Add New Layer'}
            >
                <form
                    onSubmit={async (e) => {
                        e.preventDefault();
                        if (!newLayer.name || !newLayer.geojson) {
                            alert("Please provide a name and upload a GeoJSON file");
                            return;
                        }
                        try {
                            if (editingLayer) {
                                await api.updateMapLayer(editingLayer.id, {
                                    name: newLayer.name,
                                    description: newLayer.description,
                                    layer_type: newLayer.layer_type,
                                    fill_color: newLayer.fill_color,
                                    stroke_color: newLayer.stroke_color,
                                    fill_opacity: newLayer.fill_opacity,
                                    stroke_width: newLayer.stroke_width,
                                    service_codes: newLayer.service_codes,
                                    geojson: newLayer.geojson,
                                    routing_mode: newLayer.routing_mode,
                                    routing_config: newLayer.routing_config,
                                    visible_on_map: newLayer.visible_on_map,
                                });
                                setSaveMessage('Layer updated!');
                            } else {
                                await api.createMapLayer({
                                    name: newLayer.name,
                                    description: newLayer.description,
                                    layer_type: newLayer.layer_type,
                                    fill_color: newLayer.fill_color,
                                    stroke_color: newLayer.stroke_color,
                                    fill_opacity: newLayer.fill_opacity,
                                    stroke_width: newLayer.stroke_width,
                                    service_codes: newLayer.service_codes,
                                    geojson: newLayer.geojson,
                                    routing_mode: newLayer.routing_mode,
                                    routing_config: newLayer.routing_config,
                                    visible_on_map: newLayer.visible_on_map,
                                });
                                setSaveMessage('Layer created!');
                            }
                            setShowLayerModal(false);
                            loadTabData();
                            setTimeout(() => setSaveMessage(null), 3000);
                        } catch (err: any) {
                            console.error('Failed to save layer:', err);
                            alert(err.message || 'Failed to save layer');
                        }
                    }}
                    className="space-y-4"
                >
                    <Input
                        label="Layer Name"
                        placeholder="e.g., Parks, Storm Drains, Utilities"
                        value={newLayer.name}
                        onChange={(e) => setNewLayer(p => ({ ...p, name: e.target.value }))}
                        required
                    />

                    <Input
                        label="Description (optional)"
                        placeholder="Brief description of this layer"
                        value={newLayer.description}
                        onChange={(e) => setNewLayer(p => ({ ...p, description: e.target.value }))}
                    />

                    {/* Layer Type Selection - Must choose first */}
                    <div className="space-y-2">
                        <label className="block text-sm font-medium text-white/70">
                            Layer Type <span className="text-red-400">*</span>
                        </label>
                        <div className="grid grid-cols-1 gap-3">
                            <button
                                type="button"
                                onClick={() => setNewLayer(p => ({ ...p, layer_type: 'point' }))}
                                className={`p-4 rounded-lg border-2 transition-all ${newLayer.layer_type === 'point'
                                    ? 'border-primary-500 bg-primary-500/20 text-white'
                                    : 'border-white/20 bg-white/5 text-white/60 hover:border-white/40'
                                    }`}
                            >
                                <div className="text-2xl mb-1">📍</div>
                                <div className="font-semibold">Points</div>
                                <div className="text-xs opacity-60">Individual locations (trees, lights, signs, etc.)</div>
                            </button>
                        </div>
                    </div>

                    {/* Only show rest of form after layer type is selected */}
                    {newLayer.layer_type && (
                        <>

                            <div className="grid grid-cols-2 gap-4">
                                <div>
                                    <label className="block text-sm font-medium text-white/70 mb-2">Fill Color</label>
                                    <div className="flex gap-2">
                                        <input
                                            type="color"
                                            value={newLayer.fill_color}
                                            onChange={(e) => setNewLayer(p => ({ ...p, fill_color: e.target.value }))}
                                            className="w-12 h-10 rounded cursor-pointer"
                                        />
                                        <input
                                            type="text"
                                            value={newLayer.fill_color}
                                            onChange={(e) => setNewLayer(p => ({ ...p, fill_color: e.target.value }))}
                                            className="flex-1 h-10 rounded-lg bg-white/10 border border-white/20 text-white px-3"
                                        />
                                    </div>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-white/70 mb-2">Stroke Color</label>
                                    <div className="flex gap-2">
                                        <input
                                            type="color"
                                            value={newLayer.stroke_color}
                                            onChange={(e) => setNewLayer(p => ({ ...p, stroke_color: e.target.value }))}
                                            className="w-12 h-10 rounded cursor-pointer"
                                        />
                                        <input
                                            type="text"
                                            value={newLayer.stroke_color}
                                            onChange={(e) => setNewLayer(p => ({ ...p, stroke_color: e.target.value }))}
                                            className="flex-1 h-10 rounded-lg bg-white/10 border border-white/20 text-white px-3"
                                        />
                                    </div>
                                </div>
                            </div>

                            <div className="grid grid-cols-2 gap-4">
                                <div>
                                    <label className="block text-sm font-medium text-white/70 mb-2">
                                        Fill Opacity: {Math.round(newLayer.fill_opacity * 100)}%
                                    </label>
                                    <input
                                        type="range"
                                        min="0"
                                        max="1"
                                        step="0.1"
                                        value={newLayer.fill_opacity}
                                        onChange={(e) => setNewLayer(p => ({ ...p, fill_opacity: parseFloat(e.target.value) }))}
                                        className="w-full"
                                    />
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-white/70 mb-2">
                                        Stroke Width: {newLayer.stroke_width}px
                                    </label>
                                    <input
                                        type="range"
                                        min="1"
                                        max="10"
                                        value={newLayer.stroke_width}
                                        onChange={(e) => setNewLayer(p => ({ ...p, stroke_width: parseInt(e.target.value) }))}
                                        className="w-full"
                                    />
                                </div>
                            </div>

                            {/* Nominatim Boundary Search - Only for polygons */}
                            {newLayer.layer_type === 'polygon' && (
                                <div className="space-y-3 p-4 rounded-lg bg-white/5 border border-white/10">
                                    <label className="block text-sm font-medium text-white/70">
                                        Search for Boundary (from OpenStreetMap)
                                    </label>
                                    <div className="flex gap-2">
                                        <input
                                            type="text"
                                            placeholder="e.g., Princeton University, Central Park..."
                                            value={nominatimSearch}
                                            onChange={(e) => setNominatimSearch(e.target.value)}
                                            onKeyDown={(e) => {
                                                if (e.key === 'Enter') {
                                                    e.preventDefault();
                                                    // Trigger search
                                                    if (nominatimSearch.trim()) {
                                                        setIsSearchingNominatim(true);
                                                        // Through our own backend, not the browser.
                                                        // A direct call is refused by the
                                                        // Content-Security-Policy, and cannot set the
                                                        // User-Agent Nominatim's usage policy requires.
                                                        api.searchOsmTownship(nominatimSearch)
                                                            .then(({ results }) => setNominatimResults(results))
                                                            .catch(console.error)
                                                            .finally(() => setIsSearchingNominatim(false));
                                                    }
                                                }
                                            }}
                                            className="flex-1 h-10 rounded-lg bg-white/10 border border-white/20 text-white px-3 placeholder:text-white/40"
                                        />
                                        <Button
                                            type="button"
                                            variant="secondary"
                                            onClick={() => {
                                                if (nominatimSearch.trim()) {
                                                    setIsSearchingNominatim(true);
                                                    api.searchOsmTownship(nominatimSearch)
                                                        .then(({ results }) => setNominatimResults(results))
                                                        .catch(console.error)
                                                        .finally(() => setIsSearchingNominatim(false));
                                                }
                                            }}
                                            disabled={isSearchingNominatim}
                                        >
                                            {isSearchingNominatim ? 'Searching...' : 'Search'}
                                        </Button>
                                    </div>

                                    {nominatimResults.length > 0 && (
                                        <div className="max-h-48 overflow-y-auto space-y-1">
                                            {nominatimResults.map((result: any, idx: number) => (
                                                <button
                                                    key={idx}
                                                    type="button"
                                                    className="w-full text-left p-2 rounded-lg bg-white/5 hover:bg-white/10 text-white/80 text-sm transition-colors"
                                                    onClick={async () => {
                                                        // Fetch the actual boundary GeoJSON
                                                        setIsSearchingNominatim(true);
                                                        try {
                                                            // No second lookup: /gis/osm/search asks
                                                            // Nominatim for polygon_geojson and hands the
                                                            // geometry back with the result. The old
                                                            // /details call was a browser request to
                                                            // Nominatim, which the CSP refuses anyway.
                                                            const details = { geometry: (result as { geojson?: object }).geojson };
                                                            if (details.geometry) {
                                                                const geojson = {
                                                                    type: 'Feature',
                                                                    properties: {
                                                                        name: result.display_name,
                                                                        osm_id: result.osm_id,
                                                                    },
                                                                    geometry: details.geometry,
                                                                };
                                                                setNewLayer(p => ({
                                                                    ...p,
                                                                    geojson,
                                                                    name: p.name || result.display_name.split(",")[0].trim()
                                                                }));
                                                                setNominatimResults([]);
                                                                setNominatimSearch('');
                                                            } else {
                                                                alert("Could not fetch boundary for this location");
                                                            }
                                                        } catch (err) {
                                                            console.error('Failed to fetch boundary:', err);
                                                            alert("Failed to fetch boundary");
                                                        } finally {
                                                            setIsSearchingNominatim(false);
                                                        }
                                                    }}
                                                >
                                                    <div className="font-medium">{result.display_name.split(",")[0]}</div>
                                                    <div className="text-xs text-white/50 truncate">{result.display_name}</div>
                                                </button>
                                            ))}
                                        </div>
                                    )}

                                    <p className="text-xs text-white/40">
                                        Press Enter or click Search to find boundaries. Select a result to load its boundary.
                                    </p>
                                </div>
                            )}

                            <div>
                                <label className="block text-sm font-medium text-white/70 mb-2">
                                    {newLayer.layer_type === 'polygon' ? 'Or Upload GeoJSON File' : 'GeoJSON File'}
                                </label>
                                <input
                                    type="file"
                                    accept=".geojson,.json"
                                    onChange={async (e) => {
                                        const file = e.target.files?.[0];
                                        if (!file) return;
                                        try {
                                            const text = await file.text();
                                            const geojson = JSON.parse(text);
                                            if (!geojson.type) {
                                                throw new Error('Invalid GeoJSON format');
                                            }
                                            setNewLayer(p => ({ ...p, geojson }));
                                        } catch (err) {
                                            alert("Failed to parse GeoJSON file");
                                        }
                                        e.target.value = '';
                                    }}
                                    className="w-full text-sm text-white/60 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-primary-500 file:text-white hover:file:bg-primary-600 file:cursor-pointer"
                                />
                                {newLayer.geojson && (
                                    <p className="text-xs text-green-400 mt-1">✓ GeoJSON loaded</p>
                                )}
                            </div>

                            {/* Category selector */}
                            <div>
                                <div className="flex items-center justify-between mb-2">
                                    <label className="block text-sm font-medium text-white/70">
                                        Show for Categories
                                    </label>
                                    <div className="flex gap-2">
                                        <button
                                            type="button"
                                            onClick={() => setNewLayer(p => ({ ...p, service_codes: services.map(s => s.service_code) }))}
                                            className="text-xs text-primary-400 hover:text-primary-300"
                                        >
                                            Select All
                                        </button>
                                        <span className="text-white/20">|</span>
                                        <button
                                            type="button"
                                            onClick={() => setNewLayer(p => ({ ...p, service_codes: [] }))}
                                            className="text-xs text-white/40 hover:text-white/60"
                                        >
                                            Clear All
                                        </button>
                                    </div>
                                </div>
                                {services.length === 0 ? (
                                    <p className="text-sm text-white/40 p-3 rounded-lg bg-white/5 border border-white/10">
                                        Loading categories...
                                    </p>
                                ) : (
                                    <div className="grid grid-cols-2 gap-2 max-h-48 overflow-y-auto p-3 rounded-lg bg-white/5 border border-white/10">
                                        {services.map((service) => (
                                            <label
                                                key={service.service_code}
                                                className="flex items-center gap-2 text-sm text-white/70 hover:text-white cursor-pointer p-2 rounded hover:bg-white/10"
                                            >
                                                <input
                                                    type="checkbox"
                                                    checked={newLayer.service_codes.includes(service.service_code)}
                                                    onChange={(e) => {
                                                        if (e.target.checked) {
                                                            setNewLayer(p => ({
                                                                ...p,
                                                                service_codes: [...p.service_codes, service.service_code]
                                                            }));
                                                        } else {
                                                            setNewLayer(p => ({
                                                                ...p,
                                                                service_codes: p.service_codes.filter(c => c !== service.service_code)
                                                            }));
                                                        }
                                                    }}
                                                    className="w-4 h-4 rounded border-white/30 bg-white/10 text-primary-500 focus:ring-primary-500 focus:ring-offset-0"
                                                />
                                                {service.service_name}
                                            </label>
                                        ))}
                                    </div>
                                )}
                                <p className="text-xs text-white/40 mt-1">
                                    {newLayer.service_codes.length === 0
                                        ? '⚠️ Layer will be hidden (select at least one category)'
                                        : newLayer.service_codes.length === services.length
                                            ? '✓ Layer visible for all categories'
                                            : `Layer visible for ${newLayer.service_codes.length} ${newLayer.service_codes.length === 1 ? 'category' : 'categories'}`
                                    }
                                </p>
                            </div>

                            <div className="flex justify-end gap-3 pt-4 border-t border-white/10">
                                <Button variant="ghost" onClick={() => setShowLayerModal(false)}>Cancel</Button>
                                <Button type="submit" disabled={!newLayer.layer_type}>{editingLayer ? 'Update Layer' : 'Create Layer'}</Button>
                            </div>
                        </>
                    )}
                </form>
            </Modal>

            {/* Password management handled by Auth0 SSO */}
        </div>
    );
}
