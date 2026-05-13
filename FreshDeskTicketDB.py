#!/usr/bin/env python3
"""
FreshDeskTicketDB.py — Download and archive complete Freshdesk tickets.

Saves each ticket as a readable .txt file under the Tickets/ folder.
Files are named [TicketID]-[YYYY-MM-DD].txt and contain the full
unobfuscated ticket info: customer names, emails, companies, and the
complete conversation thread.

Use FreshDeskTicketSearch.py to search the archive.

Dependencies:
    pip install requests

Usage:
    python FreshDeskTicketDB.py --month 2024-01
    python FreshDeskTicketDB.py --month 2024-01 --month 2024-02   # multiple months
    python FreshDeskTicketDB.py --year 2024
    python FreshDeskTicketDB.py --from 2024-01-01 --to 2024-03-31
    python FreshDeskTicketDB.py --days 30
    python FreshDeskTicketDB.py --ticket 12345
    python FreshDeskTicketDB.py --force          # re-download already saved tickets
    python FreshDeskTicketDB.py --output MyDB/   # custom folder
"""

import argparse
import calendar
import getpass
import html
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth

# ---- Configuration ----------------------------------------------------------
FRESHDESK_DOMAIN = "yourcompany.freshdesk.com"
FRESHDESK_API_KEY = ""   # leave blank to be prompted at runtime

AGENT_NAMES = {
    "John Doe"
}

STATUS_MAP   = {2: "Open", 3: "Pending", 4: "Resolved", 5: "Closed"}
PRIORITY_MAP = {1: "Low", 2: "Medium", 3: "High", 4: "Urgent"}


# ---- HTML stripper ----------------------------------------------------------

class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []

    def handle_data(self, data):
        self._parts.append(data)

    def handle_entityref(self, name):
        self._parts.append(html.unescape(f"&{name};"))

    def handle_charref(self, name):
        self._parts.append(html.unescape(f"&#{name};"))


def strip_html(text):
    if not text:
        return ""
    p = _HTMLStripper()
    try:
        p.feed(text)
    except Exception:
        return text
    return "\n".join(
        line for line in " ".join("".join(p._parts).split("\n")).splitlines()
    ).strip()


def strip_html_preserve_lines(text):
    """Strip HTML but keep paragraph breaks as newlines."""
    if not text:
        return ""
    # Replace block-level tags with newlines before stripping
    text = re.sub(r'<(?:br|p|div|li|tr|h[1-6])[^>]*>', '\n', text, flags=re.IGNORECASE)
    p = _HTMLStripper()
    try:
        p.feed(text)
    except Exception:
        return text
    raw = "".join(p._parts)
    # Collapse 3+ consecutive newlines to 2
    raw = re.sub(r'\n{3,}', '\n\n', raw)
    return raw.strip()


# ---- EventSentry version extractor -----------------------------------------

_VER_CTX  = re.compile(
    r'(?:version|ver\.?|v|build|release|eventsentry)[:\s=]+(\d+\.\d+(?:\.\d+(?:\.\d+)?)?)',
    re.IGNORECASE,
)
_VER_BARE = re.compile(r'\b([3-9]\.\d+(?:\.\d+(?:\.\d+)?)?)\b')


def extract_eventsentry_version(text):
    candidates = [m.group(1) for m in _VER_CTX.finditer(text)]
    if not candidates:
        candidates = [m.group(1) for m in _VER_BARE.finditer(text)]
    if not candidates:
        return None
    return max(candidates, key=lambda v: (len(v.split(".")), v))


# ---- Freshdesk client -------------------------------------------------------

