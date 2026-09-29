#!/usr/bin/env python3
"""
Notification handlers for SF Water Quality Alerts

Supports multiple notification channels:
- Console output
- Email (via SMTP)
- Slack webhook
- SMS (via Twilio)
- Discord webhook

Configure via environment variables or pass credentials directly.
"""

import os
import json
import smtplib
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from abc import ABC, abstractmethod
from typing import Optional
from datetime import datetime
from pathlib import Path
import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from features.alerts.monitoring import Alert

BWTF_LOGO_URL = "https://bwtf.surfrider.org/images/BWTF-Logo_White.png"
SURFRIDER_LOGO_URL = "https://f.hubspotusercontent20.net/hubfs/20811975/SF-Horizontal-Logo_RGB_Black_crop_small.png"

# Cap every SMTP connection. Hosts that firewall outbound SMTP (e.g. Railway
# trial plans) DROP packets silently; without a timeout that hangs the sender
# thread forever — which froze the alert watcher in production.
SMTP_TIMEOUT_SECONDS = int(os.environ.get("SMTP_TIMEOUT", "20"))

# ── HTTP email transport (Brevo) ─────────────────────────────────────────────
# Railway blocks outbound SMTP on every plan below Pro, so the preferred email
# path is Brevo's HTTPS API (port 443 — never blocked). Set BREVO_API_KEY and
# ALERT_FROM_EMAIL (a sender verified in the Brevo account). When the key is
# present, EmailNotifier and EmailToSMSNotifier send over HTTP; otherwise they
# fall back to SMTP.
BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
HTTP_EMAIL_TIMEOUT_SECONDS = int(os.environ.get("HTTP_EMAIL_TIMEOUT", "20"))


def brevo_api_key() -> Optional[str]:
    return os.environ.get("BREVO_API_KEY") or None


def email_transport_configured() -> bool:
    """True when some way to send email exists: Brevo HTTP API or SMTP creds."""
    if brevo_api_key():
        return True
    return all(os.environ.get(key) for key in ("SMTP_USERNAME", "SMTP_PASSWORD"))


class BrevoSendError(RuntimeError):
    """Brevo answered with an error status; ``status`` and ``body`` carry what it said."""

    def __init__(self, status: int, body: str):
        super().__init__(f"Brevo API {status}: {body[:200]}")
        self.status = status
        self.body = body


def _send_via_brevo(from_email: str, to_email: str, subject: str,
                    text_content: str, html_content: Optional[str] = None) -> dict:
    """Send one email through Brevo's transactional API. Returns
    ``{"http_status", "message_id"}`` from Brevo's reply; raises BrevoSendError
    on a non-2xx status (the delivery log keeps the status either way)."""
    if not from_email:
        raise RuntimeError("No sender address — set ALERT_FROM_EMAIL to a Brevo-verified sender")
    payload = {
        "sender": {"name": os.environ.get("ALERT_FROM_NAME", "SF BWTF Alerts"), "email": from_email},
        "to": [{"email": to_email}],
        "subject": subject or "SF Beach Alert",
        "textContent": text_content,
    }
    if html_content:
        payload["htmlContent"] = html_content
    response = requests.post(
        BREVO_API_URL,
        json=payload,
        headers={"api-key": brevo_api_key(), "accept": "application/json"},
        timeout=HTTP_EMAIL_TIMEOUT_SECONDS,
    )
    if response.status_code >= 300:
        raise BrevoSendError(response.status_code, response.text)
    try:
        message_id = (response.json() or {}).get("messageId")
    except ValueError:
        message_id = None
    return {"http_status": response.status_code, "message_id": message_id}


