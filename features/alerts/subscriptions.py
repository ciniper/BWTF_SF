#!/usr/bin/env python3
"""Persistence for site alert subscriptions.

Primary backend is Supabase (``subscribers`` table — survives redeploys);
when Supabase env isn't configured, falls back to the legacy ``data/`` JSON
file so a bare dev checkout still works. Passing an explicit ``path`` forces
the JSON backend (used by tests).

Deletes are soft (``active=false``) so the row history survives; upserting a
matching email/phone reactivates the row.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from shared import supabase as sb
from shared.paths import DATA_DIR

SUBSCRIPTIONS_PATH = DATA_DIR / "subscriptions.json"


@dataclass
class SiteSubscription:
    email: str
    phone_number: str
    carrier: str
    station_ids: list[str]
    created_at: str
    updated_at: str


def normalize_email(email: str) -> str:
    value = email.strip().lower()
    if not value or "@" not in value:
        raise ValueError("Enter a valid email address.")
    return value


def normalize_phone_number(phone_number: str) -> str:
    if not phone_number.strip():
        return ""
    digits = "".join(ch for ch in phone_number if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError("Phone number must contain 10 digits (or 11 with leading 1).")
    return f"+1{digits}"


class SubscriptionStore:
    def __init__(self, path: Path | None = None):
        # Explicit path -> JSON backend (tests); otherwise Supabase when configured.
        self._remote = path is None and sb.is_configured()
        self.path = path or SUBSCRIPTIONS_PATH
        if not self._remote:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _from_row(row: dict) -> SiteSubscription:
        return SiteSubscription(
            email=row.get("email", ""),
            phone_number=row.get("phone_number", ""),
            carrier=row.get("carrier", ""),
            station_ids=sorted(set(row.get("station_ids", []))),
            created_at=row.get("created_at", ""),
            updated_at=row.get("updated_at", ""),
        )

    def _remote_candidates(self, email: str, phone: str) -> list[dict]:
        """Rows matching the email OR the phone (regardless of active flag),
        mirroring the JSON upsert's match rule."""
        rows: dict[str, dict] = {}
        if email:
            for row in sb.select("subscribers", {"select": "*", "email": f"eq.{email}"}):
                rows[row["id"]] = row
        if phone:
            for row in sb.select("subscribers", {"select": "*", "phone_number": f"eq.{phone}"}):
                rows[row["id"]] = row
        return list(rows.values())

    def _mirror_to_json(self) -> None:
        """Warm-backup shadow: after every remote write, snapshot the active
        subscriber list to the legacy JSON file (on the Railway volume).

        Required for the free-tier period (decision 2026-08-15): the free plan
        has no database backups and subscribers are PII, so the JSON file
        stays a restorable copy — and the JSON fallback path can read it
        as-is. Best-effort by design: a mirror failure must never break a
        subscription write. Retire when BWTF's project moves to the Pro org.
        """
        try:
            rows = sb.select("subscribers", {"select": "*", "active": "eq.true"})
            snapshot = [{
                "email": row.get("email", ""),
                "phone_number": row.get("phone_number", ""),
                "carrier": row.get("carrier", ""),
                "station_ids": sorted(set(row.get("station_ids", []))),
                "created_at": row.get("created_at", ""),
                "updated_at": row.get("updated_at", ""),
            } for row in rows]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(snapshot, indent=2))
        except Exception as exc:
            print(f"[subscriptions] JSON mirror failed (non-fatal): {exc}")

    def _load_raw(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text() or "[]")
        except json.JSONDecodeError:
            return []

    def _save_raw(self, payload: list[dict]) -> None:
        self.path.write_text(json.dumps(payload, indent=2))

    def list_subscriptions(self) -> list[SiteSubscription]:
        if self._remote:
            rows = sb.select("subscribers", {"select": "*", "active": "eq.true"})
        else:
            rows = self._load_raw()
        subscriptions = [self._from_row(row) for row in rows]
        return sorted(subscriptions, key=lambda item: (item.email or item.phone_number, item.phone_number))

    def upsert_subscription(
        self,
        email: str,
        station_ids: list[str],
        phone_number: str = "",
        carrier: str = "",
    ) -> SiteSubscription:
        normalized_email = normalize_email(email)
        normalized_phone = normalize_phone_number(phone_number)
        normalized_carrier = carrier.strip().lower().replace("-", "").replace(" ", "")
        if normalized_carrier and not normalized_phone:
            raise ValueError("Add a phone number before choosing an SMS carrier.")
        normalized_station_ids = sorted({station_id for station_id in station_ids if station_id})
        if not normalized_station_ids:
            raise ValueError("Select at least one site.")

        if self._remote:
            candidates = self._remote_candidates(normalized_email, normalized_phone)
            patch = {
                "email": normalized_email,
                "phone_number": normalized_phone,
                "carrier": normalized_carrier,
                "station_ids": normalized_station_ids,
                "active": True,
            }
            if candidates:
                updated = []
                for row in candidates:
                    updated.extend(sb.update("subscribers", {"id": f"eq.{row['id']}"}, patch))
                row = updated[0]
            else:
                row = sb.insert("subscribers", [patch], returning=True)[0]
            self._mirror_to_json()
            return self._from_row(row)

        now = datetime.utcnow().isoformat()
        rows = self._load_raw()
        updated_rows = []
        subscription = None

        for row in rows:
            row_email = row.get("email", "")
            row_phone = row.get("phone_number", "")
            if row_email == normalized_email or (normalized_phone and row_phone == normalized_phone):
                row["email"] = normalized_email
                row["phone_number"] = normalized_phone
                row["carrier"] = normalized_carrier
                row["station_ids"] = normalized_station_ids
                row["updated_at"] = now
                subscription = SiteSubscription(
                    email=normalized_email,
                    phone_number=normalized_phone,
                    carrier=normalized_carrier,
                    station_ids=normalized_station_ids,
                    created_at=row.get("created_at", now),
                    updated_at=now,
                )
            updated_rows.append(row)

        if subscription is None:
            subscription = SiteSubscription(
                email=normalized_email,
                phone_number=normalized_phone,
                carrier=normalized_carrier,
                station_ids=normalized_station_ids,
                created_at=now,
                updated_at=now,
            )
            updated_rows.append(asdict(subscription))

        self._save_raw(updated_rows)
        return subscription

    def delete_subscription(self, email: str = "", phone_number: str = "") -> bool:
        normalized_email = normalize_email(email) if email.strip() else ""
        normalized_phone = normalize_phone_number(phone_number) if phone_number.strip() else ""
        if not normalized_email and not normalized_phone:
            raise ValueError("Provide an email or phone number to delete a subscription.")

        if self._remote:
            deactivated = 0
            if normalized_email:
                deactivated += len(sb.update(
                    "subscribers",
                    {"email": f"eq.{normalized_email}", "active": "eq.true"},
                    {"active": False},
                ))
            if normalized_phone:
                deactivated += len(sb.update(
                    "subscribers",
                    {"phone_number": f"eq.{normalized_phone}", "active": "eq.true"},
                    {"active": False},
                ))
            if deactivated:
                self._mirror_to_json()
            return deactivated > 0

        rows = self._load_raw()
        filtered = []
        for row in rows:
            email_matches = normalized_email and row.get("email", "") == normalized_email
            phone_matches = normalized_phone and row.get("phone_number", "") == normalized_phone
            if email_matches or phone_matches:
                continue
            filtered.append(row)
        self._save_raw(filtered)
        return len(filtered) != len(rows)
