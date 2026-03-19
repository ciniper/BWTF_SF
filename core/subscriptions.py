#!/usr/bin/env python3
"""Persistence for site alert subscriptions."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
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
        self.path = path or SUBSCRIPTIONS_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)

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
        rows = self._load_raw()
        subscriptions = [
            SiteSubscription(
                email=row.get("email", ""),
                phone_number=row.get("phone_number", ""),
                carrier=row.get("carrier", ""),
                station_ids=sorted(set(row.get("station_ids", []))),
                created_at=row.get("created_at", ""),
                updated_at=row.get("updated_at", ""),
            )
            for row in rows
        ]
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

    def delete_subscription(self, email: str) -> bool:
        normalized_email = normalize_email(email)
        rows = self._load_raw()
        filtered = [row for row in rows if row.get("email") != normalized_email]
        self._save_raw(filtered)
        return len(filtered) != len(rows)