class Notifier(ABC):
    """Base class for notification handlers"""
    
    @abstractmethod
    def send(self, alerts: list[Alert], report: str) -> bool:
        """
        Send notification with alerts.
        
        Args:
            alerts: List of Alert objects
            report: Formatted status report string
            
        Returns:
            True if notification sent successfully
        """
        pass
    
    def format_alert_summary(self, alerts: list[Alert]) -> str:
        """Format alerts into a summary string"""
        if not alerts:
            return "✅ No active water quality alerts"
        
        # Group alerts by type for clearer presentation
        rain_alerts = [a for a in alerts if a.alert_type == "rain_advisory"]
        cso_alerts = [a for a in alerts if a.alert_type == "cso_discharge"]
        bacteria_alerts = [a for a in alerts if a.alert_type == "elevated_bacteria"]
        other_alerts = [a for a in alerts if a.alert_type not in ("rain_advisory", "cso_discharge", "elevated_bacteria")]
        
        lines = [f"🚨 {len(alerts)} Water Quality Alert(s):"]
        
        if rain_alerts:
            lines.append("\n🌧️ Rain Advisory:")
            for alert in rain_alerts:
                lines.append(f"  • {alert.message}")
        
        if cso_alerts:
            lines.append("\n🚨 CSO (Sewer Overflow) Alerts:")
            for alert in cso_alerts:
                lines.append(f"  • {alert.message}")
        
        if bacteria_alerts:
            lines.append("\n⚠️ Elevated Bacteria:")
            for alert in bacteria_alerts:
                lines.append(f"  • {alert.message}")
        
        for alert in other_alerts:
            lines.append(f"\n• {alert.message}")
        
        return "\n".join(lines)


class ConsoleNotifier(Notifier):
    """Print alerts to console"""
    
    def send(self, alerts: list[Alert], report: str) -> bool:
        print("\n" + "=" * 60)
        print("WATER QUALITY ALERT NOTIFICATION")
        print("=" * 60)
        print(report)
        return True


