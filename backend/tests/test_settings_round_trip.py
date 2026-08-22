"""Anything a town configures has to survive a reload.

The recurring failure in this codebase is a setting that stores fine, reads
back fine, and silently does nothing -- or stores fine and is never read at
all. The retention mode did it for months because two `system_settings` rows
existed and the write and the read picked different ones. It is not a bug you
notice: the form saves, the toast appears, and the value is simply gone the
next time somebody looks, by which point it reads as "I must not have clicked
save".

So every column on `SystemSettings` has to be reachable both ways. Either it
travels through `SystemSettingsBase` -- the schema the general settings
endpoint reads and writes -- or it has a dedicated endpoint that writes it and
one that reads it back, named here.

A column in neither list is unreachable: the UI cannot set it and cannot show
it. That is the state `translations` has been in since it was added.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "app/models.py"
SCHEMAS = ROOT / "app/schemas.py"
API = ROOT / "app/api"


def _columns() -> set:
    block = MODELS.read_text()
    block = block[block.index("class SystemSettings(Base)"):]
    block = block[:block.index("\nclass ")]
    return {c for c in re.findall(r"^    (\w+) = Column", block, re.M)} - {"id"}


def _schema_fields() -> set:
    block = SCHEMAS.read_text()
    block = block[block.index("class SystemSettingsBase"):]
    block = block[:block.index("\nclass ")]
    return set(re.findall(r"^    (\w+):", block, re.M))


# Columns that do not travel through /settings, with the endpoints that do own
# them. Both directions, because a value that can be written and not read is
# the exact shape of the bug this file exists for.
DEDICATED = {
    "custom_domain":           ("POST /system/domain/configure", "GET /system/domain/status"),
    "timezone":                ("POST /system/timezone", "GET /system/timezone"),
    "retention_mode":          ("POST /system/retention/policy", "GET /system/retention/policy"),
    "retention_days":          ("POST /system/retention/policy", "GET /system/retention/policy"),
    "retention_scrub_fields":  ("POST /system/retention/policy", "GET /system/retention/policy"),
    "legal_hold":              ("POST /system/retention/legal-hold", "GET /system/retention/policy"),
    "township_boundary":       ("POST /gis/boundaries", "GET /system/settings (maps config)"),
    # Which integrations the town wants, credentials aside. Not on
    # SystemSettingsBase on purpose: the general settings endpoint takes a whole
    # object and echoes it back, and a browser posting a stale copy of it would
    # switch capabilities on and off as a side effect of renaming the township.
    # The dedicated PUT takes a partial map and touches only what changed.
    "capability_switches":     ("PUT /system/capabilities", "GET /system/providers/status"),
    # Whether anybody has said this town is set up. Its own endpoint because it
    # is written by one deliberate act -- closing the setup guide -- and read on
    # every admin sign-in to decide whether to open it.
    "setup_completed_at":      ("POST /system/setup/state", "GET /system/setup/state"),
}

# Written and read by the platform itself, never by a person. Not settings.
INTERNAL = {
    "ai_models_cache",     # discovery cache, refreshed by the model picker
    "health_alert_state",  # what the alerting layer has already said
    "managed_policy",      # set by the hosting orchestrator, not the town
    # Which credential keys the host supplied. Same reasoning as
    # managed_policy: written only by the orchestrator (POST
    # /provisioning/host-secrets) and by the write choke-point clearing a key a
    # town has taken over, never by anybody filling in a form. It surfaces to
    # the console read-only, as the `host_provided` note on the catalog and
    # secrets responses, so there is nothing here for a settings form to round
    # trip.
    "host_provided_keys",
    "updated_at",          # the ORM writes it
}

# Columns that nothing reaches. Listed so the state is a recorded decision
# rather than an oversight -- and so adding a new one fails this file.
KNOWN_UNREACHABLE = {
    # Declared with a documented format ({"es": {"township_name": ...}}) and
    # written by no code path anywhere in the backend. The per-language
    # overrides it was meant to hold have no way in. Either give it an endpoint
    # or drop the column; leaving it invites somebody to build a UI for a
    # setting that cannot be stored.
    "translations",
}


def test_every_settings_column_is_reachable():
    """The check that matters. A column the UI can neither set nor read is
    either dead weight or a feature somebody believes exists."""
    unclassified = _columns() - _schema_fields() - set(DEDICATED) - INTERNAL - KNOWN_UNREACHABLE
    assert not unclassified, (
        f"new SystemSettings columns nobody has ruled on: {sorted(unclassified)}. "
        f"Add to SystemSettingsBase, or give it a dedicated read *and* write "
        f"endpoint and list it in DEDICATED, or say why it is internal."
    )


def test_the_schema_never_promises_a_column_that_does_not_exist():
    """A field on the schema with no column is accepted by the API, echoed back
    in the response, and dropped -- the most convincing possible version of
    "it saved"."""
    phantom = _schema_fields() - _columns()
    assert not phantom, f"SystemSettingsBase declares {sorted(phantom)} with no column behind it"


# Where a dedicated column's read and write actually live, when the endpoint
# delegates rather than doing it inline.
#
# The scan below reads `app/api` because that is where every one of these was
# implemented when it was written. That is a proxy for the real question --
# something writes this column and something reads it back -- and it fails the
# moment a column is given a service of its own, which is the right place for
# one whose value is consulted from six dispatch paths. Named per column rather
# than by widening the glob: a column written only by a nightly task would still
# be unreachable from the UI, and that is what this file exists to catch.
IMPLEMENTED_IN = {
    "capability_switches": ["app/services/capability_switches.py"],
}


@pytest.mark.parametrize("column,endpoints", sorted(DEDICATED.items()))
def test_a_dedicated_column_can_be_written_and_read(column, endpoints):
    """Both halves. `retention_scrub_fields` was saved by an endpoint that had
    no matching read for a while, so the boxes a clerk ticked came back
    unticked and they ticked them again."""
    sources = list(API.glob("*.py")) + [ROOT / p for p in IMPLEMENTED_IN.get(column, [])]
    api = "\n".join(p.read_text() for p in sources)
    # The same set of variable names on both halves. The read pattern only
    # accepted `settings`, so a column written through `row.` -- which the write
    # pattern has always allowed -- and read back through `getattr(row, ...)`
    # counted as never read.
    holder = r"(?:settings|row|s)"
    written = re.search(rf"{holder}\.{column}\s*=", api)
    read = re.search(rf"getattr\({holder}, [\"']{column}[\"']|{holder}\.{column}\b(?!\s*=)", api)
    assert written, f"{column} is never written ({endpoints[0]} was expected to)"
    assert read, f"{column} is never read back ({endpoints[1]} was expected to)"


def test_the_unreachable_list_has_not_gone_stale():
    """An entry that is now reachable is a comment pretending to be a finding."""
    api = "\n".join(p.read_text() for p in API.glob("*.py"))
    for column in KNOWN_UNREACHABLE:
        assert column in _columns(), f"{column} is no longer a column; drop it from the list"
        assert not re.search(rf"settings\.{column}\s*=", api), (
            f"{column} is written now -- move it out of KNOWN_UNREACHABLE"
        )


def test_settings_are_read_through_the_singleton_helper():
    """`system_settings` has had more than one row in the wild. Any read that
    is not ordered picks whichever row PostgreSQL hands back first, which
    changes after an UPDATE rewrites the tuple -- so a value saved through one
    path was read through another and appeared not to have saved at all.

    Every read is either the shared helper or explicitly ordered.
    """
    api = "\n".join(p.read_text() for p in API.glob("*.py"))
    unordered = re.findall(r"select\(SystemSettings\)(?!\s*\.where)(?![^)]*order_by)[^\n]*\n", api)
    bad = [u for u in unordered if "order_by" not in u and "limit" in u]
    assert not bad, (
        f"unordered singleton reads: {bad}. Use read_settings_row() or "
        f".order_by(SystemSettings.id) -- LIMIT 1 without ORDER BY is not deterministic."
    )


# ---- and the work that has been accepted but not yet run ----

COMPOSE = ROOT.parent / "docker-compose.yml"


@pytest.fixture(scope="module")
def compose() -> str:
    if not COMPOSE.exists():
        pytest.skip("compose file not in this checkout")
    return COMPOSE.read_text()


def _service(compose: str, name: str) -> str:
    block = compose[compose.index(f"\n  {name}:"):]
    end = re.search(r"\n  [a-z_-]+:\n", block[3:])
    return block[:end.start() + 3] if end else block


def test_the_database_survives_a_restart(compose):
    """The obvious one, and it is fine. Here so that removing the volume is a
    test failure rather than a discovery."""
    assert "postgres_data:/var/lib/postgresql/data" in compose


def test_the_queue_survives_a_restart(compose):
    """Redis had no volume. It holds the Celery queue -- every confirmation
    email, AI triage and govtech push that has been accepted and not yet run --
    so `docker compose restart` dropped all of it silently.

    Which matters more now, because setting a custom domain asks an
    administrator to restart Caddy, and the obvious way to do that is to
    restart everything.
    """
    redis = _service(compose, "redis")
    assert "redis_data:/data" in redis, "the queue does not survive a restart"
    assert "--appendonly yes" in redis, "the volume is mounted but nothing is written to it"


def test_the_queue_is_never_evicted_to_free_memory(compose):
    """`allkeys-lru` lets Redis drop *any* key under memory pressure, queue
    entries included, with nothing raised and nothing logged. A busy morning
    could lose a resident's confirmation email and no part of the system would
    know it had happened.

    `noeviction` makes a full Redis refuse the write instead. The application
    already copes: cache writes are wrapped, and enqueue() logs and lets the
    record stand. A logged failure beats a silent loss.
    """
    redis = _service(compose, "redis")
    # The directive, not the word. The first version of this assertion matched
    # the comment above the setting that explains why allkeys-lru is wrong --
    # so it failed on correct configuration. Prose about a behaviour is not the
    # behaviour, and this is the third time in this codebase.
    assert "--maxmemory-policy allkeys-lru" not in redis, "the broker can evict queued tasks again"
    assert "--maxmemory-policy noeviction" in redis


def test_uploaded_photos_survive_a_restart(compose):
    """A resident's photo is evidence. It cannot live in a container layer."""
    assert "uploads_data:" in compose
