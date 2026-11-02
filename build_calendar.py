"""Update an unofficial opera-only iCal feed from the Opernhaus Zürich calendar."""
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


def escape(s):
    return str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(s):
    lines, current = [], ""
    for c in s:
        if len((current + c).encode("utf-8")) > 75:
            lines.append(current)
            current = " " + c
        else:
            current += c
    return lines + [current]


def read_stable(page, selector="button", attempts=6):
    for attempt in range(attempts):
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            return page.locator(selector).all_text_contents()
        except Exception as exc:
            print("Page navigation retry", attempt + 1, str(exc)[:120], flush=True)
            page.wait_for_timeout(1500)
    raise RuntimeError("Could not read website after repeated navigation")


def click_button(page, label):
    """Use a DOM click: the site has filter/month buttons hidden from role-based lookup."""
    page.evaluate("""label => {
      const buttons = Array.from(document.querySelectorAll('button'));
      const button = buttons.find(b => b.textContent.trim() === label);
      if (!button) throw new Error('Button not found: ' + label);
      button.click();
    }""", label)
    page.wait_for_timeout(1200)
    page.wait_for_load_state("domcontentloaded", timeout=30000)
    page.wait_for_timeout(600)


def parse(text, chosen_month, chosen_year):
    lines = [s.strip() for s in text.splitlines() if s.strip()]
    try:
        lines = lines[lines.index("Kalendarium") + 1:]
    except ValueError:
        raise RuntimeError("Kalendarium heading missing")
    for i, line in enumerate(lines):
        if line in ("Kontakt", "Impressum", "Datenschutz"):
            lines = lines[:i]
            break
    year, month = chosen_year, chosen_month
    day = None
    events = []
    for i, line in enumerate(lines):
        header = MONTH.fullmatch(line)
        if header and header.group(1) in MONTHS:
            month, year = MONTHS[header.group(1)], int(header.group(2))
            day = None
            continue
        date_header = DAY.fullmatch(line)
        if date_header and date_header.group(2) in MONTHS:
            day = (year, MONTHS[date_header.group(2)], int(date_header.group(1)))
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
            dt = datetime(*day, int(clock.group(1)), int(clock.group(2)),
                          tzinfo=ZoneInfo("Europe/Zurich"))
        except ValueError:
            continue
        events.append((dt, title, venue))
    return events


def main():
    all_events = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(locale="de-CH", timezone_id="Europe/Zurich")
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1500)
        try:
            page.get_by_role("button", name="Ablehnen", exact=True).last.click(timeout=2500)
        except Exception:
            pass
        # The website navigates to ?filter=1 when Oper is selected.
        click_button(page, "Oper")
        month_buttons = read_stable(page)
        months = []
        for button_text in month_buttons:
            label = button_text.strip()
            match = MONTH.fullmatch(label)
            if match and match.group(1) in MONTHS:
                item = (MONTHS[match.group(1)], int(match.group(2)), label)
                if item not in months:
                    months.append(item)
        print("Months found:", [item[2] for item in months], flush=True)
        if not months:
            raise RuntimeError("No month buttons found; website may have changed")
        failures = []
        for month, year, label in months:
            try:
                click_button(page, label)
                for retry in range(5):
                    try:
                        body = page.locator("body").inner_text(timeout=12000)
                        break
                    except Exception:
                        page.wait_for_timeout(1500)
                else:
                    raise RuntimeError("Could not read month page")
                found = parse(body, month, year)
                for dt, title, venue in found:
                    all_events[(dt.isoformat(), title, venue)] = (dt, title, venue)
                print(label, "opera events:", len(found), flush=True)
            except Exception as exc:
                failures.append((label, str(exc)))
                print("FAILED", label, exc, flush=True)
        browser.close()
    if failures:
        raise RuntimeError(f"Months failed: {failures}. Not publishing incomplete calendar.")
    if not all_events:
        raise RuntimeError("No opera performances found; not publishing empty calendar")
    stamp = datetime.now(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
    output = ["BEGIN:VCALENDAR", "VERSION:2.0",
              "PRODID:-//Opera Schedules//Opernhaus Zuerich//DE",
              "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
              "X-WR-CALNAME:Opernhaus Zürich – Oper",
              "X-WR-TIMEZONE:Europe/Zurich", "REFRESH-INTERVAL;VALUE=DURATION:P1D",
              "X-PUBLISHED-TTL:PT24H"]
    for dt, title, venue in sorted(all_events.values()):
        uid = hashlib.sha256(f"{dt.isoformat()}|{title}|{venue}".encode()).hexdigest()[:20]
        output.extend(["BEGIN:VEVENT", f"UID:{uid}@opera-schedules", f"DTSTAMP:{stamp}",
                       f"DTSTART;TZID=Europe/Zurich:{dt.strftime('%Y%m%dT%H%M%S')}",
                       f"SUMMARY:{escape(title)}", f"LOCATION:{escape(venue + ', Zürich')}",
                       f"DESCRIPTION:{escape('Spielplan: ' + URL)}", f"URL:{URL}", "END:VEVENT"])
    output.append("END:VCALENDAR")
    OUT.write_bytes(("\r\n".join(line for item in output for line in fold(item)) + "\r\n").encode("utf-8"))
    print("Wrote", OUT, "with", len(all_events), "performances")


if __name__ == "__main__":
    main()