class EmailNotifier(Notifier):
    """Send alerts via email"""
    
    def __init__(
        self,
        smtp_server: Optional[str] = None,
        smtp_port: int = 587,
        username: Optional[str] = None,
        password: Optional[str] = None,
        from_email: Optional[str] = None,
        to_emails: Optional[list[str]] = None
    ):
        self.smtp_server = smtp_server or os.environ.get("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = smtp_port or int(os.environ.get("SMTP_PORT", "587"))
        self.username = username or os.environ.get("SMTP_USERNAME")
        self.password = password or os.environ.get("SMTP_PASSWORD")
        self.from_email = from_email or os.environ.get("ALERT_FROM_EMAIL") or self.username
        self.last_error: Optional[str] = None  # set when send_message returns False
        
        to_env = os.environ.get("ALERT_TO_EMAILS", "")
        self.to_emails = to_emails or [e.strip() for e in to_env.split(",") if e.strip()]
    
    def send(self, alerts: list[Alert], report: str) -> bool:
        if not all([self.smtp_server, self.username, self.password, self.from_email, self.to_emails]):
            print("Email notifier not configured. Set SMTP_* and ALERT_* environment variables.")
            return False
        
        # Determine subject based on alert severity
        if any(a.severity == "warning" for a in alerts):
            subject = "🚨 URGENT: SF Beach Water Quality Warning"
        elif alerts:
            subject = "⚠️ SF Beach Water Quality Advisory"
        else:
            subject = "✅ SF Beach Water Quality - All Clear"
        
        # Create message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.from_email
        msg["To"] = ", ".join(self.to_emails)
        
        # Plain text version
        text_content = f"""
SF Beach Water Quality Alert
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}

{self.format_alert_summary(alerts)}

FULL REPORT:
{report}

---
Surfrider SF Blue Water Task Force
https://sf.surfrider.org/programs/blue-water-task-force
"""
        
        # HTML version
        summary_background = "rgba(255, 65, 0, 0.10)" if alerts else "rgba(37, 214, 112, 0.12)"
        html_content = f"""
<html>
<body style="margin:0; padding:24px; background:#f5f6f7; font-family:'Avenir Next','Trebuchet MS','Segoe UI',sans-serif; color:#26272a;">
    <div style="max-width:680px; margin:0 auto; background:#ffffff; border-radius:28px; overflow:hidden; box-shadow:0 18px 40px rgba(38,39,42,0.12);">
        <div style="background:linear-gradient(135deg, #26272a 0%, #317fb2 100%); padding:24px;">
            <div style="display:flex; gap:12px; flex-wrap:wrap; align-items:center; justify-content:space-between;">
                <div style="display:inline-block; background:rgba(255,255,255,0.12); color:#ffffff; border:1px solid rgba(255,255,255,0.16); border-radius:999px; padding:8px 12px; font-size:12px; font-weight:700; letter-spacing:0.12em; text-transform:uppercase;">
                    Surfrider SF Blue Water Task Force
                </div>
                <img src="{BWTF_LOGO_URL}" alt="Blue Water Task Force" style="display:block; width:180px; max-width:100%; height:auto;">
            </div>
            <h1 style="margin:18px 0 8px; color:#ffffff; font-size:32px; line-height:1; text-transform:uppercase; letter-spacing:0.03em;">SF Beach Water Quality Alert</h1>
            <p style="margin:0; color:rgba(255,255,255,0.82); font-size:15px;">Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
        </div>

        <div style="padding:24px;">
            <div style="background:#ffffff; border:1px solid #d9e4e8; border-radius:18px; padding:16px 18px; margin-bottom:20px;">
                <img src="{SURFRIDER_LOGO_URL}" alt="Surfrider Foundation" style="display:block; width:240px; max-width:100%; height:auto; margin-bottom:14px;">
                <p style="margin:0; color:#5e6a71; font-size:15px; line-height:1.5;">
                    Live status and bacteria reporting from the San Francisco Blue Water Task Force monitoring dashboard.
                </p>
            </div>

            <h2 style="margin:0 0 10px; color:#26272a; font-size:15px; letter-spacing:0.1em; text-transform:uppercase;">Alert Summary</h2>
            <div style="background:{summary_background}; border-radius:18px; padding:16px 18px; margin-bottom:20px;">
                {self._format_alerts_html(alerts)}
            </div>

            <h2 style="margin:0 0 10px; color:#26272a; font-size:15px; letter-spacing:0.1em; text-transform:uppercase;">Full Report</h2>
            <pre style="margin:0; background:#f7fafb; border:1px solid #d9e4e8; border-radius:18px; padding:18px; overflow-x:auto; white-space:pre-wrap; color:#26272a; font-size:13px; line-height:1.55;">{report}</pre>

            <div style="margin-top:22px; padding-top:18px; border-top:1px solid #d9e4e8;">
                <a href="https://sf.surfrider.org/programs/blue-water-task-force" style="display:inline-block; background:#317fb2; color:#ffffff; text-decoration:none; padding:12px 18px; border-radius:999px; font-weight:700;">
                    View Program Page
                </a>
                <p style="margin:16px 0 0; color:#5e6a71; font-size:12px; line-height:1.5;">
                    Surfrider SF Blue Water Task Force<br>
                    https://sf.surfrider.org/programs/blue-water-task-force
                </p>
            </div>
        </div>
    </div>
</body>
</html>
"""
        
        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))
        
        try:
            self._send_message(msg, self.to_emails)
            print(f"Email sent to {len(self.to_emails)} recipient(s)")
            return True
        except Exception as e:
            print(f"Failed to send email: {e}")
            return False
    
    def _format_alerts_html(self, alerts: list[Alert]) -> str:
        if not alerts:
            return "<p style='margin:0; color:#146b37; font-weight:700;'>No active water quality alerts</p>"
        
        html = "<ul>"
        for alert in alerts:
            color = "#ff4100" if alert.severity == "warning" else "#317fb2"
            html += f"<li style='color:{color}; margin:10px 0; line-height:1.5;'>{alert.message}</li>"
        html += "</ul>"
        return html

    def send_message(self, subject: str, text_content: str, to_emails: list[str], html_content: str | None = None) -> bool:
        """Send a custom email message to the provided recipients.

        Uses the Brevo HTTP API when BREVO_API_KEY is set (works on hosts that
        block SMTP); otherwise falls back to SMTP.
        """
        use_brevo = bool(brevo_api_key())
        if not to_emails:
            return False
        if not use_brevo and not all([self.smtp_server, self.username, self.password, self.from_email]):
            print("Email notifier not configured. Set BREVO_API_KEY or SMTP_* environment variables.")
            return False

        self.last_sends: list[dict] = []   # per-recipient outcome, read by the delivery log (015)
        try:
            if use_brevo:
                for to_email in to_emails:
                    try:
                        reply = _send_via_brevo(self.from_email, to_email, subject, text_content, html_content)
                    except Exception as exc:
                        self.last_sends.append({"to": to_email, "http_status": getattr(exc, "status", None),
                                                "message_id": None, "error": f"{type(exc).__name__}: {exc}"})
                        raise
                    self.last_sends.append({"to": to_email, **reply, "error": None})
            else:
                msg = MIMEMultipart("alternative")
                msg["Subject"] = subject
                msg["From"] = self.from_email
                msg["To"] = ", ".join(to_emails)
                msg.attach(MIMEText(text_content, "plain"))
                if html_content:
                    msg.attach(MIMEText(html_content, "html"))
                self._send_message(msg, to_emails)
            print(f"Email sent to {len(to_emails)} recipient(s) via {'brevo' if use_brevo else 'smtp'}")
            self.last_error = None
            return True
        except Exception as e:
            print(f"Failed to send email: {e}")
            self.last_error = f"{type(e).__name__}: {e}"
            return False

    def _send_message(self, msg: MIMEMultipart, recipients: list[str]) -> None:
        # timeout is load-bearing: smtplib's default is wait-forever, and a
        # host that firewalls outbound SMTP (silent packet drop) would hang
        # the calling thread — for the alert watcher, permanently.
        with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=SMTP_TIMEOUT_SECONDS) as server:
            server.starttls()
            server.login(self.username, self.password)
            server.sendmail(self.from_email, recipients, msg.as_string())


