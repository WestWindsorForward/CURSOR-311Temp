#!/usr/bin/env python3
"""Render every outbound email to files you can open in a browser.

Email rendering cannot be proved correct by assertion -- the failures that
matter are "Outlook squashed the columns" and "Gmail's dark mode turned the
body grey on charcoal", and neither is visible from a test. So this exists: one
command that writes every message this system can send, with plausible content,
as an .html file and a .txt file, so a human can look.

    python backend/scripts/preview_emails.py --out /tmp/emails
    # then open /tmp/emails/index.html

Or from inside the running backend container:

    docker compose exec backend python scripts/preview_emails.py --out /tmp/emails
    docker compose cp backend:/tmp/emails ./email-preview

Checking dark mode: open a preview in a browser and flip the OS (or DevTools'
"Emulate prefers-color-scheme") to dark. Both schemes must stay readable. The
.txt beside each file is the exact plain-text alternative that ships in the
same message -- if it says less than the HTML, that is a bug.

No database, no network, no credentials: every sample is built from literals
below, so this is safe to run anywhere.
"""

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import email_layout as L  # noqa: E402
from app.services import email_templates as T  # noqa: E402
from app.services.town_time import format_town  # noqa: E402

TOWN = "Maple Ridge Township"
BRAND = "#3f5b9c"
LOGO = None  # set to an https URL to preview the logo slot
PORTAL = "https://311.mapleridge.gov"
STAFF_LINK = f"{PORTAL}/staff#request/SR-2026-0184"