class Freshdesk:
    def __init__(self, domain, api_key):
        domain = domain.replace("https://", "").replace("http://", "").rstrip("/")
        self.base   = f"https://{domain}/api/v2"
        self.domain = domain
        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth(api_key, "X")
        self.session.headers.update({"Content-Type": "application/json"})
        self._user_cache  = {}
        self._agent_map   = None

    def preflight(self):
        url = f"{self.base}/agents/me"
        try:
            r = self.session.get(url, timeout=15)
        except requests.RequestException as e:
            sys.exit(f"error: could not reach {self.base!r}: {e}")
        if r.status_code == 404:
            sys.exit("error: 404 — wrong domain? Must be your *.freshdesk.com subdomain.")
        if r.status_code in (401, 403):
            sys.exit(f"error: {r.status_code} — check your API key.")
        r.raise_for_status()

    def _get(self, path, params=None):
        url = self.base + path if path.startswith("/") else f"{self.base}/{path}"
        for attempt in range(6):
            r = self.session.get(url, params=params, timeout=30)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", "30"))
                print(f"  rate-limited, sleeping {wait}s ...", file=sys.stderr)
                time.sleep(wait + 1)
                continue
            if r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r
        r.raise_for_status()

    def _paginate(self, path, params=None):
        params = dict(params or {})
        params.setdefault("per_page", 100)
        page = 1
        while True:
            params["page"] = page
            r     = self._get(path, params=params)
            batch = r.json()
            if not batch:
                return
            yield from batch
            if len(batch) < params["per_page"]:
                return
            page += 1
            if page > 300:
                return

    def agent_map(self):
        if self._agent_map is None:
            m = {}
            try:
                for a in self._paginate("/agents"):
                    contact = a.get("contact") or {}
                    name = (contact.get("name") or a.get("name") or "").strip()
                    if not name:
                        continue
                    if a.get("id"):
                        m[a["id"]] = name
                    if contact.get("id"):
                        m[contact["id"]] = name
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code in (401, 403):
                    print("warning: cannot list agents (403). Using built-in AGENT_NAMES.",
                          file=sys.stderr)
                else:
                    raise
            self._agent_map = m
        return self._agent_map

    def user_name(self, user_id, fallback_email=None):
        if user_id in self._user_cache:
            return self._user_cache[user_id]
        agents = self.agent_map()
        if user_id in agents:
            self._user_cache[user_id] = agents[user_id]
            return agents[user_id]
        try:
            r    = self._get(f"/contacts/{user_id}")
            data = r.json()
            name  = (data.get("name")  or "").strip()
            email = (data.get("email") or "").strip()
            company = ""
            if data.get("company_id"):
                try:
                    cr = self._get(f"/companies/{data['company_id']}")
                    company = cr.json().get("name", "")
                except Exception:
                    pass
            label = name or email or fallback_email or f"Contact {user_id}"
            self._user_cache[user_id] = label
            return label
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code != 404:
                raise
        label = fallback_email or f"Unknown {user_id}"
        self._user_cache[user_id] = label
        return label

    def tickets_in_window(self, since_dt: datetime, until_dt: datetime | None = None):
        """Yield tickets with updated_at >= since_dt (and optionally <= until_dt)."""
        since_iso = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "updated_since": since_iso,
            "order_by":      "updated_at",
            "order_type":    "asc",
            "include":       "requester,company",
        }
        for ticket in self._paginate("/tickets", params=params):
            updated = ticket.get("updated_at") or ""
            created = ticket.get("created_at") or ""
            if until_dt:
                # Filter by created_at being within the window
                if not created or created[:10] > until_dt.strftime("%Y-%m-%d"):
                    continue
            yield ticket

    def ticket_detail(self, ticket_id):
        r = self._get(f"/tickets/{ticket_id}",
                      params={"include": "requester,company,conversations"})
        return r.json()

    def conversations(self, ticket_id):
        yield from self._paginate(f"/tickets/{ticket_id}/conversations")


# ---- Date range helpers -----------------------------------------------------

def parse_date_args(args):
    """
    Returns a list of (start_dt, end_dt) tuples to process.
    Multiple --month flags produce multiple ranges.
    """
    ranges = []
    now = datetime.now(timezone.utc)

    if args.month:
        for m in args.month:
            try:
                year, month = map(int, m.split("-"))
            except ValueError:
                sys.exit(f"error: --month must be YYYY-MM, got {m!r}")
            start = datetime(year, month, 1, tzinfo=timezone.utc)
            last  = calendar.monthrange(year, month)[1]
            end   = datetime(year, month, last, 23, 59, 59, tzinfo=timezone.utc)
            ranges.append((start, end))
        return ranges

    if args.year:
        try:
            year = int(args.year)
        except ValueError:
            sys.exit(f"error: --year must be YYYY, got {args.year!r}")
        start = datetime(year,  1,  1,  0,  0,  0, tzinfo=timezone.utc)
        end   = datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        return [(start, end)]

    if args.from_date or args.to_date:
        try:
            start = (datetime.fromisoformat(args.from_date).replace(tzinfo=timezone.utc)
                     if args.from_date else datetime(2010, 1, 1, tzinfo=timezone.utc))
            end   = (datetime.fromisoformat(args.to_date).replace(tzinfo=timezone.utc)
                     if args.to_date else now)
        except ValueError as e:
            sys.exit(f"error: invalid date: {e}")
        # If end has no time component, push it to end of day
        if end.hour == 0 and end.minute == 0 and end.second == 0:
            end = end.replace(hour=23, minute=59, second=59)
        return [(start, end)]

    if args.days:
        start = now - timedelta(days=args.days)
        return [(start, now)]

    return [(None, None)]   # no filter — all tickets


