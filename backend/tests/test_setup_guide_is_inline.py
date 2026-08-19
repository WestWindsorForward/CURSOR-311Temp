"""The setup guide is where setup happens, not a signpost to somewhere else.

This file exists because of a specific wrong turn. Asked to put the credential
boxes inline in the guide, I instead built the "step owns its fields" layout on
the provider cards and left the guide saying "scroll to the Maps card below and
choose your provider" -- and then wrote a test asserting the guide did NOT carry
the steps, which locked the mistake in. A test can encode a misreading as firmly
as it encodes a requirement.

The requirement: a clerk works through this top to bottom without leaving it.

What is checked where. The grouping arithmetic -- which capabilities share a
login, what order tasks come in, whether every item has somewhere to type -- is
plain TypeScript in `setupPlan.ts` and is tested directly in
`setupPlan.test.ts`, where the assertions can be about behaviour rather than
about the text of a file. What is left here is the handful of invariants that
really are properties of the source: that no handoff sentence has come back,
that the wizard is what the page mounts, and that nothing anywhere promises a
price or a duration.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend/src/components"
GUIDE = FRONTEND / "SetupIntegrationsPage.tsx"
WIZARD = FRONTEND / "SetupWizard.tsx"
PLAN = FRONTEND / "setupPlan.ts"
INLINE = FRONTEND / "InlineProviderSetup.tsx"
SHARED = FRONTEND / "ProviderCredentialSteps.tsx"
CARDS = FRONTEND / "ServiceProviders.tsx"
CONTENT = FRONTEND / "setupStepsContent.tsx"
SHARED_UI = FRONTEND / "capabilityUI.tsx"

SETUP_SURFACE = (GUIDE, WIZARD, PLAN, INLINE, SHARED, CONTENT)


def _read(path: Path) -> str:
    if not path.exists():
        pytest.skip("frontend not present in this checkout")
    return path.read_text()


@pytest.fixture(scope="module")
def guide() -> str:
    return _read(GUIDE)


# ---------------------------------------------------------------------------
# No handoffs
# ---------------------------------------------------------------------------

HANDOFF_PHRASES = (
    "Scroll to the",
    "card below and choose your",
    "card below, and press",
    "Enter the bucket and keys in the",
)


def test_no_section_defers_to_a_card():
    """A guide that says "go and do this somewhere else" is a table of contents.

    Someone reading step 2 of sign-in was sent three thousand pixels down the
    page, to a card that then asked them again which provider they wanted -- a
    question they had answered at the top of the very same panel.
    """
    offenders = []
    for path in (GUIDE, WIZARD, PLAN):
        text = _read(path)
        offenders += [f"{path.name}: {p}" for p in HANDOFF_PHRASES if p in text]
    assert not offenders, (
        f"the setup guide has gone back to pointing at the cards: {offenders}"
    )


def test_the_page_mounts_the_wizard(guide):
    assert "<SetupWizard" in guide
    # And hands it everything it needs to do the work in place rather than
    # describing it. Any of these missing is a silently half-wired panel.
    for prop in ("status=", "isDone=", "onRefresh=", "publicOrigin=", "renderFoundation="):
        assert prop in guide, f"the wizard is mounted without {prop}"


def test_the_wizard_sets_providers_up_in_place():
    wizard = _read(WIZARD)
    assert "InlineProviderSetup" in wizard, "the wizard renders no credential boxes"
    assert "PlainSecrets" in wizard, "settings with no provider card have nowhere to go"


def test_inline_setup_saves_and_then_verifies():
    """A save that is not verified is the silent failure this page exists to
    avoid, so the live test is part of saving rather than a second button."""
    inline = _read(INLINE)
    assert "api.saveProvider" in inline
    assert "api.testProvider" in inline


def test_the_wizard_only_advances_on_a_passing_test():
    """Being moved along past a credential that does not work is worse than not
    advancing at all: it reads as confirmation."""
    wizard = _read(WIZARD)
    assert "onSaved={onRefresh}" not in wizard, "advancing on save rather than on a passing test"
    assert "if (verified) advanceItem" in wizard, "nothing gates the advance on the test result"
    assert "advanceFrom" in wizard


# ---------------------------------------------------------------------------
# One copy of the walk, rendered in both places
# ---------------------------------------------------------------------------

def test_the_guide_and_the_cards_render_the_same_component():
    """Inline in two places is right. Written in two places is what drifted.

    Last time this page carried two hand-written copies, the guide told towns
    Okta's issuer was their org URL while the card told them the opposite.
    """
    for path in (CARDS, INLINE):
        assert "ProviderCredentialSteps" in _read(path), (
            f"{path.name} renders setup steps without the shared component"
        )
    assert "stepsFor" in _read(SHARED)


# ---------------------------------------------------------------------------
# The questionnaire drives the page
# ---------------------------------------------------------------------------

def test_the_cloud_answer_picks_the_provider_rather_than_asking_again(guide):
    for derived in ("aiProvider", "emailProvider", "smsProvider", "redactionProvider"):
        assert f"const {derived} =" in guide, f"{derived} is not derived from the questionnaire"


def test_changing_the_cloud_moves_the_email_and_sms_defaults(guide):
    """Held as an override, not a value.

    Storing the resolved provider would freeze it: a town that picked Google,
    then switched to Azure at the top, would still be looking at SMTP with
    nothing saying why.
    """
    assert "emailOverride ?? EMAIL_BY_CLOUD[setupCloud]" in guide
    assert "smsOverride ?? SMS_BY_CLOUD[setupCloud]" in guide
    assert "redactionOverride ?? setupCloud" in guide


def test_every_feature_gates_something_in_the_plan(guide):
    """A chip that changes nothing when clicked.

    The feature ids and their chip labels used to be two separate lists, and
    both failure directions had already happened: `errors` was in one and not
    the other, so crash reporting could not be switched off. They are one list
    now, so the only thing left to check is that ticking each one actually
    reaches the plan.
    """
    features = set(re.findall(r"\['([a-z]+)', '[^']+'\]",
                              re.search(r"const FEATURES = \[(.*?)\] as const;", guide, re.S).group(1)))
    assert features, "could not find the feature list"
    assert "ALL_FEATURES: readonly string[] = FEATURES.map" in guide, (
        "the feature set is hand-written again rather than derived from the chips"
    )
    plan = _read(PLAN)
    unused = [f for f in features if f"want('{f}')" not in plan]
    assert not unused, f"ticking these changes nothing in the plan: {sorted(unused)}"


def test_provider_choices_are_all_made_in_the_questionnaire(guide):
    """Every provider decision happens once, at the top.

    Email, text and screening used to be decided on pickers nested inside their
    own sections further down, so the questionnaire could say one thing and a
    section another, and there was nowhere to see what the town had chosen.
    """
    plan = _read(PLAN)
    assert "choices?" not in plan, "a task is offering its own provider picker again"
    assert "choiceKey" not in plan
    for question in ("Who sends your email?", "Who sends your text messages?",
                     "Where should photos be checked and blurred?"):
        assert question in guide, f"the questionnaire never asks: {question}"


def test_the_town_systems_connector_is_not_repeated_in_the_guide(guide):
    """It has its own section, with its own wizard, further down the page. A
    stub in the guide duplicated the heading and explained nothing."""
    plan = _read(PLAN)
    assert "govtech" not in plan, "the guide has grown a town-systems task again"
    assert "'govtech'" not in guide


def test_the_completion_message_means_completion(guide):
    """It rendered whenever nothing was open -- which includes collapsing a row,
    so clicking the open task shut congratulated a town that had configured
    nothing at all."""
    wizard = _read(WIZARD)
    assert "allDone" in wizard, "nothing distinguishes 'finished' from 'nothing selected'"
    assert "{!open && (allDone ? (" in wizard, "the done panel is not gated on being done"
    # And it sits outside the AnimatePresence, so the fallback appears at once
    # rather than after an exit animation -- which is also what makes the bug
    # visible to a test rather than hidden behind frames jsdom never produces.
    assert wizard.index("{!open && (allDone") > wizard.index("</AnimatePresence>")


def test_done_means_done_for_the_provider_actually_chosen(guide):
    """Read per capability, "maps is set up" was true if any map provider had a
    key -- so switching from Google to Esri kept a green tick against a provider
    with no credentials, and the guide skipped it."""
    wizard = _read(WIZARD)
    assert "status?.[item.cap]?.configured?.[item.provider]" in wizard, (
        "done-ness is not being read per provider"
    )
    system = (ROOT / "backend/app/api/system.py").read_text()
    assert "async def get_provider_status" in system
    assert '"/providers/status"' in system


def test_switching_provider_reopens_the_task(guide):
    """Leaving it collapsed with a tick is how a town goes live on a provider it
    never configured."""
    wizard = _read(WIZARD)
    assert "props.redactionProvider]" in wizard, "nothing watches for a provider change"


def test_screening_and_blurring_are_one_thing(guide):
    """They were two panels about what is safe to publish, three sections apart,
    and the second was hidden by the first one's tick."""
    assert "'safety'" in guide, "no combined screening-and-blurring feature"
    assert "'moderation'" not in guide, "the old separate moderation tick is back"
    assert "safety: 'redaction'" in guide