def samples():
    """(slug, human name, message dict) for every email the system sends."""
    out = []

    out.append(("resident-confirmation", "Resident: request received",
                T.build_confirmation_email(
                    township_name=TOWN, logo_url=LOGO, primary_color=BRAND,
                    request_id="SR-2026-0184", service_name="Pothole",
                    description="Deep pothole in the eastbound lane just past the "
                                "bridge; it's been growing for a week & has burst a tire.",
                    address="120 Bridge Street", portal_url=PORTAL)))

    for status, note in (("open", None), ("in_progress", None),
                         ("closed", "Patched with hot mix and compacted.")):
        out.append((f"resident-status-{status}", f"Resident: status -> {status}",
                    T.build_status_update_email(
                        township_name=TOWN, logo_url=LOGO, primary_color=BRAND,
                        request_id="SR-2026-0184", service_name="Pothole",
                        old_status="open", new_status=status,
                        completion_message=note,
                        completion_photo_url="/media/completions/184.jpg" if note else None,
                        portal_url=PORTAL)))

    out.append(("resident-comment", "Resident: staff comment",
                T.build_comment_email(
                    township_name=TOWN, logo_url=LOGO, primary_color=BRAND,
                    request_id="SR-2026-0184", service_name="Pothole",
                    comment_author="D. Alvarez",
                    comment_content="Crew is scheduled for Thursday morning, weather "
                                    "permitting. We'll update this ticket when it's done.",
                    portal_url=PORTAL)))

    out.append(("resident-confirmation-es", "Resident: confirmation (Spanish)",
                T.build_confirmation_email(
                    township_name=TOWN, logo_url=LOGO, primary_color=BRAND,
                    request_id="SR-2026-0185", service_name="Bache",
                    description="Bache grande en la calle.", address="120 Bridge Street",
                    portal_url=PORTAL, language="es")))

    out.append(("staff-new-request", "Staff: new request assigned", L.build_email(
        subject="New request: Pothole", township_name=TOWN, logo_url=LOGO,
        primary_color=BRAND, preheader="SR-2026-0184 - Pothole",
        blocks=[
            L.heading("New request assigned", 2),
            L.fields([("Request ID", "SR-2026-0184"), ("Category", "Pothole"),
                      ("Address", "120 Bridge Street"),
                      ("Description", "Deep pothole in the eastbound lane.")]),
            L.button("View request in the staff dashboard", STAFF_LINK),
        ],
        footer_lines=[f"You're receiving this because you're staff at {TOWN}."])))

    out.append(("staff-department-fallback", "Staff: department routing address",
                L.build_email(
                    subject="New service request: #SR-2026-0184 - Pothole",
                    township_name=TOWN, logo_url=LOGO, primary_color=BRAND,
                    blocks=[
                        L.heading("New service request received", 2),
                        L.fields([("Request ID", "SR-2026-0184"), ("Category", "Pothole"),
                                  ("Description", "Deep pothole in the eastbound lane."),
                                  ("Address", "120 Bridge Street"),
                                  ("Submitted", format_town(datetime.now(timezone.utc),
                                                            "America/New_York"))]),
                        L.button("Open the staff dashboard", f"{PORTAL}/staff"),
                    ],
                    footer_lines=[f"This address is the routing address for a "
                                  f"department at {TOWN}."])))

    out.append(("staff-activity", "Staff: activity on a request", L.build_email(
        subject="Status changed: SR-2026-0184 — Pothole", township_name=TOWN,
        logo_url=LOGO, primary_color=BRAND,
        blocks=[
            L.heading("Status changed", 2),
            L.fields([("Request", "SR-2026-0184 - Pothole"), ("Status", "in_progress"),
                      ("Address", "120 Bridge Street")]),
            L.button("View request in the staff dashboard", STAFF_LINK),
        ],
        footer_lines=[f"You're receiving this because you're staff at {TOWN}."])))

    out.append(("staff-weekly-digest", "Staff: weekly digest", L.build_email(
        subject=f"Weekly digest: 12 open requests - {TOWN} 311", township_name=TOWN,
        logo_url=LOGO, primary_color=BRAND,
        preheader="9 open, 3 in progress, 2 overdue",
        blocks=[
            L.heading("Weekly digest", 2),
            L.stats([("Open", 9), ("In progress", 3), ("Overdue (7+ days)", 2)]),
            L.heading("Oldest open requests", 3),
            L.table(["ID", "Category", "Status", "Age"], [
                ["SR-2026-0102", "Streetlight out", "open", "23d"],
                ["SR-2026-0119", "Illegal dumping", "in progress", "16d"],
                ["SR-2026-0140", "Pothole", "open", "9d"],
            ]),
            L.button("View all requests", f"{PORTAL}/staff"),
        ],
        footer_lines=[f"You're receiving this because you're staff at {TOWN}.",
                      f"Manage notification preferences: {PORTAL}/staff/settings"])))

    out.append(("admin-health-escalation", "Admin: proactive health alert", L.build_email(
        subject=f"[Critical] {TOWN} - 2 system check(s) need attention",
        township_name=TOWN, logo_url=LOGO, primary_color=BRAND,
        blocks=[
            L.heading("Critical: system needs attention", 2),
            L.paragraph("Each check below has crossed its alert threshold. "
                        "The condition observed and the action for it are "
                        "stated on each one."),
            L.callout("Disk is 94% full. Free space or extend the volume.",
                      "critical", title="CRITICAL - Disk space"),
            L.callout("Geocoding quota is 88% consumed with 9 days left in the "
                      "billing period. Raise the cap or reduce lookups.",
                      "warning", title="WARNING - Geocoding quota"),
            L.paragraph("Full detail, and the restart and maintenance actions, are in "
                        "Admin Console under System Health.", muted=True),
        ],
        footer_lines=["Proactive health alert. Sent to administrators only."])))

    out.append(("admin-road-alert", "Admin: road data alert", L.build_email(
        subject=f"A road-routing rule in {TOWN} no longer matches any road",
        township_name=TOWN,
        blocks=[
            L.heading(f"A road-routing rule in {TOWN} no longer matches any road", 2),
            L.callout("Elm Street no longer appears in the road data, so the rule "
                      "that routed it to the county has stopped blocking.",
                      "critical", title="ERROR - Road data"),
            L.paragraph("Reports on Elm Street are now handled by the town, and the "
                        "routing screens show no error. The usual cause is a spelling "
                        "change by the data publisher."),
        ],
        footer_lines=["Road data check. Sent to administrators only."])))

    # The connector digest composes its own text half, so it is previewed
    # through its real entry point rather than rebuilt here.
    try:
        from app.services import connector_alerts as CA

        class _H:
            def __init__(self, connector, status, last_error=None):
                self.connector, self.status, self.last_error = connector, status, last_error
                self.last_success_at = None
                self.consecutive_failures = 4
                self.alert_level = self.alert_sent_at = None
                self.alert_muted_until = self.alert_muted_level = None

            def summary(self):
                return self.status

        now = datetime.now(timezone.utc)
        plan = CA.plan([_H("email", "down", "535 Authentication credentials invalid"),
                        _H("sms", "failing", "429 Too Many Requests")], now=now)
        out.append(("admin-connector-digest", "Admin: connector alert digest",
                    CA.compose(plan, town=TOWN, settings_url=f"{PORTAL}/admin#integrations",
                               now=now)))
    except Exception as exc:  # pragma: no cover - preview convenience only
        print(f"  (skipped connector digest: {exc})")

    out.append(("legacy-confirmation", "Legacy: unbranded confirmation", L.build_email(
        subject="Request #SR-2026-0184 received", township_name="311",
        blocks=[
            L.heading("Your request has been received", 2),
            L.fields([("Request ID", "#SR-2026-0184")]),
            L.paragraph("You can track the status of your request using this ID."),
        ],
        footer_lines=["We appreciate your help in making our community better."])))

    # ---------------------------------------------------------------
    # Two renders that exist to be *checked*, not to be sent.
    # ---------------------------------------------------------------

    # Text expansion. German and Finnish run 30-40% longer than English, and
    # the failure that costs is silent: a button label that overflows the
    # column, a stat label clipped to "Offene Anfrag", a table header that
    # pushes the table past 600px. These strings are 40-60% longer than the
    # English they stand in for, so the previews show whether the layout
    # holds before a translated deployment finds out.
    out.append(("expansion-stress-de", "Diagnostic: German-length strings (+40%)",
                L.build_email(
                    subject="Wochenbericht: 12 offene Serviceanfragen",
                    township_name="Gemeindeverwaltung Maple Ridge",
                    logo_url=LOGO, primary_color=BRAND,
                    tagline="311-Bürgerserviceportal",
                    preheader="9 offen, 3 in Bearbeitung, 2 überfällig",
                    blocks=[
                        L.heading("Wochenübersicht der offenen Serviceanfragen", 2),
                        L.lede("Zwei Anfragen sind seit mehr als sieben Tagen "
                               "unbearbeitet geblieben."),
                        L.status_panel("Aktueller Bearbeitungsstand",
                                       "In Bearbeitung durch das Straßenbauamt", "info"),
                        L.stats([("Offene Anfragen", 9),
                                 ("In Bearbeitung befindlich", 3),
                                 ("Überfällig (7+ Tage)", 2),
                                 ("Diese Woche abgeschlossen", 14)]),
                        L.fields([
                            ("Anfragenummer", "SR-2026-0184"),
                            ("Zuständigkeitsbereich", "Straßenunterhaltung und Winterdienst"),
                            ("Straßenbezeichnung", "Brückenstraße 120, Gemeinde Maple Ridge"),
                        ]),
                        L.table(["Anfragenummer", "Kategorie", "Bearbeitungsstand", "Alter"], [
                            ["SR-2026-0102", "Straßenbeleuchtung ausgefallen", "offen", "23 T"],
                            ["SR-2026-0119", "Unerlaubte Abfallentsorgung", "in Bearbeitung", "16 T"],
                        ]),
                        L.callout("Die Straßendaten konnten seit vier aufeinanderfolgenden "
                                  "Versuchen nicht aktualisiert werden.", "warning",
                                  title="Warnung - Straßendatenaktualisierung"),
                        L.button("Alle offenen Serviceanfragen im Mitarbeiterbereich anzeigen",
                                 f"{PORTAL}/staff"),
                        L.link("Benachrichtigungseinstellungen verwalten",
                               f"{PORTAL}/staff/settings"),
                    ],
                    footer_lines=["Sie erhalten diese Nachricht, weil Sie Mitarbeiterin "
                                  "oder Mitarbeiter der Gemeindeverwaltung sind."],
                    no_reply_note="Bitte antworten Sie nicht direkt auf diese E-Mail.")))

    # Right-to-left. The app offers Arabic in its language picker, so this is a
    # real path: alignment, list indents and the callout keyline all have to
    # mirror, not just the `dir` attribute.
    out.append(("resident-confirmation-ar", "Diagnostic: Arabic (right-to-left)",
                T.build_confirmation_email(
                    township_name=TOWN, logo_url=LOGO, primary_color=BRAND,
                    request_id="SR-2026-0186", service_name="حفرة في الطريق",
                    description="حفرة كبيرة في المسار الشرقي بعد الجسر مباشرة.",
                    address="١٢٠ شارع الجسر", portal_url=PORTAL, language="ar")))

    return out