class SlackNotifier(Notifier):
    """Send alerts to Slack via webhook"""
    
    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL")
    
    def send(self, alerts: list[Alert], report: str) -> bool:
        if not self.webhook_url:
            print("Slack notifier not configured. Set SLACK_WEBHOOK_URL environment variable.")
            return False
        
        # Build Slack message blocks
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🏖️ SF Beach Water Quality Update",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Generated:* {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                }
            },
            {"type": "divider"}
        ]
        
        # Add alerts
        if alerts:
            for alert in alerts:
                emoji = "🚨" if alert.severity == "warning" else "⚠️"
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"{emoji} *{alert.station_name}*\n{alert.message}\n_Sample Date: {alert.sample_date.strftime('%Y-%m-%d')}_"
                    }
                })
        else:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "✅ *All stations within water quality standards*"
                }
            })
        
        # Add link to full report
        blocks.extend([
            {"type": "divider"},
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "📞 Current conditions: 1-877-SFBEACH | 🌐 <https://webapps.sfpuc.org/sapps/beachesandbay.html|SFPUC Beach Map>"
                    }
                ]
            }
        ])
        
        payload = {"blocks": blocks}
        
        try:
            response = requests.post(self.webhook_url, json=payload)
            response.raise_for_status()
            print("Slack notification sent")
            return True
        except Exception as e:
            print(f"Failed to send Slack notification: {e}")
            return False


