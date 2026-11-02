"""Build an Opera-only, subscribable iCalendar from Opernhaus Zürich's public calendar.

This is a browser-based scraper, not an official Opernhaus feed. Changes to their
website may require changes here. DTSTART only: no unsupported end-time guesses.
"""
import hashlib
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

URL = "https://www.opernhaus.ch/spielplan/kalendarium/"
OUT = Path("opernhaus-zurich.ics")
MONTHS = {"Jan": 1, "Feb": 2, "Mär": 3, "Mar": 3, "Apr": 4, "Mai": 5,
          "Jun": 6, "Jul": 7, "Aug": 8, "Sep": 9, "Okt": 10, "Nov": 11, "Dez": 12}
DAY = re.compile(r"^(?:Mo|Di|Mi|Do|Fr|Sa|So)\s+(\d{1,2})\s+([A-Za-zÄÖÜäöü]{3})$")
TIME = re.compile(r"^(\d{1,2})\.(\d{2})$")
MONTH = re.compile(r"^([A-Za-zÄÖÜäöü]{3})\s+(20\d{2})$")
IGNORED = ("Tickets", "Ausverkauft", "Restkarten", "Premiere", "zum letzten Mal",
           "Opernhaustag", "Hauptbühne Opernhaus")


def escape(s):
    return str(s).replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')


def fold(s):
    """Fold ICS lines to max 75 UTF-8 octets."""
    result, line = [], ""
    for c in s:
        if len((line + c).encode('utf-8')) > 75:
            result.append(line)
            line = ' ' + c
        else:
            line += c
    return result + [line]


def parse(text, selected_month, selected_year):
    """Extract dates and filtered performances from browser-visible text."""
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    try:
        start = next(i for i, x in enumerate(lines) if x == 'Kalendarium') + 1
        lines = lines[start:]
    except StopIteration:
        raise RuntimeError('Kalendarium heading missing; website markup may have changed')
    for i, s in enumerate(lines):
        if s in ('Kontakt', 'Impressum', 'Datenschutz'):
            lines = lines[:i]
            break
    current_month, current_year = selected_month, selected_year
    date = None
    events = []
    i = 0
    while i < len(lines):
        s = lines[i]
        mm = MONTH.fullmatch(s)
        if mm and mm.group(1) in MONTHS:
            current_month, current_year = MONTHS[mm.group(1)], int(mm.group(2))
            i += 1
            continue
        dm = DAY.fullmatch(s)
        if dm and dm.group(2) in MONTHS:
            date = (current_year, MONTHS[dm.group(2)], int(dm.group(1)))
            i += 1
            continue
        tm = TIME.fullmatch(s)
        if tm and date and i+2 < len(lines):
            venue, title = lines[i+1], lines[i+2]
            if not TIME.fullmatch(venue) and not DAY.fullmatch(venue):
                # Opera filter should hide other categories; accept only main-stage or
                # studio performances, not tour/workshop locations.
                if ('Hauptbühne' in venue or 'Studiobühne' in venue) and title not in IGNORED:
                    try:
                        dt = datetime(*date, int(tm.group(1)), int(tm.group(2)), tzinfo=ZoneInfo('Europe/Zurich'))
                        events.append((dt, title, venue))
                    except ValueError:
                        pass
                i += 3
                continue
        i += 1
    return events


def main():
    all_events = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(locale='de-CH', timezone_id='Europe/Zurich')
        page.goto(URL, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_timeout(1800)
        # Cookie banner may overlay the filter.
        try:
            page.get_by_role('button', name='Ablehnen', exact=True).last.click(timeout=2500)
        except Exception:
            pass
        # Site provides an Opera filter; using it prevents concert/workshop entries.
        print("PAGE TITLE:", page.title(), flush=True)
        print("PAGE URL:", page.url, flush=True)
        print("VISIBLE TEXT:", page.locator("body").inner_text(timeout=10000)[:3000], flush=True)
        page.get_by_role('button', name="Oper", exact=True).first.click(timeout=10000)

        page.wait_for_timeout(1000)
        # Collect available months, rather than assuming the season always ends in July.
        candidates = page.get_by_role('button').all_text_contents()
        months = []
        for t in candidates:
            m = re.fullmatch(r'\s*([A-Za-zÄÖÜäöü]{3})\s+(20\d{2})\s*', t)
            if m and m.group(1) in MONTHS:
                key = (MONTHS[m.group(1)], int(m.group(2)), m.group(0).strip())
                if key not in months:
                    months.append(key)
        if not months:
            raise RuntimeError('No month buttons found on Opernhaus Zürich site')
        for month, year, label in months:
            try:
                page.get_by_role('button', name=label, exact=True).first.click(timeout=10000)
                page.wait_for_timeout(700)
                txt = page.locator('body').inner_text(timeout=15000)
                found = parse(txt, month, year)
                for dt, title, venue in found:
                    all_events[(dt.isoformat(), title, venue)] = (dt, title, venue)
                print(label, 'events', len(found))
            except Exception as exc:
                print('Failed to read', label, exc)
        browser.close()
    if not all_events:
        raise RuntimeError('No opera performances found: refusing to overwrite calendar with empty file')
    now = datetime.now(ZoneInfo('UTC')).strftime('%Y%m%dT%H%M%SZ')
    output = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Opera Schedules//Opernhaus Zuerich//DE',
              'CALSCALE:GREGORIAN', 'METHOD:PUBLISH', 'X-WR-CALNAME:Opernhaus Zürich – Oper',
              'X-WR-TIMEZONE:Europe/Zurich', 'REFRESH-INTERVAL;VALUE=DURATION:P1D',
              'X-PUBLISHED-TTL:PT24H']
    for dt, title, venue in sorted(all_events.values()):
        uid = hashlib.sha256(f'{dt.isoformat()}|{title}|{venue}'.encode()).hexdigest()[:20]
        output.extend(['BEGIN:VEVENT', f'UID:{uid}@opera-schedules', f'DTSTAMP:{now}',
                       f'DTSTART;TZID=Europe/Zurich:{dt.strftime("%Y%m%dT%H%M%S")}',
                       f'SUMMARY:{escape(title)}', f'LOCATION:{escape(venue + ", Zürich")}',
                       f'DESCRIPTION:{escape("Offizieller Spielplan: " + URL)}',
                       f'URL:{URL}', 'END:VEVENT'])
    output.append('END:VCALENDAR')
    OUT.write_bytes(('\r\n'.join(line for x in output for line in fold(x)) + '\r\n').encode('utf-8'))
    print('Wrote', OUT, 'with', len(all_events), 'events')

if __name__ == '__main__':
    main()