# ---- Ticket file writer -----------------------------------------------------

SEP  = "=" * 80
DASH = "-" * 80


def format_dt(iso_str):
    if not iso_str:
        return "—"
    return iso_str.replace("T", " ").replace("Z", " UTC")


def write_ticket_file(out_path: Path, ticket: dict, detail: dict,
                      conversations: list, fd: Freshdesk):
    """Write a full, human-readable ticket archive file."""
    all_agent_names = AGENT_NAMES | set(fd.agent_map().values())

    tid      = ticket["id"]
    subject  = (ticket.get("subject") or "(no subject)").strip()
    status   = STATUS_MAP.get(ticket.get("status"),   str(ticket.get("status",   "?")))
    priority = PRIORITY_MAP.get(ticket.get("priority"), str(ticket.get("priority", "?")))
    created  = format_dt(ticket.get("created_at"))
    updated  = format_dt(ticket.get("updated_at"))

    requester = ticket.get("requester") or detail.get("requester") or {}
    req_name  = (requester.get("name")  or "").strip()
    req_email = (requester.get("email") or "").strip()
    req_label = f"{req_name} <{req_email}>" if req_name and req_email else req_name or req_email

    company_obj = ticket.get("company") or detail.get("company") or {}
    company = (company_obj.get("name") or "").strip()

    # Best effort: assigned agent
    responder_id = detail.get("responder_id") or ticket.get("responder_id")
    assigned = fd.agent_map().get(responder_id, "") if responder_id else ""

    desc = strip_html_preserve_lines(
        detail.get("description") or detail.get("description_text") or ""
    )

    # Collect all text for version extraction
    all_text = "\n".join([
        subject, desc,
        " ".join(strip_html(c.get("body") or "") for c in conversations)
    ])
    version = extract_eventsentry_version(all_text)

    ticket_url = f"https://{fd.domain}/helpdesk/tickets/{tid}"

    lines = [
        SEP,
        f"TICKET #{tid}  |  Status: {status}  |  Priority: {priority}",
        f"Created: {created}  |  Updated: {updated}",
        SEP,
        f"SUBJECT   : {subject}",
        f"CUSTOMER  : {req_label}",
    ]
    if company:
        lines.append(f"COMPANY   : {company}")
    if assigned:
        lines.append(f"ASSIGNED  : {assigned}")
    lines.append(f"PRIORITY  : {priority}")
    if version:
        lines.append(f"VERSION   : {version}")
    lines.append(f"URL       : {ticket_url}")
    lines.append(SEP)
    lines.append("")

    # Original description
    desc_ts = ticket.get("created_at", "")
    lines.append(f"[ORIGINAL ISSUE — {format_dt(desc_ts)} — {req_label}]")
    lines.append(DASH)
    lines.append(desc or "(no description)")
    lines.append("")

    if conversations:
        lines.append(SEP)
        lines.append("CONVERSATION")
        lines.append(SEP)
        lines.append("")

        for conv in conversations:
            uid        = conv.get("user_id")
            from_email = (conv.get("from_email") or "").strip()
            ts         = format_dt(conv.get("created_at") or conv.get("updated_at") or "")
            body       = strip_html_preserve_lines(
                conv.get("body") or conv.get("body_text") or ""
            )
            if not body:
                continue

            real_name  = fd.user_name(uid, fallback_email=from_email) if uid else (from_email or "Unknown")
            is_agent   = real_name in all_agent_names or uid in fd.agent_map()
            role       = "AGENT" if is_agent else "CUSTOMER"
            private    = " [PRIVATE NOTE]" if conv.get("private") else ""

            name_label = real_name
            if from_email and from_email.lower() != real_name.lower():
                name_label = f"{real_name} <{from_email}>"

            lines.append(f"[{ts}] {role}: {name_label}{private}")
            lines.append(DASH)
            lines.append(body)
            lines.append("")

    lines.extend([SEP, f"END OF TICKET #{tid}", SEP, ""])

    out_path.write_text("\n".join(lines), encoding="utf-8")


