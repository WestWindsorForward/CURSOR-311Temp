"""What to tell an admin after a road-data refresh, and what to stay quiet about.

Most refreshes are uneventful and should produce silence. A monthly job that
emails "nothing changed" twelve times a year teaches people to filter it, and
then the one message that mattered gets filtered too.

So there are exactly two reasons to write to somebody:

  * something needs a decision -- new streets appeared and nobody has said which
    jurisdiction they belong to;
  * something is broken -- the refresh keeps failing, the source shrank
    implausibly, or a road an active rule depends on has vanished from the data.

That last one is the important one. A rule whose road disappears stops blocking
silently. Nothing errors, nothing looks wrong, and a county road quietly becomes
the town's problem until somebody notices months later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set

from app.services.road_matching import normalize_road_name, road_matches

# Three failures is roughly three months of a monthly schedule. One is noise --
# a service restarting, a transient 500 -- and waiting past three means data
# stale enough to matter.
FAILURE_ALERT_THRESHOLD = 3

# Below this fraction of the previous count the fetch is treated as truncated
# rather than as the town genuinely losing roads.
SHRINK_ALERT_RATIO = 0.7

# A refresh that adds more roads than this is reported as a bulk change rather
# than listing every street, which nobody reads.
MAX_LISTED_ROADS = 25


@dataclass
class RoadChanges:
    """What a refresh did to the set of road names in a town."""

    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    previous_count: int = 0
    current_count: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.added and not self.removed


def diff_road_names(before: Iterable[Optional[str]], after: Iterable[Optional[str]]) -> RoadChanges:
    """Which road names appeared and disappeared.

    Compares on the normalized name, so a publisher switching "CRANBURY RD" to
    "Cranbury Road" is not reported as one road vanishing and another arriving.
    Unnamed segments are ignored entirely -- they are numerous, they churn, and
    a clerk can do nothing with them.
    """
    def index(names: Iterable[Optional[str]]) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for name in names:
            if not name:
                continue
            key = normalize_road_name(name)
            if key:
                result.setdefault(key, name)
        return result

    old, new = index(before), index(after)
    return RoadChanges(
        added=sorted(new[k] for k in new.keys() - old.keys()),
        removed=sorted(old[k] for k in old.keys() - new.keys()),
        previous_count=len(old),
        current_count=len(new),
    )


def configured_roads(routing_configs: Sequence[dict]) -> Set[str]:
    """Every road name any jurisdiction rule depends on, across all services."""
    from app.services.road_matching import _as_list

    roads: Set[str] = set()
    for config in routing_configs or []:
        if not isinstance(config, dict):
            continue
        for jurisdiction in config.get("jurisdictions") or []:
            if isinstance(jurisdiction, dict):
                roads.update(_as_list(jurisdiction.get("roads")))
        roads.update(_as_list(config.get("exclusion_list")))
        roads.update(_as_list(config.get("municipal_roads")))
        roads.update(_as_list(config.get("inclusion_list")))
    return {r for r in roads if r.strip()}


def broken_rules(configured: Iterable[str], available: Iterable[Optional[str]]) -> List[str]:
    """Configured roads that no longer match anything in the data.

    These are the silent failures. The rule still exists, the admin console
    still shows it, and it can never fire again.
    """
    names = [n for n in available if n]
    return sorted(
        road for road in configured
        if names and not any(road_matches(road, name) for name in names)
    )


@dataclass
class Alert:
    severity: str          # "error" | "warning" | "info"
    subject: str
    body: str


def build_alerts(
    *,
    changes: Optional[RoadChanges],
    consecutive_failures: int,
    last_error: Optional[str],
    newly_broken_rules: Sequence[str],
    township: str = "your town",
) -> List[Alert]:
    """Everything worth sending after one refresh. Empty means say nothing.

    Order matters: a broken rule is a live routing defect, a failing refresh is
    data going stale, and new roads are a decision that can wait a month.
    """
    alerts: List[Alert] = []

    if newly_broken_rules:
        listed = ", ".join(newly_broken_rules[:MAX_LISTED_ROADS])
        alerts.append(Alert(
            severity="error",
            subject=f"A road-routing rule in {township} no longer matches any road",
            body=(
                f"These roads are used by jurisdiction rules but are no longer in the "
                f"road data: {listed}.\n\n"
                "Reports on them are now handled by the town instead of being "
                "redirected, and the routing screens show no error. The usual cause is "
                "a spelling change by the data publisher. Check the road names against "
                "Service Categories -> Routing."
            ),
        ))

    if consecutive_failures >= FAILURE_ALERT_THRESHOLD:
        alerts.append(Alert(
            severity="error",
            subject=f"Road data for {township} has failed to update {consecutive_failures} times",
            body=(
                f"The last error was: {last_error or 'unknown'}.\n\n"
                "Routing still works, because the last successful copy of the road "
                "data is still in use. That copy is not being refreshed, so streets "
                "added or renamed since then are absent. Check that the source URL in "
                "the road-data settings still resolves."
            ),
        ))

    if changes and not changes.is_empty:
        # A collapse is reported as a problem, not as a list of removals: the
        # refresh has almost certainly returned a truncated response.
        shrank = (
            changes.previous_count
            and changes.current_count < changes.previous_count * SHRINK_ALERT_RATIO
        )
        if shrank:
            alerts.append(Alert(
                severity="warning",
                subject=f"Road data for {township} shrank sharply and was not applied",
                body=(
                    f"The update returned {changes.current_count} named roads against "
                    f"{changes.previous_count} before. That is usually a truncated response "
                    "rather than roads genuinely disappearing, so the previous data was kept."
                ),
            ))
        else:
            parts = []
            if changes.added:
                parts.append(_describe("New road", changes.added))
            if changes.removed:
                parts.append(_describe("Removed road", changes.removed))
            alerts.append(Alert(
                severity="info",
                subject=f"Road data for {township} was updated",
                body=(
                    "\n\n".join(parts)
                    + "\n\nNew roads are handled by the town unless you assign them to "
                    "another agency in Service Categories -> Routing. No action is needed "
                    "if that is correct."
                ),
            ))

    return alerts


def _describe(label: str, roads: Sequence[str]) -> str:
    plural = "" if len(roads) == 1 else "s"
    if len(roads) > MAX_LISTED_ROADS:
        return f"{label}{plural}: {len(roads)} (too many to list)"
    return f"{label}{plural} ({len(roads)}): " + ", ".join(roads)
