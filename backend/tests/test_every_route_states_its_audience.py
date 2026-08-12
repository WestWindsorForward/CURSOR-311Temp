"""Every route says who may call it, and the ones that change things say "staff".

This exists because a decorator came adrift. `@router.post("/boundaries")` was
sitting on `persist_boundary`, an internal helper two functions above the
handler it belonged to -- so the endpoint that overwrites a town's boundary was
served by a function with no `Depends` on it, and the admin-guarded handler
underneath had no route at all and had silently never existed.

Nothing catches that by reading. The decorator is syntactically fine, the
module imports, the app starts, and the only symptom is an endpoint quietly
answering to the whole internet.

So the audience of every route is asserted here. Writes are the strict half:
anything that mutates state has to name a role, and the unauthenticated ones
are listed individually with a reason.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "backend/app/api"

WRITE_METHODS = {"post", "put", "patch", "delete"}


def _routes():
    """(file, method, path, function, role) for every decorated route."""
    for path in sorted(API.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                    continue
                if dec.func.attr not in WRITE_METHODS | {"get"}:
                    continue
                route = dec.args[0].value if dec.args and isinstance(dec.args[0], ast.Constant) else "?"
                defaults = ast.dump(ast.Module(
                    body=[ast.Expr(d) for d in node.args.defaults if d], type_ignores=[]))
                role = next((r for r, marker in (
                    ("admin", "get_current_admin"),
                    ("staff", "get_current_staff"),
                    ("researcher", "get_current_researcher"),
                    ("user", "get_current_user"),
                ) if marker in defaults), None)
                yield path.name, dec.func.attr, route, node.name, role


# Unauthenticated writes, each with the reason it is allowed to be one. Anything
# not on this list and not behind a role is a failure.
#
# Every entry here either verifies its own credential in the body of the
# handler (a signed token, a webhook secret, a bootstrap password) or is a
# deliberate public action.
PUBLIC_WRITES = {
    # Resident-facing, by design.
    ("open311.py", "post", "/requests.json"),                        # file a report
    ("open311.py", "post", "/public/requests/{request_id}/comments"),  # comment on your own report
    # Screens a photo when the resident picks it, so the Vision round trip is
    # not sitting on the Submit button. Unauthenticated for the same reason
    # /requests.json is: a resident has no account. It writes a row and spends
    # money, so it is limited harder than filing a report (12/min against 10),
    # size-capped before the bytes are read, stores only the REDACTED image, and
    # the row is reaped within the hour whether or not anyone claims it.
    ("open311.py", "post", "/photos/screen"),
    ("system.py", "post", "/disclaimer/acknowledge"),                # records a click
    ("system.py", "post", "/translate/batch"),                       # renders the page
    ("roads.py", "post", "/road-check"),                             # is this street closed
    # First-run and machine paths. Each checks a secret inside the handler,
    # which is why no Depends appears in the signature.
    ("auth.py", "post", "/bootstrap"),                               # bootstrap password
    ("auth.py", "post", "/bootstrap/verify"),                        # bootstrap token
    ("auth.py", "post", "/onboarding/redeem"),                       # single-use signed link
    ("integrations.py", "post", "/webhook/{platform}/{token}"),      # per-integration secret
    ("provisioning.py", "post", "/bootstrap"),                       # provisioning token
    ("provisioning.py", "post", "/lifecycle"),                       # provisioning token
    ("provisioning.py", "post", "/break-glass"),                     # signed break-glass token
    ("provisioning.py", "post", "/managed-settings"),                # provisioning token
    ("provisioning.py", "post", "/host-secrets"),                    # provisioning token
}


def test_nothing_writes_without_saying_who_may():
    unguarded = {
        (f, method, route)
        for f, method, route, _fn, role in _routes()
        if method in WRITE_METHODS and role is None
    }
    surprises = unguarded - PUBLIC_WRITES
    assert not surprises, (
        f"unauthenticated write endpoints: {sorted(surprises)}. If one of these "
        f"is deliberate, add it to PUBLIC_WRITES with the reason it is safe. "
        f"This test exists because a route decorator drifted onto a helper and "
        f"exposed the boundary writer to anyone."
    )


def test_the_public_write_list_has_not_gone_stale():
    """An entry that no longer matches a real route is a comment pretending to
    be a check, and the next person reads it as coverage."""
    actual = {(f, m, r) for f, m, r, _fn, role in _routes() if m in WRITE_METHODS and role is None}
    stale = PUBLIC_WRITES - actual
    assert not stale, f"PUBLIC_WRITES lists routes that are no longer unauthenticated: {sorted(stale)}"


def test_a_boundary_can_only_be_replaced_by_an_administrator():
    """The specific regression. A town's boundary decides which roads are
    fetched, how reports match to streets, and what the map draws."""
    writers = [
        (fn, role) for f, m, r, fn, role in _routes()
        if f == "gis.py" and m == "post" and "boundar" in r.lower()
    ]
    assert writers, "no boundary write endpoints found at all"
    for fn, role in writers:
        assert role == "admin", f"gis.{fn} writes a boundary as role={role}"


def test_the_boundary_helper_is_not_itself_a_route():
    """`persist_boundary` takes a raw session and no Depends. It is a helper,
    and it was serving POST /gis/boundaries."""
    for _f, method, route, fn, _role in _routes():
        assert fn != "persist_boundary", (
            f"persist_boundary is decorated as {method.upper()} {route}; it is an "
            f"internal helper with no authentication"
        )


def test_legal_hold_is_administrators_only():
    """Raised, not ignored. Silently dropping the field would leave the caller
    believing a hold was placed on a record it was not placed on -- which is
    the failure mode that matters for something whose whole purpose is to
    survive a records request."""
    source = (API / "open311.py").read_text()
    assert 'if "flagged" in update_dict and current_user.role != "admin":' in source
    guard = source[source.index('if "flagged" in update_dict and current_user.role != "admin":'):]
    assert "raise HTTPException(status_code=403" in guard[:250], (
        "legal hold must refuse a non-admin rather than ignore the field"
    )


@pytest.mark.parametrize("path,fn", [
    ("open311.py", "delete_request"),
    ("open311.py", "update_request_status"),
])
def test_the_destructive_request_endpoints_stay_behind_a_role(path, fn):
    roles = [role for f, _m, _r, name, role in _routes() if f == path and name == fn]
    assert roles, f"{path}:{fn} is not a route any more"
    for role in roles:
        assert role in ("staff", "admin"), f"{fn} is reachable as role={role}"
