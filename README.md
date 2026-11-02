# Opernhaus Zürich – opera-only iCal subscription

Unofficial feed built from the public [Opernhaus Zürich calendar](https://www.opernhaus.ch/spielplan/kalendarium/).

## Setup

1. Create a new **public** GitHub repository, for example `opernhaus-zurich-calendar`.
2. Upload `build_calendar.py` at the repository root and `.github/workflows/update.yml` at that exact path. Alternatively upload the contents of this folder using a GitHub-capable Git client. Keep the directories.
3. Under **Actions → Update Opernhaus Zürich calendar**, choose **Run workflow**.
4. Wait for a successful run; the generated `opernhaus-zurich.ics` should appear in the repository root.
5. Subscribe in Apple Calendar: **File → New Calendar Subscription**, pasting:

   `https://raw.githubusercontent.com/YOUR-GITHUB-USERNAME/opernhaus-zurich-calendar/main/opernhaus-zurich.ics`

6. Choose auto-refresh daily in Calendar. If your repository name or branch differs, adjust the URL.

This is a subscription, **not** a one-time import. GitHub Actions runs daily, but its scheduled jobs may be delayed. The scraper relies on the public website and may need maintenance if the website changes. Start times come from the website; end times are deliberately omitted rather than guessed. No cast or ticket sale date is asserted.
