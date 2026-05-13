#!/usr/bin/env python3
"""
FreshDeskTicketSearch.py — Search the local ticket archive built by FreshDeskTicketDB.py.

Searches through Tickets/*.txt files by customer name, subject, content,
status, EventSentry version, or date range. Outputs matching tickets with
a context snippet around each match.

No API key required — searches local files only.

Usage:
    python FreshDeskTicketSearch.py --customer "John Smith"
    python FreshDeskTicketSearch.py --subject "email alert"
    python FreshDeskTicketSearch.py --search "smtp timeout"
    python FreshDeskTicketSearch.py --version 6.1
    python FreshDeskTicketSearch.py --status resolved
    python FreshDeskTicketSearch.py --month 2024-01
    python FreshDeskTicketSearch.py --from 2024-01-01 --to 2024-03-31
    python FreshDeskTicketSearch.py --ticket 12345
    python FreshDeskTicketSearch.py --customer "Acme" --search "agent"   # AND logic
    python FreshDeskTicketSearch.py --search "syslog" --show-full        # full file
"""

import argparse
import calendar
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


# ---- File loader ------------------------------------------------------------

def iter_ticket_files(tickets_dir: Path):
    """Yield Path objects for all ticket .txt files, sorted by ticket ID."""
    files = list(tickets_dir.glob("*.txt"))
    def sort_key(p):
        m = re.match(r'^(\d+)-', p.name)
        return int(m.group(1)) if m else 0
    yield from sorted(files, key=sort_key)


def read_header(path: Path) -> dict:
    """
    Parse the structured header lines of a ticket file.
    Returns a dict with keys: ticket_id, date, subject, customer, company,
    assigned, status, priority, version.
    """
    info = {
        "ticket_id": "", "date": "", "subject": "", "customer": "",
        "company": "", "assigned": "", "status": "", "priority": "", "version": "",
    }
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.rstrip()
                if line.startswith("TICKET #"):
                    m = re.search(r'TICKET #(\d+)', line)
                    if m:
                        info["ticket_id"] = m.group(1)
                    sm = re.search(r'Status:\s*(\w+)', line)
                    if sm:
                        info["status"] = sm.group(1).lower()
                elif line.startswith("Created:"):
                    m = re.search(r'Created:\s*([\d-]+)', line)
                    if m:
                        info["date"] = m.group(1)
                elif line.startswith("SUBJECT"):
                    info["subject"] = line.split(":", 1)[-1].strip()
                elif line.startswith("CUSTOMER"):
                    info["customer"] = line.split(":", 1)[-1].strip()
                elif line.startswith("COMPANY"):
                    info["company"] = line.split(":", 1)[-1].strip()
                elif line.startswith("ASSIGNED"):
                    info["assigned"] = line.split(":", 1)[-1].strip()
                elif line.startswith("VERSION"):
                    info["version"] = line.split(":", 1)[-1].strip()
                elif line.startswith("=" * 10) and info["subject"]:
                    # Second separator block = end of header
                    if info.get("_header_done"):
                        break
                    info["_header_done"] = True
    except OSError:
        pass
    return info


# ---- Matching ---------------------------------------------------------------

def find_context(text: str, pattern: re.Pattern, context_lines: int = 2) -> list[str]:
    """
    Return snippets: [context_lines] lines before and after each match line.
    Deduplicates overlapping windows.
    """
    lines   = text.splitlines()
    matched = set()
    for i, line in enumerate(lines):
        if pattern.search(line):
            start = max(0, i - context_lines)
            end   = min(len(lines), i + context_lines + 1)
            for j in range(start, end):
                matched.add(j)

    if not matched:
        return []

    snippets = []
    sorted_lines = sorted(matched)
    group = [sorted_lines[0]]
    for idx in sorted_lines[1:]:
        if idx == group[-1] + 1:
            group.append(idx)
        else:
            snippets.append(group)
            group = [idx]
    snippets.append(group)

    result = []
    for group in snippets:
        result.append("  " + "\n  ".join(lines[i] for i in group))
    return result


def match_ticket(path: Path, args, patterns: dict) -> dict | None:
    """
    Check if a ticket file matches all specified filters.
    Returns a result dict, or None if no match.
    """
    header = read_header(path)

    # --- Structured header filters (fast, no full read) ---
    if args.status:
        if args.status.lower() not in header["status"].lower():
            return None

    if args.version:
        if not header["version"].startswith(args.version):
            return None

    if args.ticket:
        if header["ticket_id"] != str(args.ticket):
            return None

    if args.from_date or args.to_date or args.month or args.year:
        date = header.get("date", "")
        if not date:
            return None
        if args.from_date and date < args.from_date:
            return None
        if args.to_date and date > args.to_date:
            return None

    if patterns.get("subject"):
        if not patterns["subject"].search(header["subject"]):
            return None

    if patterns.get("customer"):
        if not patterns["customer"].search(header["customer"] + " " + header["company"]):
            return None

    # --- Full text search (slower — only if needed) ---
    snippets = {}
    try:
        full_text = path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"warning: cannot read {path.name}: {e}", file=sys.stderr)
        return None

    for key in ("search",):
        if patterns.get(key):
            found = find_context(full_text, patterns[key])
            if not found:
                return None
            snippets[key] = found

    return {"header": header, "snippets": snippets, "path": path, "full_text": full_text}