# ---------------------------------------------------------------------------
# Callback URLs point at the real site
# ---------------------------------------------------------------------------

def test_the_cards_and_the_guide_share_their_parts_without_sharing_a_layout():
    """Two surfaces, one vocabulary.

    They are deliberately not the same shape -- the guide is a numbered walk
    through setup, the cards are "is anything wrong" -- but the tile, the status
    pill and the buttons must come from one file. Hand-rolled from utility
    classes in each, they drift: that is exactly how the department cards and
    the service-category cards ended up looking like two different products.
    """
    ui = _read(SHARED_UI)
    for export in ("export function StatusPill", "export function CapabilityTile",
                   "export function Action"):
        assert export in ui, f"the shared vocabulary is missing {export}"
    cards = _read(CARDS)
    for part in ("StatusPill", "CapabilityTile", "Action"):
        assert part in cards, f"the provider cards roll their own {part} again"
    assert "from './capabilityUI'" in cards


def test_providers_are_chosen_in_one_place_only():
    """The questionnaire decides. A picker on the card as well meant the two
    could disagree, with nowhere to see which the town had actually meant."""
    cards = _read(CARDS)
    assert 'aria-label={`${title} provider`}' not in cards, (
        "the provider cards have grown their own provider picker again"
    )


def test_the_cards_do_not_run_a_second_setup_walk():
    """This section used to carry its own "Step 3 of 8" cursor and progress
    bar. With the guide above doing the same job, a town had two progress
    indicators that counted differently."""
    # Comment lines are excluded: the note explaining why the guided walk was
    # removed necessarily quotes the copy it removed.
    live = [line for line in _read(CARDS).splitlines()
            if not line.lstrip().startswith(("*", "//", "/*"))]
    cards = "\n".join(live)
    for gone in ("Skip the guide", "Walk me through what is left", "One at a time, in order"):
        assert gone not in cards, f"the cards are running their own guided setup again: {gone!r}"


