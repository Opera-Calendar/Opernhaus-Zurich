"""Unofficial Opernhaus Zürich iCalendar subscription (opera category).

Dates/times originate from the official calendar. End times are scheduled durations
when verified (see KNOWN_DURATIONS), otherwise clearly labelled estimates.
"""
import hashlib
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

URL = "https://www.opernhaus.ch/spielplan/kalendarium/"
OUT = Path("opernhaus-zurich.ics")
TZ = ZoneInfo("Europe/Zurich")
MONTHS = {"Jan": 1, "Feb": 2, "Mär": 3, "Mar": 3, "Apr": 4, "Mai": 5,
          "Jun": 6, "Jul": 7, "Aug": 8, "Sep": 9, "Okt": 10, "Nov": 11, "Dez": 12}
DAY = re.compile(r"^(Mo|Di|Mi|Do|Fr|Sa|So)\s+(\d{1,2})\s+([A-Za-zÄÖÜäöü]{3})$")
TIME = re.compile(r"^(\d{1,2})\.(\d{2})$")
MONTH = re.compile(r"^([A-Za-zÄÖÜäöü]{3})\s+(20\d{2})$")
WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

# Durations in minutes. Official duration includes intermissions.
# 'Elektra' is sourced from Opernhaus Zürich's Elektra production page;
# 'Die Walküre' from its 2027 production page. Estimates are not official.
KNOWN_DURATIONS = {
    "elektra": (105, True),
    "die walküre": (300, True),
}
ESTIMATES = {
    "la traviata": 165, "alice im wunderland": 115,
    "rachmaninow – die drei opern": 195, "das rheingold": 165,
    "manon lescaut": 165, "die lustige witwe": 165,
    "doctor atomic": 210, "roméo et juliette": 200,
    "la fanciulla del west": 180, "la clemenza di tito": 180,
    "ascanio in alba": 180, "rinaldo": 180,
    "die zauberflöte": 165, "tosca": 160,
    "l'elisir d'amore": 165, "le nozze di figaro": 210,
    "requiem pour ophélie": 120, "don pasquale": 155,
    "samson et dalila": 190, "la bohème": 155,
    "don carlo": 245, "sophie hunger": 120,
}


def escape(value):
    return str(value).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line):
    lines, current = [], ""
    for ch in line:
        if len((current + ch).encode("utf-8")) > 75:
            lines.append(current)
            current = " " + ch
        else:
            current += ch
    return lines + [current]


def stable_buttons(page):
    for attempt in range(6):
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            return page.locator("button").all_text_contents()
        except Exception as exc:
            print("Navigation retry", attempt + 1, str(exc)[:100], flush=True)
            page.wait_for_timeout(1500)
    raise RuntimeError("Could not read month buttons")


def click_button(page, label):
    page.evaluate("""label => {
        const b = Array.from(document.querySelectorAll('button'))
            .find(b => b.textContent.trim() === label);
        if (!b) throw Error('Missing button: ' + label);
        b.click();
    }""", label)
    page.wait_for_timeout(1400)
    page.wait_for_load_state("domcontentloaded", timeout=30000)
    page.wait_for_timeout(800)


def parse_month(text, expected_month, expected_year):
    lines = [s.strip() for s in text.splitlines() if s.strip()]
    if "Kalendarium" not in lines:
        raise RuntimeError("Missing Kalendarium heading")
    lines = lines[lines.index("Kalendarium") + 1:]
    for i, line in enumerate(lines):
        if line in ("Kontakt", "Impressum", "Datenschutz"):
            lines = lines[:i]
            break
    year, month = expected_year, expected_month
    day = None
    events = []
    for i, line in enumerate(lines):
        header = MONTH.fullmatch(line)
        if header and header.group(1) in MONTHS:
            month, year = MONTHS[header.group(1)], int(header.group(2))
            day = None
            # A heading for an adjacent month is normal in this calendar.
            # Only the selected month is collected in this pass.
            continue
        heading = DAY.fullmatch(line)
        if heading and heading.group(3) in MONTHS:
            heading_month = MONTHS[heading.group(3)]
            # The website may display the first days of the NEXT month at
            # the end of a selected month (e.g. Fr 01 Jan after Dez 2026).
            # They are handled when that next month is selected explicitly.
            # Do not interpret them using the selected month's year.
            if heading_month != expected_month:
                day = None
                continue
            try:
                candidate = datetime(expected_year, expected_month, int(heading.group(2)))
            except ValueError as exc:
                raise RuntimeError(f"Invalid date {line} in {expected_month}/{expected_year}") from exc
            if WEEKDAYS[candidate.weekday()] != heading.group(1):
                raise RuntimeError(f"Weekday/date mismatch for {line} in {expected_year}")
            day = (candidate.year, candidate.month, candidate.day)
            continue
        clock = TIME.fullmatch(line)
        if not (clock and day and i + 2 < len(lines)):
            continue
        venue, title = lines[i + 1], lines[i + 2]
        if not ("Hauptbühne" in venue or "Studiobühne" in venue):
            continue
        if title.lower() in ("tickets", "ausverkauft", "restkarten"):
            continue
        try:
            dt = datetime(*day, int(clock.group(1)), int(clock.group(2)), tzinfo=TZ)
        except ValueError:
            continue
        if (dt.month, dt.year) != (expected_month, expected_year):
            continue
        events.append((dt, title, venue))
    return events


