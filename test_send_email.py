#!/usr/bin/env python3
"""
test_send_email.py — Send a realistic fake prospect email to your own inbox.

Usage:
  python test_send_email.py                        # default scenario: new_lead
  python test_send_email.py --scenario interested
  python test_send_email.py --list
"""

from __future__ import annotations

import argparse
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

import os

# ── Scenario definitions ─────────────────────────────────────────────────────

SCENARIOS: dict[str, dict[str, str]] = {
    "new_lead": {
        "subject": "Inquiry about your services",
        "body": (
            "Hi,\n\n"
            "I came across your company while researching solutions for our team.\n"
            "We're a 50-person SaaS company and I'm exploring options to streamline our "
            "sales outreach. Could you tell me a bit more about what you offer and whether "
            "it would be a good fit for a team our size?\n\n"
            "Thanks,\n"
            "Alex Johnson\n"
            "Head of Sales, Acme Corp"
        ),
        "stage_hint": "Prospect/01-New Lead",
    },
    "interested": {
        "subject": "Re: Following up on our earlier chat",
        "body": (
            "Hi,\n\n"
            "Thanks for the overview last week — it was really helpful.\n"
            "I've shared your materials with our VP and she's keen to learn more. "
            "We're particularly interested in the automation features and the CRM integrations. "
            "Could you walk us through how those work in practice? We handle around 200 "
            "leads a month so scale matters for us.\n\n"
            "Looking forward to hearing from you.\n\n"
            "Best,\n"
            "Alex Johnson"
        ),
        "stage_hint": "Prospect/03-Interested",
    },
    "demo_request": {
        "subject": "Can we book a demo?",
        "body": (
            "Hi,\n\n"
            "After reviewing the information you sent, we'd love to see the platform in action. "
            "Is it possible to schedule a 30-minute demo this week or next? "
            "Our team is available Tuesday and Thursday afternoons (GMT+1).\n\n"
            "Also, do you support SSO? That's a hard requirement on our end.\n\n"
            "Thanks,\n"
            "Alex Johnson\n"
            "Acme Corp"
        ),
        "stage_hint": "Prospect/04-Demo Requested",
    },
    "negotiating": {
        "subject": "Proposal feedback — pricing question",
        "body": (
            "Hi,\n\n"
            "We've reviewed the proposal and we're very close to a decision. "
            "The platform looks great but the annual price is a bit above our current budget. "
            "Is there any flexibility on the per-seat cost if we commit to a 2-year contract? "
            "We'd also need the onboarding support package included — is that something we "
            "could factor into the deal?\n\n"
            "Happy to jump on a call to discuss.\n\n"
            "Best regards,\n"
            "Alex Johnson"
        ),
        "stage_hint": "Prospect/06-Negotiating",
    },
}

# ── CLI ──────────────────────────────────────────────────────────────────────

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send a fake prospect email to your own inbox for testing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python test_send_email.py                        # new_lead scenario
  python test_send_email.py --scenario interested
  python test_send_email.py --scenario demo_request
  python test_send_email.py --scenario negotiating
  python test_send_email.py --list
        """,
    )
    parser.add_argument(
        "--scenario",
        choices=list(SCENARIOS.keys()),
        default="new_lead",
        help="Scenario to send (default: new_lead).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all available scenarios and exit.",
    )
    return parser.parse_args(argv)


# ── Email sending ─────────────────────────────────────────────────────────────

def send_test_email(scenario_key: str) -> None:
    email_address = os.getenv("EMAIL_ADDRESS", "")
    email_password = os.getenv("EMAIL_PASSWORD", "")
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_use_tls = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
    sender_name = os.getenv("SENDER_NAME", "")

    if not email_address or not email_password:
        print("ERROR: EMAIL_ADDRESS and EMAIL_PASSWORD must be set in .env", file=sys.stderr)
        sys.exit(1)

    scenario = SCENARIOS[scenario_key]
    subject = f"[TEST] {scenario['subject']}"
    body = scenario["body"]
    stage_hint = scenario["stage_hint"]

    # Build MIME message — send to ourselves
    msg = MIMEMultipart("alternative")
    sender_display = sender_name or email_address
    msg["From"] = f"Test Prospect <{email_address}>"
    msg["To"] = email_address
    msg["Subject"] = subject
    msg["X-Test-Scenario"] = scenario_key
    msg["X-Expected-Stage"] = stage_hint
    msg["Date"] = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # Send via SMTP
    if smtp_use_tls:
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
        server.ehlo()
        server.starttls()
        server.ehlo()
    else:
        server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30)

    with server:
        server.login(email_address, email_password)
        server.sendmail(email_address, [email_address], msg.as_bytes())

    print(f"✓ Sent scenario '{scenario_key}' to {email_address}")
    print(f"  Subject : {subject}")
    print(f"  Expected stage hint: {stage_hint}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.list:
        print("Available scenarios:")
        for key, data in SCENARIOS.items():
            print(f"  {key:<16} — {data['subject']}  (hint: {data['stage_hint']})")
        return 0

    try:
        send_test_email(args.scenario)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