def test_only_a_real_problem_takes_the_whole_width():
    """Spotlight puts anything wrong on a full-width card and shrinks the rest
    to bubbles. An earlier version also spotlit "not set up", which on a town
    mid-setup produced six full-width cards -- the wall this layout exists to
    avoid, shouting about work the guide above is already walking through."""
    cards = _read(CARDS)
    assert "s === 'failing' || s === 'unchecked'" in cards
    assert "s === 'unset'" not in cards, (
        "switched-off capabilities are being spotlit as though they were faults"
    )


def test_a_save_on_one_surface_reaches_the_other():
    """The guide and the cards are two views of one set of credentials on one
    screen. A key entered in the guide leaving the card below reading "Not set
    up" reads, to the person who typed it, as a save that did not take."""
    guide_text = _read(GUIDE)
    assert "refreshToken={providerRefresh}" in guide_text, (
        "a save in the guide never reaches the cards below"
    )
    assert "onChanged=" in guide_text, "a save on the cards never reaches the guide above"
    cards = _read(CARDS)
    assert "refreshToken > 0" in cards, "the cards ignore the refresh signal"


def test_callback_urls_use_the_configured_domain(guide):
    """`window.location.origin` is wherever the admin happens to be -- an
    internal hostname, a port-forward, an IP. A redirect URI registered from one
    of those can never be redirected to, and the login then fails after the
    password is accepted, which reads as a wrong secret rather than a wrong URL.
    """
    assert "public_origin" in guide, "the page never asks the server for its real address"
    for path in (INLINE, CARDS):
        assert "publicOrigin || window.location.origin" in _read(path), (
            f"{path.name} still builds callback URLs from the browser's address"
        )