def duration(title):
    norm = " ".join(title.casefold().split())
    if norm in KNOWN_DURATIONS:
        return KNOWN_DURATIONS[norm]
    return ESTIMATES.get(norm, 180), False


def main():
    events = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(locale="de-CH", timezone_id="Europe/Zurich")
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1500)
        try:
            page.get_by_role("button", name="Ablehnen", exact=True).last.click(timeout=2500)
        except Exception:
            pass
        click_button(page, "Oper")
        months = []
        for text in stable_buttons(page):
            match = MONTH.fullmatch(text.strip())
            if match and match.group(1) in MONTHS:
                item = (MONTHS[match.group(1)], int(match.group(2)), text.strip())
                if item not in months:
                    months.append(item)
        if not months:
            raise RuntimeError("No calendar month buttons found")
        failures = []
        for month, year, label in months:
            try:
                click_button(page, label)
                for _ in range(5):
                    try:
                        body = page.locator("body").inner_text(timeout=12000)
                        break
                    except Exception:
                        page.wait_for_timeout(1500)
                else:
                    raise RuntimeError("Cannot read month page")
                found = parse_month(body, month, year)
                for dt, title, venue in found:
                    if dt.date() >= datetime.now(TZ).date():
                        events[(dt.isoformat(), title, venue)] = dt, title, venue
                print(label, len(found), "performances", flush=True)
            except Exception as exc:
                failures.append((label, str(exc)))
        browser.close()
    if failures:
        raise RuntimeError(f"Some months failed; refusing incomplete feed: {failures}")
    if not events:
        raise RuntimeError("No events; refusing empty feed")
    stamp = datetime.now(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
    output = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Opera Schedules//Opernhaus Zuerich//DE",
              "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:Opernhaus Zürich – Oper",
              "X-WR-TIMEZONE:Europe/Zurich", "REFRESH-INTERVAL;VALUE=DURATION:P1D", "X-PUBLISHED-TTL:PT24H"]
    for start, title, venue in sorted(events.values()):
        minutes, official = duration(title)
        end = start + timedelta(minutes=minutes)
        uid = hashlib.sha256(f"{start.isoformat()}|{title}|{venue}".encode()).hexdigest()[:20]
        note = (f"Dauer laut Opernhaus Zürich: ca. {minutes // 60} Std. {minutes % 60:02d} Min. (inkl. Pausen)."
                if official else f"Geschätzte Dauer: ca. {minutes // 60} Std. {minutes % 60:02d} Min.; kein bestätigtes Vorstellungsende.")
        output.extend(["BEGIN:VEVENT", f"UID:{uid}@opera-schedules", f"DTSTAMP:{stamp}",
                       f"DTSTART;TZID=Europe/Zurich:{start.strftime('%Y%m%dT%H%M%S')}",
                       f"DTEND;TZID=Europe/Zurich:{end.strftime('%Y%m%dT%H%M%S')}",
                       f"SUMMARY:{escape(title)}", f"LOCATION:{escape(venue + ', Zürich')}",
                       f"DESCRIPTION:{escape(note + ' Spielplan: ' + URL)}", f"URL:{URL}", "END:VEVENT"])
    output.append("END:VCALENDAR")
    OUT.write_bytes(("\r\n".join(line for item in output for line in fold(item)) + "\r\n").encode())
    print(f"Wrote {len(events)} events to {OUT}")


if __name__ == "__main__":
    main()
