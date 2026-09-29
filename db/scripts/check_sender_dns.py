#!/usr/bin/env python3
"""Sender-domain check for the Brevo alert path.

    venv/bin/python db/scripts/check_sender_dns.py [domain]

Without an argument it reads the domain off watcher_config.alert_from_email
(what the pg dispatcher sends from). Looks up, with dig:

  * brevo1._domainkey / brevo2._domainkey  CNAME  — Brevo's DKIM (Manual records)
  * brevo-code                             TXT    — Brevo's domain verification
  * _dmarc                                 TXT    — exactly ONE record. GoDaddy
    plants a default `p=quarantine … onsecureserver.net` DMARC; adding Brevo's
    then leaves two, which receivers treat as no DMARC at all (Surftober hit
    this 2026-09-06). Delete the registrar's.

Read-only. Exit 0 when every check passes, 1 otherwise. A gmail.com (or any
mailbox-provider) sender can never pass: Brevo cannot sign it, so it rewrites
the From to a shared brevosend.com address — see DEPLOY.md "Sender domain".
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

MAILBOX_PROVIDERS = {"gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "me.com"}


def dig(name: str, rtype: str) -> list[str]:
    out = subprocess.run(["dig", "+short", rtype, name], capture_output=True, text=True, timeout=20).stdout
    return [line.strip().strip('"') for line in out.splitlines() if line.strip()]


def sender_domain() -> str | None:
    try:
        from shared import supabase as sb
        rows = sb.select("watcher_config", {"select": "value", "key": "eq.alert_from_email"})
        addr = (rows[0].get("value") or "") if rows else ""
        return addr.split("@")[-1].lower() if "@" in addr else None
    except Exception as exc:  # noqa: BLE001
        print(f"(could not read watcher_config.alert_from_email: {exc})")
        return None


def main() -> int:
    domain = (sys.argv[1] if len(sys.argv) > 1 else sender_domain() or "").strip().lower().rstrip(".")
    if not domain:
        print("usage: check_sender_dns.py <domain>   (or set watcher_config.alert_from_email)")
        return 1
    print(f"sender domain: {domain}")
    ok = True
    if domain in MAILBOX_PROVIDERS:
        print(f"FAIL  {domain} is a mailbox provider — Brevo cannot authenticate it; alerts go out 'via' brevosend.com")
        return 1

    for host in ("brevo1._domainkey", "brevo2._domainkey"):
        cn = dig(f"{host}.{domain}", "CNAME")
        good = any("brevo" in c or "sendinblue" in c for c in cn)
        ok &= good
        print(f"{'PASS' if good else 'FAIL'}  {host}.{domain} CNAME → {cn or 'none'}")

    code = dig(f"brevo-code.{domain}", "TXT") or [t for t in dig(domain, "TXT") if "brevo-code" in t]
    ok &= bool(code)
    print(f"{'PASS' if code else 'FAIL'}  brevo-code TXT → {code or 'none'}")

    dmarc = dig(f"_dmarc.{domain}", "TXT")
    if len(dmarc) == 1 and dmarc[0].startswith("v=DMARC1"):
        print(f"PASS  _dmarc.{domain} TXT → {dmarc[0]}")
    elif not dmarc:
        ok = False
        print(f"FAIL  _dmarc.{domain} TXT → none (add Brevo's: v=DMARC1; p=none; rua=mailto:rua@dmarc.brevo.com)")
    else:
        ok = False
        print(f"FAIL  _dmarc.{domain} has {len(dmarc)} TXT records — receivers treat that as NO DMARC; keep exactly one:")
        for d in dmarc:
            print(f"        {d}")

    spf = [t for t in dig(domain, "TXT") if t.startswith("v=spf1")]
    print(f"info  SPF → {spf[0] if spf else 'none (Brevo signs with DKIM; SPF optional in its current flow)'}")
    print("\nALL PASS" if ok else "\nNOT READY — fix the FAIL lines, then re-run")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