def test_the_backend_serves_the_real_origin():
    system = (ROOT / "backend/app/api/system.py").read_text()
    assert "async def public_origin" in system
    assert '"public_origin"' in system


# ---------------------------------------------------------------------------
# No promises about price, speed or difficulty
# ---------------------------------------------------------------------------

# A town's procurement officer reads this page too. A free tier can change
# without notice, and telling a clerk something takes ten minutes is a way of
# making them feel slow when it takes forty.
CLAIMS = re.compile(
    r"\b(free tier|for free|no cost|costs? (?:only|about|around)|"
    r"\$\d|per month|a few dollars|cents? per|a cent per|"
    r"about \d+ minutes?|takes \d+|in (?:under )?\d+ minutes?|"
    r"quickest|fastest|easiest|simplest|it is easy|very easy)\b",
    re.I,
)


def test_nothing_promises_a_price_a_duration_or_that_it_is_easy():
    offenders = []
    for path in SETUP_SURFACE:
        for n, line in enumerate(_read(path).splitlines(), 1):
            if line.lstrip().startswith(("*", "//", "/*")):
                continue  # commentary to maintainers, not copy shown to a clerk
            for hit in CLAIMS.findall(line):
                offenders.append(f"{path.name}:{n} {hit!r}")
    assert not offenders, "claims about cost, speed or difficulty:\n" + "\n".join(offenders)


def test_the_guide_no_longer_carries_time_and_cost_labels(guide):
    """Every section used to be introduced by an estimate and a price."""
    assert "cost=" not in guide
    assert "time=" not in guide


def test_esri_is_offered_rather_than_pushed():
    """The maps step led with "ask your GIS department before you buy anything"
    and told towns to pick Esri. It is one of four reasonable options."""
    guide_text = _read(GUIDE)
    assert "before you buy anything" not in guide_text
    assert "choose Esri above" not in guide_text


# ---------------------------------------------------------------------------
# The wall
# ---------------------------------------------------------------------------

def test_only_one_item_is_open_inside_a_task():
    """Grouping by login turned four visits into one and then put all four on
    the screen at once. The Azure task rendered about six thousand pixels tall
    while the rail said "1 left" -- the same wall, moved.

    Items collapse for the same reason tasks do, and advance on the same rule:
    a save whose live test came back green.
    """
    wizard = _read(WIZARD)
    assert "openItemId" in wizard, "every item in a task is expanded at once"
    assert "expanded={item.id === openItemId}" in wizard
    assert "advanceItem" in wizard, "finishing an item does not open the next"
    assert "if (verified) advanceItem" in wizard, (
        "items advance on save rather than on a passing test"
    )


