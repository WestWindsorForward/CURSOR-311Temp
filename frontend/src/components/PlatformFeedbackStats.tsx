import React from 'react';
import { XAxis, YAxis, ResponsiveContainer, AreaChart, Area, Tooltip } from 'recharts';
import { PlatformFeedbackStatistics } from '../types';
import { PLATFORM_FEEDBACK_OPTIONS, PLATFORM_FEEDBACK_QUESTION } from './PlatformFeedback';

/**
 * Aggregate platform feedback, for the statistics view.
 *
 * Counts and shares across the five options, plus responses per month drawn the
 * same way the other trends on that page are drawn. There is nothing else to
 * show: the module stores one categorical answer and a timestamp per response,
 * so there are no individual records to open, no comments to read and nobody to
 * attribute an answer to.
 *
 * NO AVERAGE. The scale is ordinal — "somewhat easier" ranks above "no
 * difference", but the distance between them is not a number anyone measured,
 * so scoring the options 1..5 and taking a mean would report a precision that
 * does not exist. The one summary figure shown is the net: the share who said
 * easier minus the share who said harder, which needs only the ordering, and it
 * is labelled as such rather than as a score.
 */

/** Scale order, best first, with a colour ramp that reads as a diverging scale
 *  rather than as five categories. */
const BAR_COLORS: Record<string, string> = {
    much_easier: 'bg-emerald-500',
    somewhat_easier: 'bg-emerald-600/70',
    no_difference: 'bg-slate-500',
    somewhat_harder: 'bg-amber-600/80',
    much_harder: 'bg-red-600',
};

interface Props {
    /** Whether the town enabled the module. When it is off this renders
     *  nothing — and the endpoint that feeds it 404s, so there is no data to
     *  render either. */
    enabled?: boolean;
    stats: PlatformFeedbackStatistics | null;
}

const PlatformFeedbackStats: React.FC<Props> = ({ enabled, stats }) => {
    if (!enabled || !stats) return null;

    const total = stats.total_responses;
    const trend = Object.entries(stats.responses_by_month || {})
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([period, responses]) => ({ period, responses }));

    return (
        <div
            className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-xl p-4 sm:p-6"
            data-testid="platform-feedback-stats"
        >
            <h2 className="text-lg font-semibold text-white mb-1">Platform Feedback</h2>
            <p className="text-xs text-white/40 mb-4">
                {PLATFORM_FEEDBACK_QUESTION} Anonymous; one answer per response, no comments collected.
            </p>

            {total === 0 ? (
                <p className="text-sm text-white/50">No responses yet.</p>
            ) : (
                <>
                    <div className="grid grid-cols-2 gap-2 sm:gap-4 mb-5">
                        <div className="bg-white/5 border border-white/10 rounded-xl p-3 sm:p-4">
                            <div className="text-[10px] sm:text-xs font-medium text-white/70 uppercase tracking-wider">Responses</div>
                            <div className="text-2xl sm:text-3xl font-bold text-white mt-1">{total}</div>
                            <div className="text-[10px] sm:text-xs text-white/60 mt-1">All time</div>
                        </div>
                        <div className="bg-white/5 border border-white/10 rounded-xl p-3 sm:p-4">
                            <div className="text-[10px] sm:text-xs font-medium text-white/70 uppercase tracking-wider">Net easier</div>
                            <div className={`text-2xl sm:text-3xl font-bold mt-1 ${stats.net_easier_percent >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                                {stats.net_easier_percent > 0 ? '+' : ''}{stats.net_easier_percent}%
                            </div>
                            {/* Spelled out because a bare percentage next to a
                                five-point scale invites being read as a score. */}
                            <div className="text-[10px] sm:text-xs text-white/60 mt-1">
                                Said easier minus said harder — not an average
                            </div>
                        </div>
                    </div>

                    <div className="space-y-2 mb-5">
                        {PLATFORM_FEEDBACK_OPTIONS.map((opt) => {
                            const count = stats.counts?.[opt.value] ?? 0;
                            const pct = stats.percentages?.[opt.value] ?? 0;
                            return (
                                <div key={opt.value} className="flex items-center gap-3">
                                    <span className="w-32 sm:w-40 shrink-0 text-xs text-white/60">{opt.label}</span>
                                    <div className="flex-1 h-4 rounded-full bg-white/5 overflow-hidden">
                                        <div
                                            className={`h-full transition-all ${BAR_COLORS[opt.value]}`}
                                            style={{ width: `${pct}%` }}
                                        />
                                    </div>
                                    <span className="w-20 shrink-0 text-right text-xs text-white/70">
                                        {count} ({pct}%)
                                    </span>
                                </div>
                            );
                        })}
                    </div>

                    {trend.length > 1 && (
                        <div>
                            <p className="text-xs text-white/40 mb-2">Responses per month</p>
                            <div style={{ width: '100%', height: 180 }}>
                                <ResponsiveContainer width="100%" height="100%">
                                    <AreaChart data={trend}>
                                        <XAxis dataKey="period" stroke="rgba(255,255,255,0.3)" style={{ fontSize: '11px' }} tick={{ fill: 'rgba(255,255,255,0.5)' }} />
                                        <YAxis allowDecimals={false} stroke="rgba(255,255,255,0.3)" style={{ fontSize: '11px' }} tick={{ fill: 'rgba(255,255,255,0.5)' }} />
                                        <Tooltip contentStyle={{ backgroundColor: 'rgba(17,24,39,0.95)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', color: 'white' }} />
                                        <Area type="monotone" dataKey="responses" stroke="#22d3ee" fill="url(#feedbackGradient)" fillOpacity={0.4} strokeWidth={2} />
                                        <defs>
                                            <linearGradient id="feedbackGradient" x1="0" y1="0" x2="0" y2="1">
                                                <stop offset="0%" stopColor="#22d3ee" stopOpacity={0.6} />
                                                <stop offset="100%" stopColor="#22d3ee" stopOpacity={0.05} />
                                            </linearGradient>
                                        </defs>
                                    </AreaChart>
                                </ResponsiveContainer>
                            </div>
                        </div>
                    )}
                </>
            )}
        </div>
    );
};

export default PlatformFeedbackStats;
