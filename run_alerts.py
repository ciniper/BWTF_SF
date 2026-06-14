#!/usr/bin/env python3
"""
SF Beach Water Quality Alert Runner

This script checks for water quality alerts and sends notifications.
Can be run manually or scheduled via cron/launchd.

Usage:
    python run_alerts.py                    # Check all stations
    python run_alerts.py --priority-only    # Check only BWTF priority sites
    python run_alerts.py --alerts-only      # Only notify if there are alerts
    python run_alerts.py --dry-run          # Print report without sending notifications

Environment Variables for Notifications:
    SLACK_WEBHOOK_URL       - Slack incoming webhook URL
    DISCORD_WEBHOOK_URL     - Discord webhook URL
    SMTP_SERVER             - SMTP server (default: smtp.gmail.com)
    SMTP_PORT               - SMTP port (default: 587)
    SMTP_USERNAME           - SMTP username
    SMTP_PASSWORD           - SMTP password (use app password for Gmail)
    ALERT_FROM_EMAIL        - From email address
    ALERT_TO_EMAILS         - Comma-separated list of recipient emails
    TWILIO_ACCOUNT_SID      - Twilio account SID
    TWILIO_AUTH_TOKEN       - Twilio auth token
    TWILIO_FROM_NUMBER      - Twilio phone number
    TWILIO_TO_NUMBERS       - Comma-separated list of recipient phone numbers
    SOCRATA_APP_TOKEN       - Optional: SF Gov data API token for higher rate limits

Example cron entry (check every 6 hours):
    0 */6 * * * cd /path/to/BWTF && python run_alerts.py --alerts-only >> /var/log/bwtf_alerts.log 2>&1
"""

import argparse
import sys
from datetime import datetime

from features.alerts.monitoring import SFWaterQualityMonitor, CombinedWaterQualityMonitor
from features.alerts.notifiers import create_notifier_from_env, ConsoleNotifier


def main():
    parser = argparse.ArgumentParser(
        description="SF Beach Water Quality Alert System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "--priority-only",
        action="store_true",
        help="Only check BWTF priority monitoring sites"
    )
    parser.add_argument(
        "--alerts-only",
        action="store_true",
        help="Only send notifications if there are active alerts"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print report to console without sending notifications"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=3,
        help="Number of days of data to check (default: 3)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output alerts as JSON"
    )
    
    parser.add_argument(
        "--realtime",
        action="store_true",
        help="Use SFPUC real-time API for CSO alerts (recommended)"
    )
    parser.add_argument(
        "--weather",
        action="store_true",
        help="Include weather (rain advisory) and tide data in report"
    )
    
    args = parser.parse_args()
    
    print(f"\n{'='*60}")
    print(f"SF Beach Water Quality Alert Check")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")
    
    # Initialize monitor - use combined monitor by default for real-time CSO data
    if args.realtime or not args.priority_only:
        print("Using combined monitor (SFPUC real-time + SF Gov API)...")
        monitor = CombinedWaterQualityMonitor()
        alerts = monitor.get_combined_alerts()
        report = monitor.format_combined_report()
    else:
        print("Using SF Gov API only...")
        monitor = SFWaterQualityMonitor()
        alerts = monitor.check_for_alerts(
            days=args.days,
            priority_only=args.priority_only
        )
        report = monitor.format_status_report()
    
    # Optionally include weather/tide data
    if args.weather:
        try:
            from shared.weather_tides import EnvironmentalContext
            env = EnvironmentalContext()
            weather_report = env.format_environmental_report()
            report = weather_report + "\n\n" + report
        except ImportError:
            print("⚠️  weather_tides module not found. Skipping weather data.")
        except Exception as e:
            print(f"⚠️  Error fetching weather data: {e}")
    
    # JSON output mode
    if args.json:
        import json
        output = {
            "timestamp": datetime.now().isoformat(),
            "alert_count": len(alerts),
            "alerts": [
                {
                    "type": a.alert_type,
                    "station_id": a.station_id,
                    "station_name": a.station_name,
                    "message": a.message,
                    "severity": a.severity,
                    "sample_date": a.sample_date.isoformat(),
                    "details": a.details
                }
                for a in alerts
            ]
        }
        print(json.dumps(output, indent=2))
        return 0
    
    # Check if we should send notifications
    if args.alerts_only and not alerts:
        print("No alerts found. Skipping notifications (--alerts-only mode).")
        print(f"\nChecked {args.days} days of data.")
        return 0
    
    # Send notifications
    if args.dry_run:
        print("DRY RUN - Printing report only:\n")
        notifier = ConsoleNotifier()
    else:
        notifier = create_notifier_from_env()
    
    success = notifier.send(alerts, report)
    
    if alerts:
        print(f"\n⚠️  Found {len(alerts)} alert(s)")
        return 1 if any(a.severity == "warning" for a in alerts) else 0
    else:
        print("\n✅ No alerts - all stations within standards")
        return 0


if __name__ == "__main__":
    sys.exit(main())
