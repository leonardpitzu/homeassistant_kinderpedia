# Kinderpedia for Home Assistant

A custom [Home Assistant](https://www.home-assistant.io/) integration that brings your child's [Kinderpedia](https://mykp.kinderpedia.co) kindergarten data into your smart home dashboard - daily meals, nap times, check-ins, and newsfeed activity, all at a glance.

## Features

### Calendar

A calendar entity (`<name> school`) shows the full weekly timeline your kindergarten reports through Kinderpedia:

- **School event** - a timed event starting at the check-in time and ending at 18:00. The description shows each meal on its own line with an icon and consumption percentage:
  ```
  🥣 Breakfast (100%): Tartine cu hummus, ardei și ceai
  🍽️ Lunch (75%): Supă cremă de morcov, Papricaș de pui
  🍪 Snack: Biscuiți cu ovăz
  ```
- **Nap event** - a separate timed event using the actual nap start/end times reported by the teacher.
- **Absence handling** - when a child is marked absent (motivated or not), no School or Nap events are created for that day. The planned menu is still fetched by the API, but it won't clutter your calendar.
- **Historical data** - past weeks are automatically archived so the calendar can display the full kindergarten history, not just the current week. See [History & backfill](#history--backfill) below.

The calendar entity also exposes **detailed attributes** for the most recent school day (or today, if available): `checkin`, `nap`, `nap_duration`, `breakfast_items`, `breakfast_percent`, `breakfast_kcal`, `lunch_items`, `lunch_percent`, `lunch_kcal`, `snack_items`, and more. These are ready to use in template cards or automations.

### Sensors

| Sensor | Description |
|---|---|
| **Child info** | Name, birth date, gender, kindergarten name |
| **Breakfast week** | Weekly breakfast consumption percentages (Mon-Fri) for charting |
| **Lunch week** | Weekly lunch consumption percentages (Mon-Fri) for charting |
| **Nap week** | Weekly nap durations in minutes (Mon-Fri) for charting |
| **Newsfeed** | Latest school newsfeed post (icon: `mdi:newspaper-variant-outline`). Recent entries available as an attribute |

## Installation

### HACS (recommended)

1. Open HACS in your Home Assistant instance.
2. Go to **Integrations** -> **⋮** -> **Custom repositories**.
3. Add `https://github.com/leonardpitzu/homeassistant_kinderpedia` as an **Integration**.
4. Search for **Kinderpedia** and install it.
5. Restart Home Assistant.

### Manual

1. Copy the `custom_components/kinderpedia` folder into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.

## Configuration

1. Go to **Settings** -> **Devices & Services** -> **Add Integration**.
2. Search for **Kinderpedia**.
3. Enter your Kinderpedia email and password.
4. The integration will discover all children linked to your account and create entities automatically.

Data is polled every 15 minutes.

## History & backfill

The Kinderpedia API returns one week of timeline data at a time. This integration stores past weeks locally so the calendar shows the **full history** - not just the current week.

### How it works

| Scenario | What happens | Frequency |
|---|---|---|
| **Normal polling** | Fetches the current week (`week=0`) | Every 15 minutes |
| **Weekly archive** | Archives the just-completed previous week | Once per week (Monday at 03:00) |
| **Initial backfill** | Walks backwards through all past weeks until it reaches the first week of enrollment | Once, automatically on first install |
| **Manual re-sync** | Triggered via the `kinderpedia.backfill_history` service | On demand |

- Past weeks are **immutable** - once stored they are never re-fetched.
- Data is persisted in Home Assistant's `.storage` directory (one file per child).
- The initial backfill runs in the background and makes one API request every 5 seconds to avoid overloading the server. For a child enrolled since September 2024, that's roughly 75 weeks - about 6 minutes of quiet background work.
- After the initial backfill, the only recurring cost is **one extra API call per week**.

### Manual backfill service

If you need to re-trigger the backfill (e.g. after clearing storage), call the service:

```yaml
service: kinderpedia.backfill_history
```

No parameters needed - it runs for all configured children.

## Dashboard ideas

- Use the **calendar card** for a quick daily glance - meal icons, percentages, and nap times are visible at a glance.
- Use the **week sensors** (`breakfast_week`, `lunch_week`, `nap_week`) with an [ApexCharts card](https://github.com/RomRider/apexcharts-card) to visualise weekly trends.
- Use the calendar entity's **extra attributes** in template cards for a detailed breakdown of today's meals.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Media backup (standalone)

The `media_backup/` folder contains a standalone Python script that downloads all photos and videos from your Kinderpedia gallery, timestamps them with EXIF metadata, and organises them into album folders - ready to import into Apple Photos (or any other library).

> **Note:** This tool is completely independent of the Home Assistant integration. HACS does not deploy it, and it has no effect on the HA setup.

### Quick start

```bash
cd media_backup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.json.template config.json   # then fill in your credentials
python downloader.py
```

### Configuration

Copy `config.json.template` to `config.json` and fill in your values:

```json
{
    "email": "your_email@example.com",
    "password": "your_password",
    "child_id": "000000",
    "kindergarten_id": "0000"
}
```

`config.json` is gitignored and stays local. The template is tracked so you can recreate the config after a fresh clone.

### What it does

- Logs in to Kinderpedia and paginates through the full photo and video gallery.
- Downloads images into per-album folders under `media_backup/downloads/`.
- Embeds the original date into EXIF `DateTimeOriginal` and sets the file's mtime, so Apple Photos sorts them chronologically.
- Writes album descriptions into each image's EXIF `ImageDescription` field.
- Downloads Vimeo-hosted videos via `yt-dlp`.

### Prerequisites

[FFmpeg](https://ffmpeg.org/) must be installed and available on your `PATH` - `yt-dlp` needs it to merge video and audio streams from Vimeo.

```bash
# macOS
brew install ffmpeg
```