# ---- Main -------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)

    date_group = ap.add_argument_group("date range (pick one)")
    date_group.add_argument("--month", metavar="YYYY-MM", action="append",
                            help="Month to download (repeatable: --month 2024-01 --month 2024-02)")
    date_group.add_argument("--year", metavar="YYYY",
                            help="Full year to download")
    date_group.add_argument("--from", dest="from_date", metavar="YYYY-MM-DD",
                            help="Start date (inclusive)")
    date_group.add_argument("--to", dest="to_date", metavar="YYYY-MM-DD",
                            help="End date (inclusive, defaults to today)")
    date_group.add_argument("--days", type=int,
                            help="Last N days (e.g. --days 30)")

    ap.add_argument("--ticket", type=int, metavar="ID",
                    help="Download a single ticket by ID (ignores date args)")
    ap.add_argument("--output", default="Tickets",
                    help="Output folder (default: Tickets/)")
    ap.add_argument("--force", action="store_true",
                    help="Re-download tickets that already exist on disk")
    args = ap.parse_args()

    api_key = FRESHDESK_API_KEY.strip()
    if not api_key:
        api_key = getpass.getpass("Freshdesk API key: ").strip()
    if not api_key:
        sys.exit("error: API key is required.")

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    fd = Freshdesk(FRESHDESK_DOMAIN, api_key)
    fd.preflight()
    print(f"Connected to {FRESHDESK_DOMAIN}.", file=sys.stderr)
    _ = fd.agent_map()   # preload

    saved = skipped = errors = 0

    if args.ticket:
        # Single ticket mode
        print(f"Fetching ticket #{args.ticket} ...", file=sys.stderr)
        try:
            detail = fd.ticket_detail(args.ticket)
            convs  = list(fd.conversations(args.ticket))
            date_str = (detail.get("created_at") or "")[:10] or "0000-00-00"
            out_path = out_dir / f"{args.ticket}-{date_str}.txt"
            if out_path.exists() and not args.force:
                print(f"  already exists: {out_path.name} (use --force to overwrite)",
                      file=sys.stderr)
            else:
                write_ticket_file(out_path, detail, detail, convs, fd)
                print(f"  saved: {out_path.name}", file=sys.stderr)
                saved += 1
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            errors += 1
    else:
        date_ranges = parse_date_args(args)

        for start_dt, end_dt in date_ranges:
            if start_dt:
                label = (f"{start_dt.strftime('%Y-%m-%d')} → "
                         f"{end_dt.strftime('%Y-%m-%d') if end_dt else 'now'}")
            else:
                label = "all time (no date filter)"
            print(f"Fetching tickets: {label} ...", file=sys.stderr)

            # Use a far-past date if no start given
            fetch_since = start_dt or datetime(2010, 1, 1, tzinfo=timezone.utc)

            tickets_seen = 0
            for ticket in fd.tickets_in_window(fetch_since, end_dt):
                tickets_seen += 1
                tid      = ticket["id"]
                date_str = (ticket.get("created_at") or "")[:10] or "0000-00-00"
                out_path = out_dir / f"{tid}-{date_str}.txt"

                if out_path.exists() and not args.force:
                    skipped += 1
                    continue

                subject = (ticket.get("subject") or "").strip()
                print(f"  #{tid} {date_str}  {subject[:55]}", file=sys.stderr)
                try:
                    detail = fd.ticket_detail(tid)
                    convs  = list(fd.conversations(tid))
                    write_ticket_file(out_path, ticket, detail, convs, fd)
                    saved += 1
                except Exception as e:
                    print(f"    ERROR: {e}", file=sys.stderr)
                    errors += 1

                if tickets_seen % 50 == 0:
                    print(f"  ... {tickets_seen} tickets processed so far", file=sys.stderr)

            print(f"  Range done. {tickets_seen} tickets in window.", file=sys.stderr)

    print(f"\nDone. {saved} saved, {skipped} skipped (already exist), "
          f"{errors} errors.", file=sys.stderr)
    print(f"Tickets at: {out_dir.resolve()}", file=sys.stderr)


if __name__ == "__main__":
    main()