class DiscordNotifier(Notifier):
    """Send alerts to Discord via webhook"""
    
    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL")
    
    def send(self, alerts: list[Alert], report: str) -> bool:
        if not self.webhook_url:
            print("Discord notifier not configured. Set DISCORD_WEBHOOK_URL environment variable.")
            return False
        
        # Determine embed color
        if any(a.severity == "warning" for a in alerts):
            color = 0xd32f2f  # Red
        elif alerts:
            color = 0xf57c00  # Orange
        else:
            color = 0x4caf50  # Green
        
        # Build Discord embed
        embed = {
            "title": "🏖️ SF Beach Water Quality Update",
            "description": self.format_alert_summary(alerts),
            "color": color,
            "timestamp": datetime.utcnow().isoformat(),
            "footer": {
                "text": "Surfrider SF Blue Water Task Force"
            },
            "fields": []
        }
        
        # Add alert details
        for alert in alerts[:5]:  # Limit to 5 to avoid hitting embed limits
            embed["fields"].append({
                "name": alert.station_name,
                "value": f"{alert.message}\nSample: {alert.sample_date.strftime('%Y-%m-%d')}",
                "inline": False
            })
        
        if len(alerts) > 5:
            embed["fields"].append({
                "name": "Additional Alerts",
                "value": f"...and {len(alerts) - 5} more alerts",
                "inline": False
            })
        
        payload = {
            "embeds": [embed],
            "content": "📢 **Water Quality Alert**" if alerts else None
        }
        
        try:
            response = requests.post(self.webhook_url, json=payload)
            response.raise_for_status()
            print("Discord notification sent")
            return True
        except Exception as e:
            print(f"Failed to send Discord notification: {e}")
            return False


class TwilioSMSNotifier(Notifier):
    """Send SMS alerts via Twilio"""
    
    def __init__(
        self,
        account_sid: Optional[str] = None,
        auth_token: Optional[str] = None,
        from_number: Optional[str] = None,
        to_numbers: Optional[list[str]] = None
    ):
        self.account_sid = account_sid or os.environ.get("TWILIO_ACCOUNT_SID")
        self.auth_token = auth_token or os.environ.get("TWILIO_AUTH_TOKEN")
        self.from_number = from_number or os.environ.get("TWILIO_FROM_NUMBER")
        
        to_env = os.environ.get("TWILIO_TO_NUMBERS", "")
        self.to_numbers = to_numbers or [n.strip() for n in to_env.split(",") if n.strip()]
    
    def send(self, alerts: list[Alert], report: str) -> bool:
        if not all([self.account_sid, self.auth_token, self.from_number, self.to_numbers]):
            print("Twilio notifier not configured. Set TWILIO_* environment variables.")
            return False
        
        # SMS should be concise
        if not alerts:
            message = "✅ SF Beach Water Quality: All stations within standards. Check https://webapps.sfpuc.org/sapps/beachesandbay.html"
        else:
            message = f"🚨 SF Beach Alert: {len(alerts)} station(s) with elevated bacteria. "
            # Add first alert location
            message += f"Including: {alerts[0].station_name}. "
            message += "Call 1-877-SFBEACH for details."
        
        # Truncate if too long
        if len(message) > 160:
            message = message[:157] + "..."
        
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        
        success = True
        for to_number in self.to_numbers:
            try:
                self._send_to_number(to_number, message)
                print(f"SMS sent to {to_number}")
            except Exception as e:
                print(f"Failed to send SMS to {to_number}: {e}")
                success = False
        
        return success

    def send_message(self, message: str) -> bool:
        """Send a custom SMS message to the configured recipient list."""
        if not all([self.account_sid, self.auth_token, self.from_number, self.to_numbers]):
            print("Twilio notifier not configured. Set TWILIO_* environment variables.")
            return False

        success = True
        for to_number in self.to_numbers:
            try:
                self._send_to_number(to_number, message)
                print(f"SMS sent to {to_number}")
            except Exception as e:
                print(f"Failed to send SMS to {to_number}: {e}")
                success = False

        return success

    def _send_to_number(self, to_number: str, message: str) -> None:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        response = requests.post(
            url,
            auth=(self.account_sid, self.auth_token),
            data={
                "From": self.from_number,
                "To": to_number,
                "Body": message,
            }
        )
        response.raise_for_status()


