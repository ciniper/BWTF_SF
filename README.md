# SF Beach Water Quality Alert System

A monitoring and alert system for San Francisco beach water quality, designed for the **Surfrider SF Blue Water Task Force (BWTF)**.

## Overview

This system monitors water quality data from multiple sources and sends alerts when:
- **Combined Sewer Overflow (CSO) events** are detected (real-time from SFPUC)
- **Bacteria levels exceed California state standards** (E. coli, Enterococcus, Fecal Coliform)
- **Fecal/Total coliform ratio** exceeds 0.1 (conditional AB 411 standard)
- **30-day geometric mean** can be reviewed in a separate report script (requires 5+ weekly samples)
- **Stations are posted** (elevated bacteria or recent CSO)
- **Rain advisory** is active (avoid water contact 72 hours after rain)
- **Multiple bay stations** show elevated readings (potential CSO indicator)

## Data Sources

| Source | Description | Freshness | URL |
|--------|-------------|-----------|-----|
| **SFPUC Real-Time API** | Live station status & CSO alerts | Real-time | Internal API used by SFPUC map |
| **SF Gov Open Data** | Detailed bacteria measurements | 1-2 day delay | [data.sfgov.org](https://data.sfgov.org/Energy-and-Environment/Beach-Water-Quality-Monitoring/v3fv-x3ux) |
| **NWS Weather API** | Rainfall observations & forecasts | Real-time | [api.weather.gov](https://api.weather.gov/gridpoints/MTR/88,126/forecast) |
| **NOAA CO-OPS API** | Tide predictions (SF Station 9414290) | Real-time | [tidesandcurrents.noaa.gov](https://api.tidesandcurrents.noaa.gov/api/prod/) |
| **SFPUC Website** | Beach Water Quality Map | Real-time | [webapps.sfpuc.org](https://webapps.sfpuc.org/sapps/beachesandbay.html) |

### About the SFPUC Real-Time API

We discovered that the SFPUC website uses an internal API (`infrastructure.sfwater.org/lims.asmx/getBeaches`) that provides real-time station status including:
- **CSO (Combined Sewer Overflow) alerts** - Shows active sewage discharge events
- **Posted status** - Stations with elevated bacteria or recent CSO
- **Safe status** - Stations meeting CA water quality standards

This is more current than the SF Gov Open Data API, which has a 1-2 day delay.

### CSO Outfall Mapping

The system maps CSO discharge points to affected beaches:

| Outfall | Location | Drainage Basin |
|---------|----------|----------------|
| CSD-001, CSD-002, CSD-003 | Ocean Beach | Westside |
| CSD-004 | Ocean Beach / Fort Funston | Westside |
| CSD-005 | China Beach | Westside |
| CSD-006, CSD-007 | Baker Beach | Westside |
| NSB-001 | Aquatic Park / Hyde Street Pier / Crissy Field / Mission Creek / Crane Cove Park | North Shore |
| SEB-001 | Islais Creek / Candlestick Point (Sunnydale Cove, Windsurfer Circle, Jackrabbit Beach) | Southeast |

During heavy rain, CSO discharges are typically ~94% treated stormwater and ~6% sanitary flow.

### Weather & Tide Integration

- **Rain Advisory**: SFPUC advises avoiding water contact during and **72 hours after rain events**. The system checks NWS data for recent and forecasted rain.
- **CSO Risk Assessment**: Heavy rain (>0.5 inches) triggers high CSO risk warnings.
- **Tide Data**: NOAA tide predictions help assess bacteria dilution and beach conditions.

## California State Standards (AB 411)

### Single Sample Maximums

| Indicator | Single Sample Max (MPN/100mL) | 30-Day Geometric Mean |
|-----------|------------------------------|----------------------|
| Enterococcus | 104 | 35 |
| E. coli | 235 | 126 |
| Fecal Coliform | 400 | 200 |
| Total Coliform | 10,000 (or 1,000*) | 1,000 |

*\*Total Coliform limit drops to 1,000 when the fecal-to-total coliform ratio exceeds 0.1*

### Confirmation Before Posting

Per SFPUC policy, beaches without known pollution sources use a confirmation approach:
- A single elevated indicator requires confirmation via:
  - A second elevated indicator in the same sample
  - An elevated indicator at a linked station
  - An elevated indicator in a repeat sample
- Beaches with known sources (storm drains, creek discharges) are posted immediately

## Installation

```bash
# Clone or download this repository
cd BWTF

# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Quick Start

```bash
# Run a quick check and print status report
python run_alerts.py --dry-run

# Include weather and tide data
python run_alerts.py --dry-run --weather

# Check only BWTF priority sites
python run_alerts.py --priority-only --dry-run

# Output as JSON
python run_alerts.py --json

# Launch the unified web dashboard
python -m app.wsgi
```

## Project structure

The app is organized as feature packages behind one web server, so the three
pages can be worked on independently:

```
app/                 the web server (Flask) + the landing page
  wsgi.py            Flask app: routes "/" + every feature's endpoint (run: python -m app.wsgi)
  landing.py         current conditions + CSO status + links to the 3 pages
features/
  alerts/            sewage alert system  (/alerts + alert APIs)
  forecast/          ML CSO forecaster    (/forecast)  — self-contained, lazy-loaded
  comparison/        BWTF vs. city data   (/compare + history charts)
shared/              data clients used by >1 feature (sfpuc_api, weather_tides, paths)
data/                runtime state (subscriptions, simulated CSO events)
run_*.py             CLI entry points (alerts, subscription dispatch, geo-mean report)
```

## Web Dashboard

```bash
# Local dev (Flask's built-in server)
python -m app.wsgi
# Open http://localhost:8080

# Production (what a PaaS host runs via the Procfile)
gunicorn -w 1 --threads 8 -b 0.0.0.0:$PORT app.wsgi:app
```

Pages:
- **`/`** — landing: current conditions, overall CSO status, and links to the three tools
- **`/alerts`** — rain advisory + CSO banners, tide info, summary cards, station cards, subscriber alerts
- **`/forecast`** — machine-learning CSO-risk forecast (requires the ML extras in `requirements.txt`)
- **`/compare`** — Surfrider BWTF vs. city lab results per site, with history charts

API endpoints:
- `GET /api/status` — Station status (JSON)
- `GET /api/alerts` — Active alerts (JSON)
- `GET /api/realtime` — SFPUC real-time data (JSON)
- `GET /api/weather` — Weather and tide data (JSON)
- `GET /api/compare` · `GET /api/site-history?site=<name>` — comparison data
- `GET /forecast/api/data` — current forecast snapshot

## Notification Channels

The system supports multiple notification channels. Configure via environment variables:

### Slack
```bash
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
```

### Discord
```bash
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/YOUR/WEBHOOK/URL"
```

### Email (SMTP)
```bash
export SMTP_SERVER="smtp.gmail.com"
export SMTP_PORT="587"
export SMTP_USERNAME="your-email@gmail.com"
export SMTP_PASSWORD="your-app-password"  # Use app password for Gmail
export ALERT_FROM_EMAIL="your-email@gmail.com"
export ALERT_TO_EMAILS="recipient1@example.com,recipient2@example.com"
```

### SMS (Twilio)
```bash
export TWILIO_ACCOUNT_SID="your-account-sid"
export TWILIO_AUTH_TOKEN="your-auth-token"
export TWILIO_FROM_NUMBER="+1234567890"
export TWILIO_TO_NUMBERS="+1234567890,+0987654321"
```

### Free SMS (Email-to-SMS Gateway)
```bash
export SMTP_USERNAME="your-email@gmail.com"
export SMTP_PASSWORD="your-app-password"
export SMS_GATEWAY_EMAILS="5551234567@vtext.com,5559876543@txt.att.net"
```

Supported carriers: Verizon (`vtext.com`), AT&T (`txt.att.net`), T-Mobile (`tmomail.net`), Sprint (`messaging.sprintpcs.com`), Cricket, MetroPCS, US Cellular.

### SF Gov API Token (Optional)
For higher rate limits on the SF Gov API:
```bash
export SOCRATA_APP_TOKEN="your-app-token"
```

## Scheduling

### Using cron (Linux/macOS)

Check every 6 hours and only notify on alerts:
```bash
# Edit crontab
crontab -e

# Add this line (adjust path as needed)
0 */6 * * * cd /path/to/BWTF && /path/to/venv/bin/python run_alerts.py --alerts-only --weather >> /var/log/bwtf_alerts.log 2>&1
```

### Using launchd (macOS)

Create `~/Library/LaunchAgents/com.surfrider.bwtf-alerts.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.surfrider.bwtf-alerts</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/venv/bin/python</string>
        <string>/path/to/BWTF/run_alerts.py</string>
        <string>--alerts-only</string>
        <string>--weather</string>
    </array>
    <key>StartInterval</key>
    <integer>21600</integer> <!-- Every 6 hours -->
    <key>StandardOutPath</key>
    <string>/tmp/bwtf-alerts.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/bwtf-alerts.error.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>SLACK_WEBHOOK_URL</key>
        <string>your-webhook-url</string>
    </dict>
</dict>
</plist>
```

Load the agent:
```bash
launchctl load ~/Library/LaunchAgents/com.surfrider.bwtf-alerts.plist
```

## Monitored Stations

### BWTF Priority Sites
- Ocean Beach - North (Balboa St)
- Ocean Beach - Lincoln Way
- Ocean Beach - Sloat Blvd
- Crissy Field - East Beach
- Baker Beach

### All Monitored Stations (per SFPUC 2025)

| Station ID | Location | Drainage Basin |
|------------|----------|----------------|
| **Bay Stations** | | |
| BAY#202.4_SL | Aquatic Park - Hyde Street Pier | North Shore |
| BAY#202.5_SL | Aquatic Park - Municipal Pier | North Shore |
| BAY#210.1_SL | Crissy Field - East Beach | North Shore |
| BAY#211_SL | Crissy Field - West Beach | North Shore |
| BAY#220_SL | Baker Beach - East | Westside |
| BAY#230_SL | Baker Beach - Central | Westside |
| BAY#300.1_SL | China Beach | Westside |
| BAY#301.1_SL | Baker Beach - West (Lobos Creek) | Westside |
| BAY#301.2_SL | Baker Beach - West | Westside |
| BAY#305_SL | Mission Creek - Berry St at Kayak Pier | North Shore |
| BAY#310_SL | Crane Cove Park | North Shore |
| BAY#315_SL | Islais Creek - Islais Landing | Southeast |
| BAY#320_SL | Candlestick Point - Sunnydale Cove | Southeast |
| BAY#320.1_SL | Candlestick Point - Windsurfer Circle | Southeast |
| BAY#320.2_SL | Candlestick Point - Jackrabbit Beach | Southeast |
| **Ocean Stations** | | |
| OCEAN#15_SL | Ocean Beach - North (Balboa St) | Westside |
| OCEAN#15EAST_SL | Ocean Beach - North (Balboa St East) | Westside |
| OCEAN#16_SL | Ocean Beach - Lincoln Way | Westside |
| OCEAN#17_SL | Ocean Beach - Judah St | Westside |
| OCEAN#18_SL | Ocean Beach - Noriega St | Westside |
| OCEAN#19_SL | Ocean Beach - Sloat Blvd | Westside |
| OCEAN#20_SL | Ocean Beach - Fort Funston North | Westside |
| OCEAN#21_SL | Ocean Beach - Fort Funston South | Westside |
| OCEAN#21.1_SL | Ocean Beach - Fort Funston | Westside |
| OCEAN#22_SL | Ocean Beach - Thornton Beach | Westside |

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    Data Sources                               │
├──────────────┬──────────────┬──────────────┬─────────────────┤
│ SFPUC LIMS   │ SF Gov API   │ NWS Weather  │ NOAA Tides      │
│ (real-time)  │ (bacteria)   │ (rain)       │ (predictions)   │
└──────┬───────┴──────┬───────┴──────┬───────┴────────┬────────┘
       │              │              │                │
       ▼              ▼              ▼                ▼
┌──────────────────────────────────────────────────────────────┐
│              CombinedWaterQualityMonitor                      │
│  ┌─────────────┐ ┌──────────────┐ ┌────────────────────┐    │
│  │sfpuc_scraper│ │sf_water_     │ │weather_tides       │    │
│  │  .py        │ │quality_alerts│ │  .py               │    │
│  │             │ │  .py         │ │                    │    │
│  │• CSO events │ │• AB 411      │ │• Rain advisory     │    │
│  │• Station    │ │  standards   │ │• Tide predictions  │    │
│  │  status     │ │• Geo mean    │ │• CSO risk          │    │
│  │• Outfall    │ │• Coliform    │ │                    │    │
│  │  mapping    │ │  ratio       │ │                    │    │
│  └─────────────┘ └──────────────┘ └────────────────────┘    │
└──────────────────────────┬───────────────────────────────────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │run_alerts│ │dashboard │ │notifiers │
        │  .py     │ │  .py     │ │  .py     │
        │(CLI)     │ │(Web UI)  │ │(Multi-ch)│
        └──────────┘ └──────────┘ └──────────┘
```

## Usage Examples

### Python API

```python
from core.monitoring import CombinedWaterQualityMonitor

# Initialize combined monitor (all data sources)
monitor = CombinedWaterQualityMonitor()

# Get all active alerts (CSO, bacteria, rain)
alerts = monitor.get_combined_alerts()

# Generate the separate 30-day geometric mean report
python run_geometric_mean_report.py
for alert in alerts:
    print(f"[{alert.severity}] {alert.message}")

# Generate full report with weather
report = monitor.format_combined_report()
print(report)
```

### Weather & Tides

```python
from core.weather_tides import EnvironmentalContext

env = EnvironmentalContext()

# Get rain advisory
rain = env.weather.get_rain_advisory()
print(f"Rain active: {rain.is_active}, CSO risk: {rain.cso_risk}")

# Get tide info
tides = env.tides.get_tide_info()
print(f"Tide trend: {tides.current_trend}")
print(f"Next high: {tides.next_high.time} ({tides.next_high.height_ft} ft)")

# Full environmental context (JSON-serializable)
context = env.get_full_context()
```

### Custom Notifications

```python
from core.monitoring import CombinedWaterQualityMonitor
from core.notifiers import SlackNotifier, EmailNotifier, MultiNotifier

monitor = CombinedWaterQualityMonitor()
alerts = monitor.get_combined_alerts()
report = monitor.format_combined_report()

# Send to multiple channels
notifier = MultiNotifier([
    SlackNotifier(webhook_url="https://hooks.slack.com/..."),
    EmailNotifier(
        smtp_server="smtp.gmail.com",
        username="you@gmail.com",
        password="app-password",
        from_email="you@gmail.com",
        to_emails=["team@example.com"]
    )
])

notifier.send(alerts, report)
```

## Resources

- **SFPUC Beach Monitoring Program**: https://www.sfpuc.gov/programs/ocean-and-beach-monitoring
- **SFPUC Beach Map**: https://webapps.sfpuc.org/sapps/beachesandbay.html
- **Beach Hotline**: 1-877-SFBEACH (1-877-732-3224) or 415-242-2214
- **Rationale for Confirmation Before Posting**: https://www.sfpuc.gov/sites/default/files/programs/BeachesConfirmationBeforePosting.pdf
- **SF Combined Sewer System**: https://www.sfpuc.gov/about-us/our-systems/sewer-system/our-combined-sewer
- **Surfrider SF**: https://sf.surfrider.org/
- **Blue Water Task Force**: https://sf.surfrider.org/blue-water-task-force/
- **AB 411 Standards**: California Code of Regulations, Title 17, Section 7958

## Contributing

Contributions welcome! This is a volunteer project for the Surfrider SF Blue Water Task Force.

## License

MIT License - Feel free to use and modify for water quality monitoring purposes.
