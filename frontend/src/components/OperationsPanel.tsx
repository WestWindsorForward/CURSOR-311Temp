import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
    Server, Database, RefreshCw, Play, Trash2, HardDrive, Clock,
    CheckCircle, XCircle, Loader2, RotateCcw, Wrench,
    Cloud, Activity, AlertTriangle, Cpu
} from 'lucide-react';
import { Card, Button } from './ui';
import api, { HealthDashboard, RunbookResult, ProactiveHealth, HealthCheck } from '../services/api';
import type { ConnectorHealth } from '../types';
import { ALERTING_STATUSES, hasAlert } from './capabilityUI';
import { useDialog } from './DialogProvider';

/* Same list the provider cards gate their mute button on, so "is this
 * alerting?" cannot mean two different things in two places. */
const ALERTING: readonly string[] = ALERTING_STATUSES;

interface ServiceStatus {
    status: 'running' | 'stopped' | 'unknown' | 'error' | 'not_configured';
    uptime?: string;
    error?: string;
}


// Resolution tips for common issues
const RESOLUTION_TIPS: Record<string, { issue: string; steps: string[] }> = {
    /* "Click Restart Backend" is not a step everywhere.
     *
     * The button only works where the app can reach Docker Compose and the
     * project directory, and it says so honestly when it cannot -- but these
     * instructions told a clerk to click it regardless, so on a managed or
     * single-container deployment the first suggested remedy is one that
     * always fails. The host-side command comes first now, because it works
     * everywhere. */
    backend: { issue: 'Backend API not responding', steps: ['Restart it on the host: docker compose restart backend', 'Or press Restart below, where this deployment allows it', 'Check the server logs'] },
    frontend: { issue: 'Frontend not reachable', steps: ['Check FRONTEND_HOST — a wrong address looks the same as a stopped service', 'Restart on the host: docker compose restart frontend', 'Review build errors'] },
    db: { issue: 'Database connection failed', steps: ['Check PostgreSQL is running', 'Check free disk space', 'Review connection limits'] },
    redis: { issue: 'Redis cache unavailable', steps: ['Restart on the host: docker compose restart redis', 'Check Redis logs', 'Verify memory limits'] },
    caddy: { issue: 'Reverse proxy not routing', steps: ['If you reached this page through the proxy, it is routing', 'Check the Caddyfile', 'Verify SSL certificates'] }
};