INDEX_CSS = """
body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 2rem auto;
       max-width: 46rem; line-height: 1.6; }
li { margin-bottom: .4rem; }
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="/tmp/email-preview", help="output directory")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    rows = []
    for slug, name, message in samples():
        html_path = os.path.join(args.out, f"{slug}.html")
        text_path = os.path.join(args.out, f"{slug}.txt")
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(message["html"])
        with open(text_path, "w", encoding="utf-8") as fh:
            fh.write(message["text"])
        rows.append(f'<li><a href="{slug}.html">{name}</a> '
                    f'&mdash; <code>{message["subject"]}</code> '
                    f'(<a href="{slug}.txt">text part</a>)</li>')
        print(f"  {slug}")

    with open(os.path.join(args.out, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(f"<!doctype html><meta charset=utf-8><title>Email previews</title>"
                 f"<style>{INDEX_CSS}</style>"
                 f"<h1>Outbound email previews</h1>"
                 f"<p>Flip your OS or DevTools to dark mode and re-check every one of "
                 f"these. Each HTML file is paired with the exact plain-text "
                 f"alternative that ships in the same message.</p>"
                 f"<ul>{''.join(rows)}</ul>")
    print(f"\nWrote {len(rows)} emails to {args.out}. Open {args.out}/index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