def test_no_console_walk_runs_away_with_itself():
    """A cap on how long any one provider's instructions can get.

    Not arbitrary: the three key-management walks were 456, 465 and 559 words,
    and two of the Azure steps restated what step one had already said. Prose at
    that length stops being instructions and becomes something to skim, which is
    how the warnings inside it get missed.

    Budgeted per step rather than per walk, which is what the original 400 was
    reaching for. A flat cap punishes a walk for being *complete*: the Azure key
    vault walk grew from six steps to eight because two screens were missing
    from it entirely -- the Access configuration tab, where the permission model
    is chosen, and the role a vault's creator has to give themselves before the
    key screen will work for them at all. Leaving those out kept the count down
    and stranded the reader, which is the opposite of what this test wants. What
    it is really policing is a step that rambles, so the budget follows the
    steps: 80 words each, with a floor of 400 so short walks keep the original
    limit and cannot pad their way up by splitting a step in two.
    """
    import re

    source = _read(CONTENT)
    offenders = []
    for m in re.finditer(r"defineSteps\(\s*'([a-z]+)'\s*,\s*'([a-z0-9]+)'", source):
        end = source.index("\n]);", m.start())
        block = source[m.start():end]
        words = len(re.sub(r"<[^>]+>", " ", block).split())
        steps = max(1, len(re.findall(r"^\s{4}\{", block, re.M)))
        budget = max(400, 80 * steps)
        if words > budget:
            offenders.append(
                f"{m.group(1)}:{m.group(2)} is {words} words across {steps} steps (budget {budget})"
            )
    assert not offenders, "console walks have grown back:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# A warning that is everywhere is not a warning
# ---------------------------------------------------------------------------

def test_warnings_are_rare_enough_to_read():
    """There was one on every other step, and on all three steps of the Google
    Maps walk. The billing warning -- the one that silently produces a grey map
    on a key that looks correct -- sat in identical amber beside "changes can
    take up to five minutes". A page that flags everything flags nothing.

    The bar is now: amber is for a failure that is silent, one that cannot be
    undone, or one that lands on the town's money or resident data. Everything
    else is a grey note.
    """
    source = _read(CONTENT)
    offenders = []
    for m in re.finditer(r"defineSteps\(\s*'([a-z]+)'\s*,\s*'([a-z0-9]+)'", source):
        end = source.index("\n]);", m.start())
        block = source[m.start():end]
        warnings = len(re.findall(r"^\s+trouble:", block, re.M))
        steps = max(1, len(re.findall(r"^\s{4}\{", block, re.M)))
        # Half, rounded up. A three-step walk may carry two if both earn it --
        # Google Maps does: one is the billing step that silently yields a grey
        # map, the other is an unrestricted key somebody can run a bill up on.
        if warnings > -(-steps // 2):
            offenders.append(f"{m.group(1)}:{m.group(2)} has {warnings} across {steps} steps")
    assert not offenders, "warnings are back to competing with each other:\n" + "\n".join(offenders)


def test_the_quiet_kind_of_aside_exists_and_is_used():
    """The demotion has to go somewhere. If `note` disappeared, the 28 asides
    that stopped being warnings were deleted rather than quietened -- and some
    of them are the difference between finishing and not."""
    source = _read(CONTENT)
    assert len(re.findall(r"^\s+note:", source, re.M)) >= 20
    assert "note?: ReactNode" in _read(FRONTEND / "setupSteps.tsx")


def test_a_note_does_not_look_like_a_warning():
    """Same weight and colour is what made the warnings stop registering."""
    renderer = _read(SHARED)
    assert "st.note" in renderer, "notes are defined but never rendered"
    note_block = renderer[renderer.index("st.note &&"):]
    note_block = note_block[:note_block.index("</p>")]
    assert "amber" not in note_block
    assert "AlertCircle" not in note_block


def test_the_silent_failures_kept_their_warning():
    """The specific ones worth keeping. Each is a case where a clerk would
    otherwise believe it had worked."""
    source = _read(CONTENT)
    for phrase in (
        "Google will issue a key without it",       # billing: key looks right, map grey
        "quietly encrypted with",                    # KMS: wrong key, no error
        "Entra shows the Value once",                # no second chance
    ):
        assert phrase in source, f"a silent-failure warning was demoted: {phrase}"
        i = source.index(phrase)
        assert "trouble:" in source[max(0, i - 400):i], f"{phrase} is no longer a warning"