class EmailToSMSNotifier(Notifier):
    """
    Send SMS alerts via email-to-SMS gateways (FREE!)
    
    Carrier gateways:
    - Verizon: number@vtext.com
    - AT&T: number@txt.att.net
    - T-Mobile: number@tmomail.net
    - Sprint: number@messaging.sprintpcs.com
    
    Requires Gmail (or other SMTP) credentials.
    """
    
    CARRIER_GATEWAYS = {
        "verizon": "vtext.com",
        "att": "txt.att.net",
        "tmobile": "tmomail.net",
        "sprint": "messaging.sprintpcs.com",
        "cricket": "sms.cricketwireless.net",
        "metropcs": "mymetropcs.com",
        "uscellular": "email.uscc.net",
    }
    
    def __init__(
        self,
        smtp_server: Optional[str] = None,
        smtp_port: int = 587,
        username: Optional[str] = None,
        password: Optional[str] = None,
        from_email: Optional[str] = None,
        to_sms_emails: Optional[list[str]] = None
    ):
        self.smtp_server = smtp_server or os.environ.get("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = smtp_port or int(os.environ.get("SMTP_PORT", "587"))
        self.username = username or os.environ.get("SMTP_USERNAME")
        self.password = password or os.environ.get("SMTP_PASSWORD")
        self.from_email = from_email or os.environ.get("ALERT_FROM_EMAIL") or os.environ.get("SMTP_USERNAME")
        self.last_error: Optional[str] = None  # set when send_message returns False

        # SMS gateway emails (e.g., "9166226075@vtext.com")
        to_env = os.environ.get("SMS_GATEWAY_EMAILS", "")
        self.to_sms_emails = to_sms_emails or [e.strip() for e in to_env.split(",") if e.strip()]
    
    @classmethod
    def phone_to_gateway(cls, phone: str, carrier: str) -> str:
        """Convert phone number and carrier to gateway email"""
        # Strip non-digits
        phone = ''.join(c for c in phone if c.isdigit())
        # Remove leading 1 if present
        if phone.startswith('1') and len(phone) == 11:
            phone = phone[1:]
        
        carrier = carrier.lower().replace("-", "").replace(" ", "")
        gateway = cls.CARRIER_GATEWAYS.get(carrier)
        
        if not gateway:
            raise ValueError(f"Unknown carrier: {carrier}. Supported: {list(cls.CARRIER_GATEWAYS.keys())}")
        
        return f"{phone}@{gateway}"
    
    def send(self, alerts: list[Alert], report: str) -> bool:
        if not all([self.smtp_server, self.username, self.password, self.to_sms_emails]):
            print("Email-to-SMS notifier not configured.")
            print("Set SMTP_USERNAME, SMTP_PASSWORD, and SMS_GATEWAY_EMAILS environment variables.")
            return False
        
        # Build message with CSO sites and rain advisory
        if not alerts:
            message = "SF Beach: All clear! No water quality alerts."
        else:
            rain_alerts = [a for a in alerts if a.alert_type == "rain_advisory"]
            cso_alerts = [a for a in alerts if "CSO" in a.message.upper() or "sewer" in a.message.lower()]
            
            if cso_alerts:
                cso_sites = [a.station_name for a in cso_alerts]
                message = f"🚨 SF Beach CSO Alert ({len(cso_sites)} sites):\n"
                message += "\n".join(f"• {site}" for site in cso_sites)
                message += "\n\nAvoid water contact. 1-877-SFBEACH"
            elif rain_alerts:
                message = f"🌧️ SF Beach Rain Advisory: Avoid water contact 72hrs after rain. "
                cso_risk = rain_alerts[0].details.get("cso_risk", "unknown")
                message += f"CSO risk: {cso_risk}. 1-877-SFBEACH"
            else:
                message = f"SF Beach Alert: {len(alerts)} station(s) with high bacteria. 1-877-SFBEACH"
        
        # Create simple plain text message (SMS gateways don't support HTML)
        msg = MIMEText(message)
        msg["From"] = self.from_email
        msg["Subject"] = ""  # Keep subject empty for cleaner SMS
        
        success = True
        for sms_email in self.to_sms_emails:
            try:
                self._send_to_email(sms_email, message)
                print(f"SMS sent to {sms_email}")
            except Exception as e:
                print(f"Failed to send SMS to {sms_email}: {e}")
                success = False
        
        return success

    def send_message(self, message: str) -> bool:
        """Send a custom plain-text message to the configured gateway recipients."""
        use_brevo = bool(brevo_api_key())
        if not self.to_sms_emails:
            return False
        if not use_brevo and not all([self.smtp_server, self.username, self.password]):
            print("Email-to-SMS notifier not configured.")
            print("Set BREVO_API_KEY or SMTP_USERNAME/SMTP_PASSWORD environment variables.")
            return False

        success = True
        self.last_sends: list[dict] = []   # per-recipient outcome, read by the delivery log (015)
        for sms_email in self.to_sms_emails:
            try:
                reply = self._send_to_email(sms_email, message) or {}
                print(f"SMS sent to {sms_email}")
                self.last_error = None
                self.last_sends.append({"to": sms_email, "http_status": reply.get("http_status"),
                                        "message_id": reply.get("message_id"), "error": None})
            except Exception as e:
                print(f"Failed to send SMS to {sms_email}: {e}")
                self.last_error = f"{type(e).__name__}: {e}"
                self.last_sends.append({"to": sms_email, "http_status": getattr(e, "status", None),
                                        "message_id": None, "error": self.last_error})
                success = False

        return success

    def _send_to_email(self, sms_email: str, message: str) -> Optional[dict]:
        if brevo_api_key():
            # Carrier gateways render the subject inline, so keep it blank-ish.
            return _send_via_brevo(self.from_email, sms_email, " ", message)
            return
        msg = MIMEText(message)
        msg["From"] = self.from_email
        msg["To"] = sms_email
        msg["Subject"] = ""
        with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=SMTP_TIMEOUT_SECONDS) as server:
            server.starttls()
            server.login(self.username, self.password)
            server.sendmail(self.from_email, [sms_email], msg.as_string())