# ---- Output -----------------------------------------------------------------

def print_result(result: dict, show_full: bool):
    h   = result["header"]
    sep = "-" * 70

    ver = f"  [EventSentry {h['version']}]" if h["version"] else ""
    print(f"\n{sep}")
    print(f"Ticket #{h['ticket_id']}  |  {h['date']}  |  {h['status'].capitalize()}{ver}")
    print(f"Subject  : {h['subject']}")
    print(f"Customer : {h['customer']}")
    if h["company"]:
        print(f"Company  : {h['company']}")
    if h["assigned"]:
        print(f"Assigned : {h['assigned']}")
    print(f"File     : {result['path'].name}")

    if show_full:
        print()
        print(result["full_text"])
        return

    for key, snippets in result["snippets"].items():
        if snippets:
            print(f"\n  --- matches ({key}) ---")
            for snip in snippets[:3]:    # cap at 3 snippets per ticket
                print(snip)
            if len(snippets) > 3:
                print(f"  ... ({len(snippets) - 3} more matches)")


# ---- Date arg helpers -------------------------------------------------------

def resolve_date_range(args):
    """Return (from_date_str, to_date_str) as YYYY-MM-DD strings or None."""
    if args.month:
        try:
            year, month = map(int, args.month.split("-"))
        except ValueError:
            sys.exit(f"error: --month must be YYYY-MM, got {args.month!r}")
        last = calendar.monthrange(year, month)[1]
        return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last:02d}"

    if args.year:
        try:
            year = int(args.year)
        except ValueError:
            sys.exit(f"error: --year must be YYYY, got {args.year!r}")
        return f"{year:04d}-01-01", f"{year:04d}-12-31"

    return args.from_date or None, args.to_date or None


# ---- Main -------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)

    ap.add_argument("--customer", metavar="NAME",
                    help="Search by customer name or email (partial match)")
    ap.add_argument("--subject", metavar="TEXT",
                    help="Search by ticket subject (partial match)")
    ap.add_argument("--search", metavar="TEXT",
                    help="Full-text search across the entire ticket (partial match)")
    ap.add_argument("--version", metavar="VER",
                    help="Filter by EventSentry version prefix (e.g. 6.1)")
    ap.add_argument("--status", metavar="STATUS",
                    help="Filter by status: open, pending, resolved, closed")
    ap.add_argument("--ticket", type=int, metavar="ID",
                    help="Show a specific ticket by ID")

    date_group = ap.add_argument_group("date filters (pick one)")
    date_group.add_argument("--month", metavar="YYYY-MM",
                            help="Tickets created in this month")
    date_group.add_argument("--year", metavar="YYYY",
                            help="Tickets created in this year")
    date_group.add_argument("--from", dest="from_date", metavar="YYYY-MM-DD",
                            help="Created on or after this date")
    date_group.add_argument("--to", dest="to_date", metavar="YYYY-MM-DD",
                            help="Created on or before this date")

    ap.add_argument("--tickets-dir", default="Tickets",
                    help="Ticket archive folder (default: Tickets/)")
    ap.add_argument("--show-full", action="store_true",
                    help="Print the full ticket file instead of snippets")
    ap.add_argument("--limit", type=int, default=50,
                    help="Max results to show (default: 50)")
    ap.add_argument("--case-sensitive", action="store_true",
                    help="Case-sensitive search (default: case-insensitive)")
    args = ap.parse_args()

    # Resolve month/year into from/to for the matching function
    args.from_date, args.to_date = resolve_date_range(args)

    tickets_dir = Path(args.tickets_dir)
    if not tickets_dir.exists():
        sys.exit(f"error: folder {tickets_dir!r} not found.\n"
                 f"Run FreshDeskTicketDB.py first.")

    # Compile search patterns
    flags    = 0 if args.case_sensitive else re.IGNORECASE
    patterns = {}
    if args.customer:
        patterns["customer"] = re.compile(re.escape(args.customer), flags)
    if args.subject:
        patterns["subject"]  = re.compile(re.escape(args.subject),  flags)
    if args.search:
        patterns["search"]   = re.compile(re.escape(args.search),   flags)

    if not any([args.customer, args.subject, args.search, args.version,
                args.status, args.ticket, args.from_date, args.to_date]):
        ap.error("provide at least one filter: --customer, --subject, --search, "
                 "--version, --status, --ticket, --month, --year, --from/--to")

    all_files = list(iter_ticket_files(tickets_dir))
    print(f"Searching {len(all_files)} tickets in {tickets_dir} ...", file=sys.stderr)

    results = []
    for path in all_files:
        result = match_ticket(path, args, patterns)
        if result:
            results.append(result)
        if len(results) >= args.limit:
            print(f"  limit of {args.limit} results reached (use --limit N for more)",
                  file=sys.stderr)
            break

    print(f"Found {len(results)} matching ticket(s).\n", file=sys.stderr)

    for result in results:
        print_result(result, args.show_full)

    if not results:
        print("No tickets matched your search.")


if __name__ == "__main__":
    main()