export default function OperationsPanel() {
    const [health, setHealth] = useState<HealthDashboard | null>(null);
    /* Derived from the same endpoint the provider cards read, rather than
     * from a hardcoded list of vendors this deployment may not even use. */
    const [connectorRollup, setConnectorRollup] = useState<
        { working: number; failing: number; unchecked: number; names: string[] } | null
    >(null);
    const [proactive, setProactive] = useState<ProactiveHealth | null>(null);
    const [systemProbes, setSystemProbes] = useState<ConnectorHealth[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [runbookLoading, setRunbookLoading] = useState<string | null>(null);
    const [lastAction, setLastAction] = useState<RunbookResult | null>(null);
    /* Which check's mute is in flight, and any local override of the server's
     * answer. The panel refetches every 30s, so without the override the row
     * would snap back to its old state for up to half a minute after the click
     * and read as a button that did nothing. */
    const [mutingCheck, setMutingCheck] = useState<string | null>(null);
    const [muteOverride, setMuteOverride] = useState<Record<string, string | null>>({});
    /* Carries which row failed, so the message lands under that row's card
     * rather than under both of them. */
    const [muteError, setMuteError] = useState<{ id: string; message: string } | null>(null);
    const dialog = useDialog();

    const mutedUntilOf = (c: HealthCheck): string | null =>
        c.key in muteOverride ? muteOverride[c.key] : (c.muted ? (c.muted_until ?? null) : null);
    const mutedNow = (c: HealthCheck): boolean =>
        c.key in muteOverride ? muteOverride[c.key] !== null : !!c.muted;

    /* Mute and unmute are the same endpoint; `days: 0` lifts it. Local state
     * only moves on success -- a failed mute that greyed the row anyway would
     * be the worst outcome available, an admin believing they have silenced an
     * alarm that is still going to page them. */
    const runMute = async (
        id: string,
        isMuted: boolean,
        call: (days: number | undefined) => Promise<{ muted_until: string | null }>,
    ) => {
        setMutingCheck(id);
        setMuteError(null);
        try {
            const r = await call(isMuted ? 0 : undefined);
            setMuteOverride(prev => ({ ...prev, [id]: r.muted_until }));
            fetchAll();
        } catch (err: any) {
            setMuteError({
                id,
                message: `${isMuted ? 'Alerts were not resumed' : 'Alerts were not paused'}: ${err?.message || 'the change did not save'}`,
            });
        } finally {
            setMutingCheck(null);
        }
    };

    const muteErrorFor = (ids: string[]) =>
        muteError && ids.includes(muteError.id)
            ? <p role="alert" className="text-xs mt-3 text-red-300">{muteError.message}</p>
            : null;

    const toggleCheckMute = (key: string, isMuted: boolean) =>
        runMute(key, isMuted, days => api.muteHealthCheck(key, days));

    const toggleProbeMute = (connector: string, isMuted: boolean) =>
        runMute(connector, isMuted, days => api.muteConnectorAlerts(connector, days));

    const probeMutedUntil = (c: ConnectorHealth): string | null =>
        c.connector in muteOverride ? muteOverride[c.connector] : (c.alerts_muted_until ?? null);

    /* One mute button for both surfaces, so "Mute alerts" cannot come to mean
     * two different things two cards apart.
     *
     * A plain function, not a nested component: a component declared in the
     * render body is a new element *type* every render, which remounts the
     * button on every 30-second poll. */
    const muteButton = ({ id, isMuted, label, onToggle }: {
        id: string; isMuted: boolean; label: string; onToggle: () => void;
    }) => (
        <button
            type="button"
            onClick={onToggle}
            disabled={mutingCheck !== null}
            aria-label={isMuted ? `Unmute ${label}` : `Mute alerts for ${label}`}
            title={isMuted
                ? 'Start emailing administrators about this again'
                : 'Stop emailing administrators about this for a week. The status stays as it is.'}
            className="shrink-0 px-2.5 py-1 text-xs rounded-md border border-slate-600/60 text-gray-300 hover:text-white hover:border-slate-400/60 disabled:opacity-50 disabled:cursor-not-allowed"
        >
            {mutingCheck === id
                ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                : (isMuted ? 'Unmute' : 'Mute alerts')}
        </button>
    );

    const fetchAll = async () => {
        setIsLoading(true);
        setError(null);
        try {
            // Every source is fetched independently and tolerant of failure, so a
            // single failing probe (e.g. infra introspection in a managed env)
            // degrades that section instead of blanking the whole panel.
            const [healthData, connectorData, proactiveData] = await Promise.all([
                api.getHealthDashboard().catch(() => null),
                api.getConnectorHealth().catch(() => null),
                api.getProactiveHealth().catch(() => null),
            ]);
            setHealth(healthData);
            if (connectorData) {
                /* The infrastructure probes ride in the same table so they
                 * inherit the alerting and the mute, but they belong to the
                 * panel above rather than to this roll-up. */
                const external = connectorData.connectors.filter(c => !c.connector.startsWith('system:'));
                /* The infrastructure probes email admins through the same
                 * digest as every other connector, and until now they were
                 * rendered nowhere at all -- an alert with no card and
                 * therefore no way to acknowledge it. Only the ones actually
                 * alerting, or already muted, earn a row. */
                setSystemProbes(connectorData.connectors.filter(
                    c => c.connector.startsWith('system:')
                        && (ALERTING.includes(c.status) || !!c.alerts_muted_until)
                ));
                setConnectorRollup({
                    working: external.filter(c => c.status === 'working').length,
                    failing: external.filter(c => c.status === 'failing' || c.status === 'down').length,
                    unchecked: external.filter(c => c.status === 'unknown' || c.status === 'stale').length,
                    names: external
                        .filter(c => c.status === 'failing' || c.status === 'down')
                        .map(c => c.connector),
                });
            }
            setProactive(proactiveData);
            // Only show the hard error state if literally nothing loaded.
            if (!healthData && !connectorData && !proactiveData) {
                setError('Failed to fetch system status');
            }
        } catch (err: any) {
            setError(err.message || 'Failed to fetch system status');
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => {
        fetchAll();
        const interval = setInterval(fetchAll, 30000);
        return () => clearInterval(interval);
    }, []);

    const executeRunbook = async (action: string, label: string) => {
        const confirmed = await dialog.confirm({
            title: `Execute: ${label}`,
            message: `Are you sure you want to execute "${label}"?\n\nThis action will be logged and may briefly affect availability.`,
            variant: action.includes('restart') ? 'warning' : 'info',
            confirmText: 'Execute',
        });

        if (!confirmed) return;

        setRunbookLoading(action);
        try {
            const result = await api.executeRunbook(action);
            setLastAction(result);
            setTimeout(fetchAll, 2000);
        } catch (err: any) {
            setLastAction({
                action,
                executed_by: 'unknown',
                timestamp: new Date().toISOString(),
                status: 'error',
                details: { error: err.message },
            });
        } finally {
            setRunbookLoading(null);
        }
    };

    const getStatusBadge = (status: string) => {
        const colors: Record<string, string> = {
            running: 'bg-green-500/20 text-green-300 border-green-500/30',
            healthy: 'bg-green-500/20 text-green-300 border-green-500/30',
            configured: 'bg-green-500/20 text-green-300 border-green-500/30',
            stopped: 'bg-red-500/20 text-red-300 border-red-500/30',
            error: 'bg-red-500/20 text-red-300 border-red-500/30',
            unknown: 'bg-yellow-500/20 text-yellow-300 border-yellow-500/30',
            not_configured: 'bg-gray-500/20 text-gray-300 border-gray-500/30',
            disabled: 'bg-gray-500/20 text-gray-300 border-gray-500/30',
            fallback: 'bg-yellow-500/20 text-yellow-300 border-yellow-500/30',
        };
        return colors[status] || colors.unknown;
    };

    const canRestart = (name: string) => ['backend', 'frontend', 'redis', 'caddy'].includes(name);

    // Find services with issues
    const degradedServices = health ?
        Object.entries(health.services)
            .filter(([_, s]) => (s as ServiceStatus).status !== 'running')
            .map(([name]) => name)
        : [];

    if (error) {
        return (
            <Card className="bg-red-500/10 border-red-500/20">
                <div className="flex items-center gap-3">
                    <XCircle className="w-6 h-6 text-red-400" />
                    <div className="flex-1">
                        <h3 className="text-lg font-semibold text-red-300">Error Loading Dashboard</h3>
                        <p className="text-red-200/80 mt-1">{error}</p>
                    </div>
                    <Button onClick={fetchAll} disabled={isLoading}>
                        <RefreshCw className={`w-4 h-4 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
                        Retry
                    </Button>
                </div>
            </Card>
        );
    }

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-xl font-bold text-white flex items-center gap-2">
                        <Activity className="w-6 h-6 text-blue-400" />
                        System Dashboard
                    </h2>
                    <p className="text-gray-300 text-sm mt-1">
                        Infrastructure, integrations, and emergency operations
                    </p>
                </div>
                <Button onClick={fetchAll} disabled={isLoading} variant="secondary">
                    <RefreshCw className={`w-4 h-4 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
                    Refresh
                </Button>
            </div>

            {/* Status Summary Cards */}
            {health && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    {/* Infrastructure Status */}
                    <Card className={`${health.overall_status === 'healthy' ? 'bg-green-500/10 border-green-500/30' : 'bg-yellow-500/10 border-yellow-500/30'}`}>
                        <div className="flex items-center gap-3">
                            <Server className="w-8 h-8 text-blue-400" />
                            <div>
                                <p className="text-gray-300 text-xs uppercase tracking-wide">Infrastructure</p>
                                <p className="text-white font-semibold">
                                    {Object.values(health.services).filter((s: any) => s.status === 'running').length}/{Object.keys(health.services).length} Running
                                </p>
                            </div>
                        </div>
                    </Card>

                    {/* The "Integrations — 0/0 Configured" tile that was here has
                        gone. On a town with nothing connected it read "0/0
                        Configured" in a green card, which is simultaneously
                        alarming and meaningless: zero of zero is not a state
                        anybody needs a tile for, and green said everything was
                        fine about something that did not exist.

                        The roll-up further down this page already lists the
                        service providers a town actually uses, by name, with
                        what the last check found. That answers the question
                        this tile was gesturing at. */}

                    {/* Database Status */}
                    <Card className={`${health.database.status === 'healthy' ? 'bg-green-500/10 border-green-500/30' : 'bg-red-500/10 border-red-500/30'}`}>
                        <div className="flex items-center gap-3">
                            <Database className="w-8 h-8 text-purple-400" />
                            <div>
                                <p className="text-gray-300 text-xs uppercase tracking-wide">PostgreSQL</p>
                                <p className="text-white font-semibold">{health.database.size || '?'}</p>
                                <p className="text-gray-500 text-xs">{health.database.connections || 0} connections</p>
                            </div>
                        </div>
                    </Card>

                    {/* Cache Status */}
                    <Card className={`${health.cache.status === 'healthy' ? 'bg-green-500/10 border-green-500/30' : 'bg-yellow-500/10 border-yellow-500/30'}`}>
                        <div className="flex items-center gap-3">
                            <HardDrive className="w-8 h-8 text-orange-400" />
                            <div>
                                <p className="text-gray-300 text-xs uppercase tracking-wide">Redis Cache</p>
                                <p className="text-white font-semibold">{health.cache.used_memory || 'N/A'}</p>
                            </div>
                        </div>
                    </Card>
                </div>
            )}

            {/* What this container is using of what it is allowed.

                These three readings existed but were only rendered once one of
                them crossed a threshold -- until then the whole section
                collapsed to a single line saying everything passed. So the
                answer to "how much memory is left?" was unavailable at exactly
                the times somebody thinks to ask it, which is usually before
                anything is wrong.

                Read from the same probes the hourly sweep and the alert email
                use. A second measurement rendered here would be a second
                opinion that drifts from the one that raises the alarm. */}
            {proactive && (
                <Card>
                    <h3 className="text-lg font-semibold text-white mb-1 flex items-center gap-2">
                        <Cpu className="w-5 h-5 text-cyan-400" />
                        Container resources
                    </h3>
                    <p className="text-gray-400 text-xs mb-4">
                        Measured against this container's own limits, not the server's total.
                    </p>
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                        {['memory', 'disk', 'cpu'].map(key => {
                            const c = proactive.checks.find(x => x.key === key);
                            if (!c) return null;
                            const pct = typeof c.value === 'number' ? c.value : null;
                            // A gauge with no reading is drawn as no reading.
                            // An empty bar reads as 0% used, which is the most
                            // reassuring possible rendering of "we could not
                            // measure this".
                            const bar = c.status === 'critical' ? 'bg-red-400'
                                : c.status === 'warning' ? 'bg-amber-400'
                                    : 'bg-emerald-400';
                            return (
                                <div key={key} className="bg-slate-800/50 rounded-lg p-4 border border-slate-700/50">
                                    <div className="flex items-baseline justify-between mb-2">
                                        <span className="text-gray-300 text-xs uppercase tracking-wide">{c.label}</span>
                                        <span className={`text-lg font-semibold ${c.status === 'critical' ? 'text-red-300'
                                            : c.status === 'warning' ? 'text-amber-300' : 'text-white'}`}>
                                            {pct === null ? '—' : `${pct}%`}
                                        </span>
                                    </div>
                                    <div
                                        className="h-1.5 rounded-full bg-white/10 overflow-hidden"
                                        role="meter"
                                        aria-label={c.label}
                                        aria-valuenow={pct ?? undefined}
                                        aria-valuemin={0}
                                        aria-valuemax={100}
                                    >
                                        {pct !== null && (
                                            <div className={`h-full ${bar}`} style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
                                        )}
                                    </div>
                                    <p className="text-gray-400 text-xs mt-2">{c.message}</p>
                                </div>
                            );
                        })}
                    </div>
                </Card>
            )}

            {/* Proactive (leading-indicator) health — warns before something fails */}
            {proactive && (
                proactive.overall_status === 'ok' ? (
                    <Card className="bg-green-500/5 border-green-500/20">
                        <div className="flex items-center gap-3">
                            <CheckCircle className="w-5 h-5 text-green-400" />
                            <p className="text-green-200/90 text-sm font-medium">
                                All early-warning checks passing (disk, memory, database, backups, cache).
                            </p>
                        </div>
                    </Card>
                ) : (
                    <Card className={proactive.overall_status === 'critical' ? 'bg-red-500/5 border-red-500/30' : 'bg-amber-500/5 border-amber-500/30'}>
                        <h3 className={`text-lg font-semibold mb-1 flex items-center gap-2 ${proactive.overall_status === 'critical' ? 'text-red-300' : 'text-amber-300'}`}>
                            <AlertTriangle className="w-5 h-5" />
                            Needs attention {proactive.overall_status === 'critical' ? '— act now' : 'soon'}
                        </h3>
                        <p className="text-gray-400 text-xs mb-3">Leading indicators — resolving these prevents an outage. Admins are emailed when a check crosses a threshold.</p>
                        <div className="space-y-2">
                            {proactive.checks
                                .filter(c => c.status === 'warning' || c.status === 'critical')
                                .map(c => {
                                    /* A muted check is still listed, and still
                                     * carries its own status badge. Muting stops
                                     * the mail; it is not a way to make the row
                                     * look like it passed. The de-emphasis is on
                                     * the row, never on the badge. */
                                    const isMuted = mutedNow(c);
                                    return (
                                        <div
                                            key={c.key}
                                            data-testid={`health-check-${c.key}`}
                                            data-muted={isMuted ? 'true' : 'false'}
                                            className={`flex items-start gap-3 rounded-lg p-3 border ${isMuted
                                                ? 'bg-slate-800/25 border-slate-700/30'
                                                : 'bg-slate-800/50 border-slate-700/50'}`}
                                        >
                                            <span className={`px-2 py-0.5 mt-0.5 text-xs rounded-full border shrink-0 ${getStatusBadge(c.status === 'critical' ? 'error' : 'fallback')}`}>
                                                {c.status}
                                            </span>
                                            <div className={`min-w-0 flex-1 ${isMuted ? 'opacity-60' : ''}`}>
                                                <p className="text-white text-sm font-medium">
                                                    {c.label}: <span className="font-normal text-gray-300">{c.message}</span>
                                                </p>
                                                {c.action && <p className="text-gray-400 text-xs mt-0.5">→ {c.action}</p>}
                                                {isMuted && (
                                                    <p className="text-xs mt-1.5 text-amber-200/85">
                                                        Muted — nobody is being emailed about this
                                                        {mutedUntilOf(c) ? ` until ${new Date(mutedUntilOf(c) as string).toLocaleDateString()}` : ''}.
                                                        {' '}It is still not passing.
                                                    </p>
                                                )}
                                            </div>
                                            {muteButton({
                                                id: c.key,
                                                isMuted,
                                                label: c.label,
                                                onToggle: () => toggleCheckMute(c.key, isMuted),
                                            })}
                                        </div>
                                    );
                                })}
                        </div>
                        {muteErrorFor(proactive.checks.map(c => c.key))}
                    </Card>
                )
            )}

            {/* Infrastructure probes.
                These ride the connector health table, so they were already
                emailing admins through the connector digest -- but they were
                rendered nowhere, which meant an alert with no card and so no
                way to say "I know about this one". Only the probes actually
                alerting, or already muted, get a row: a list of five green
                lines would be noise of its own. */}
            {systemProbes.length > 0 && (
                <Card>
                    <h3 className="text-lg font-semibold text-white mb-1 flex items-center gap-2">
                        <Server className="w-5 h-5 text-cyan-400" />
                        Infrastructure probes
                    </h3>
                    <p className="text-gray-400 text-xs mb-3">
                        Checked hourly. Admins are emailed while one is failing.
                    </p>
                    <div className="space-y-2">
                        {systemProbes.map(c => {
                            const until = probeMutedUntil(c);
                            const isMuted = !!until;
                            const label = c.connector.replace(/^system:/, '');
                            return (
                                <div
                                    key={c.connector}
                                    data-testid={`system-probe-${label}`}
                                    data-muted={isMuted ? 'true' : 'false'}
                                    className={`flex items-start gap-3 rounded-lg p-3 border ${isMuted
                                        ? 'bg-slate-800/25 border-slate-700/30'
                                        : 'bg-slate-800/50 border-slate-700/50'}`}
                                >
                                    <span className={`px-2 py-0.5 mt-0.5 text-xs rounded-full border shrink-0 ${getStatusBadge(hasAlert(c.status) ? 'error' : 'unknown')}`}>
                                        {c.status}
                                    </span>
                                    <div className={`min-w-0 flex-1 ${isMuted ? 'opacity-60' : ''}`}>
                                        <p className="text-white text-sm font-medium capitalize">
                                            {label}: <span className="font-normal text-gray-300">{c.summary}</span>
                                        </p>
                                        {isMuted && (
                                            <p className="text-xs mt-1.5 text-amber-200/85">
                                                Muted — nobody is being emailed about this
                                                {` until ${new Date(until as string).toLocaleDateString()}`}.
                                                {' '}It is still not working.
                                            </p>
                                        )}
                                    </div>
                                    {muteButton({
                                        id: c.connector,
                                        isMuted,
                                        label,
                                        onToggle: () => toggleProbeMute(c.connector, isMuted),
                                    })}
                                </div>
                            );
                        })}
                    </div>
                    {muteErrorFor(systemProbes.map(c => c.connector))}
                </Card>
            )}

            {/* Infrastructure Services */}
            {health && (
                <Card>
                    <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
                        <Server className="w-5 h-5 text-blue-400" />
                        Infrastructure Services
                    </h3>
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-3">
                        {Object.entries(health.services).map(([name, service]) => {
                            const svc = service as ServiceStatus;
                            return (
                                <div key={name} className="bg-slate-800/50 rounded-lg p-3 border border-slate-700/50">
                                    <div className="flex items-center justify-between mb-2">
                                        <span className="text-white font-medium capitalize">{name}</span>
                                        <span className={`px-2 py-0.5 text-xs rounded-full border ${getStatusBadge(svc.status)}`}>
                                            {svc.status}
                                        </span>
                                    </div>
                                    <p className="text-gray-500 text-xs truncate mb-2">{svc.uptime || 'Checking...'}</p>
                                    {canRestart(name) && (
                                        <Button
                                            size="sm"
                                            variant="ghost"
                                            className="w-full text-xs"
                                            onClick={() => executeRunbook(`restart-${name}`, `Restart ${name}`)}
                                            disabled={runbookLoading !== null}
                                        >
                                            <RotateCcw className="w-3 h-3 mr-1" />
                                            Restart
                                        </Button>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                </Card>
            )}

            {/* The "Uptime Monitoring" block that was here has gone, for the
                same reason as the Cloud Integrations one below it.

                It listed whatever service names happened to be in the uptime
                table, and that table still holds rows for translation_api,
                vertex_ai, kms, secret_store and auth0 -- services the sampler
                stopped checking when it was cut back to the database alone.
                So a town saw seven dependencies, five of which nothing had
                probed since the change, reported as "only 11 of 288 checks"
                or as a bare dash.

                Worse, the honest ones were indistinguishable from the dead
                ones: "100.0%" next to "only 11 of 288 checks" is a number
                computed from almost no data, and it sat in the same column as
                a real one.

                Whether a connector is working now lives on its own card in
                Setup & Integrations, per connector, with what the last check
                actually found and when. That answers the question this panel
                was trying to answer, for the services the town actually uses.

                The sampler still records the database every five minutes and
                the health uptime endpoints still serve it -- the state panel
                reads them through telemetry. Nothing in the admin console
                does. */}


            {/* The "Cloud Integrations" block that was here has gone.
                It was a hardcoded struct -- auth0, gcp_auth, vertex_ai,
                translation_api -- so a town on Azure with Entra sign-in was
                shown checks for four services it does not use, and none for
                the four it does. It also duplicated the provider cards, which
                read the real catalog and the real health table.

                Replaced by a roll-up of the same data those cards use. This
                page keeps what only it can answer: the machine this runs on. */}
            {connectorRollup && (
                <Card>
                    <h3 className="text-lg font-semibold text-white mb-1 flex items-center gap-2">
                        <Cloud className="w-5 h-5 text-purple-400" />
                        Service providers
                    </h3>
                    <p className="text-white/55 text-xs mb-4">
                        Checked automatically once a day. Set up and changed under Setup &amp; Integration.
                    </p>
                    <div className="flex flex-wrap items-center gap-2.5">
                        {connectorRollup.working > 0 && (
                            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-2xl text-xs font-semibold border bg-gradient-to-r from-emerald-500/20 to-teal-500/20 text-emerald-300 border-emerald-500/30">
                                <CheckCircle className="w-3.5 h-3.5" /> {connectorRollup.working} working
                            </span>
                        )}
                        {connectorRollup.failing > 0 && (
                            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-2xl text-xs font-semibold border bg-gradient-to-r from-red-500/25 to-rose-500/20 text-red-200 border-red-400/35">
                                <AlertTriangle className="w-3.5 h-3.5" /> {connectorRollup.failing} not working
                            </span>
                        )}
                        {connectorRollup.unchecked > 0 && (
                            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-2xl text-xs font-semibold border bg-white/[0.07] text-white/70 border-white/15">
                                {connectorRollup.unchecked} not checked yet
                            </span>
                        )}
                    </div>
                    {connectorRollup.names.length > 0 && (
                        <p className="text-red-200/85 text-xs mt-3">
                            Not working: {connectorRollup.names.join(', ')}
                        </p>
                    )}
                </Card>
            )}

            {/* Troubleshooting Tips - shown when degraded */}
            {degradedServices.length > 0 && (
                <Card className="bg-amber-500/5 border-amber-500/20">
                    <h3 className="text-lg font-semibold text-amber-300 mb-3 flex items-center gap-2">
                        <Wrench className="w-5 h-5" />
                        Troubleshooting Tips
                    </h3>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                        {degradedServices.map(serviceName => {
                            const tips = RESOLUTION_TIPS[serviceName];
                            if (!tips) return null;
                            return (
                                <div key={serviceName} className="bg-slate-800/50 rounded-lg p-3">
                                    <h4 className="font-medium text-white text-sm capitalize mb-2">
                                        {serviceName}: {tips.issue}
                                    </h4>
                                    <ol className="list-decimal list-inside text-gray-300 text-xs space-y-1">
                                        {tips.steps.map((step, i) => (
                                            <li key={i}>{step}</li>
                                        ))}
                                    </ol>
                                </div>
                            );
                        })}
                    </div>
                </Card>
            )}

            {/* Emergency Operations */}
            <Card className="bg-slate-800/50">
                <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
                    <Play className="w-5 h-5 text-green-400" />
                    Emergency Operations
                </h3>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <Button
                        variant="secondary"
                        onClick={() => executeRunbook('restart-all', 'Restart All Services')}
                        disabled={runbookLoading !== null}
                    >
                        {runbookLoading === 'restart-all' ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <RotateCcw className="w-4 h-4 mr-2" />}
                        Restart All
                    </Button>
                    <Button
                        variant="secondary"
                        onClick={() => executeRunbook('clear-cache', 'Clear Cache')}
                        disabled={runbookLoading !== null}
                    >
                        {runbookLoading === 'clear-cache' ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Trash2 className="w-4 h-4 mr-2" />}
                        Clear Cache
                    </Button>
                    <Button
                        variant="secondary"
                        onClick={() => executeRunbook('vacuum', 'DB Maintenance')}
                        disabled={runbookLoading !== null}
                    >
                        {runbookLoading === 'vacuum' ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Database className="w-4 h-4 mr-2" />}
                        DB Vacuum
                    </Button>
                    <Button
                        variant="secondary"
                        onClick={fetchAll}
                        disabled={isLoading}
                    >
                        <RefreshCw className={`w-4 h-4 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
                        Refresh
                    </Button>
                </div>
            </Card>

            {/* Last Backup Info */}
            {health?.last_backup?.created && (
                <Card className="bg-slate-800/30">
                    <div className="flex items-center gap-3">
                        <Clock className="w-5 h-5 text-green-400" />
                        <div>
                            <span className="text-white font-medium">Last Backup</span>
                            <span className="text-gray-300 text-sm ml-3">
                                {new Date(health.last_backup.created).toLocaleString()}
                            </span>
                        </div>
                    </div>
                </Card>
            )}

            {/* Last Action Result */}
            <AnimatePresence>
                {lastAction && (
                    <motion.div
                        initial={{ opacity: 0, y: 10 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -10 }}
                    >
                        <Card className={`${lastAction.status === 'success' ? 'bg-green-500/10 border-green-500/30' : lastAction.status === 'partial' ? 'bg-amber-500/10 border-amber-500/30' : 'bg-red-500/10 border-red-500/30'}`}>
                            <div className="flex items-start gap-3">
                                {lastAction.status === 'success' ? <CheckCircle className="w-5 h-5 text-green-400 mt-0.5" /> : <XCircle className="w-5 h-5 text-red-400 mt-0.5" />}
                                <div className="flex-1 min-w-0">
                                    <span className="font-medium text-white">{lastAction.action}: {lastAction.status}</span>
                                    <span className="text-gray-300 text-sm ml-2">at {new Date(lastAction.timestamp).toLocaleTimeString()}</span>
                                    {(() => {
                                        const d = (lastAction.details || {}) as Record<string, any>;
                                        const reason = d.error || (d.failed ? Object.entries(d.failed).map(([s, e]) => `${s}: ${e}`).join(' · ') : null);
                                        return reason ? <p className="text-red-200/80 text-xs mt-1 break-words">{reason}</p> : null;
                                    })()}
                                </div>
                                <Button size="sm" variant="ghost" onClick={() => setLastAction(null)}>Dismiss</Button>
                            </div>
                        </Card>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Loading State */}
            {isLoading && !health && (
                <div className="flex items-center justify-center py-12">
                    <RefreshCw className="w-8 h-8 text-blue-400 animate-spin" />
                    <span className="ml-3 text-gray-300">Loading system dashboard...</span>
                </div>
            )}
        </div>
    );
}