class MultiNotifier(Notifier):
    """Send alerts through multiple notification channels"""
    
    def __init__(self, notifiers: list[Notifier]):
        self.notifiers = notifiers
    
    def send(self, alerts: list[Alert], report: str) -> bool:
        results = []
        for notifier in self.notifiers:
            try:
                result = notifier.send(alerts, report)
                results.append(result)
            except Exception as e:
                print(f"Error with {notifier.__class__.__name__}: {e}")
                results.append(False)
        
        return all(results)


def create_notifier_from_env() -> Notifier:
    """
    Create a multi-notifier based on available environment variables.
    
    Returns a MultiNotifier with all configured notification channels.
    """
    notifiers = [ConsoleNotifier()]  # Always include console
    
    # Check for Slack
    if os.environ.get("SLACK_WEBHOOK_URL"):
        notifiers.append(SlackNotifier())
    
    # Check for Discord
    if os.environ.get("DISCORD_WEBHOOK_URL"):
        notifiers.append(DiscordNotifier())
    
    # Check for Email
    if os.environ.get("SMTP_USERNAME") and os.environ.get("ALERT_TO_EMAILS"):
        notifiers.append(EmailNotifier())
    
    # Check for Twilio
    if os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_TO_NUMBERS"):
        notifiers.append(TwilioSMSNotifier())
    
    # Check for Email-to-SMS (free!)
    if os.environ.get("SMTP_USERNAME") and os.environ.get("SMS_GATEWAY_EMAILS"):
        notifiers.append(EmailToSMSNotifier())
    
    return MultiNotifier(notifiers)
