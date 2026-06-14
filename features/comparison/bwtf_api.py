#!/usr/bin/env python3
"""
Surfrider Blue Water Task Force (BWTF) data client — San Francisco chapter.

Source: the public BWTF water-quality database that powers bwtf.surfrider.org
and the chapter widget embedded at
https://sf.surfrider.org/programs/blue-water-task-force

It is an AWS AppSync GraphQL API. The San Francisco chapter is **lab id 76**.
This client uses the same public (API-key) GraphQL operations the chapter
website's front-end widget uses:

  - getLab(id)                              -> sites + each site's latest sample
  - waterQualityDataByLabAndCollectionTime  -> historical samples for the lab

BWTF measures **Enterococcus** (MPN/100mL) and grades it against the California
State Water Resources Control Board thresholds (lower 36, upper / single-sample
max 104) — the same standard the city uses, which is what makes the two
sources directly comparable.

Note: the GraphQL endpoint and API key below are the public values shipped in
the chapter website's JavaScript. Like the SFPUC LIMS feed this project already
relies on, this is an undocumented endpoint and could change without notice.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests

# Public BWTF AppSync GraphQL endpoint + API key (shipped in the website front-end)
BWTF_GRAPHQL_URL = "https://esvbxbhkmzgh5ojw3g2hvsx7du.appsync-api.us-west-2.amazonaws.com/graphql"
BWTF_API_KEY = "da2-vnfkf5zembhljj6lwoshzhiodm"

# Surfrider San Francisco chapter lab id
SF_LAB_ID = 76

# The indicator BWTF reports for SF marine sites
ENTERO_SUBSTANCE = "Enterococcus"

# Public report/explore links (per site) on the BWTF site
BWTF_REPORT_URL = "https://bwtf.surfrider.org/report/{lab_id}/{site_id}"


def parse_result(raw) -> tuple[str, Optional[float]]:
    """Return (display_string, numeric_value) for a BWTF/lab result.

    Handles below-detection-limit ("<10") and above-range (">2419") notation
    the same way the city monitor does — below-detection uses half the limit.
    """
    if raw is None:
        return "", None
    s = str(raw).strip()
    if not s:
        return "", None
    try:
        if s.startswith("<"):
            return s, float(s[1:]) / 2
        if s.startswith(">"):
            return s, float(s[1:])
        return s, float(s)
    except ValueError:
        return s, None


def parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse a BWTF ISO collectionTime (e.g. '2026-06-12T01:10:00.000Z')."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.replace(tzinfo=None)  # normalize to naive for easy comparison
    except ValueError:
        try:
            return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None


@dataclass
class BWTFSample:
    """A single analyte result within a BWTF sampling event."""
    collection_time: Optional[datetime]
    substance: str
    result_raw: str
    result_value: Optional[float]
    units: str = ""


@dataclass
class BWTFSite:
    """A BWTF monitoring location and its most recent sample."""
    site_id: str
    name: str
    latitude: Optional[float]
    longitude: Optional[float]
    latest_time: Optional[datetime] = None
    entero_raw: Optional[str] = None
    entero_value: Optional[float] = None
    samples: list[BWTFSample] = field(default_factory=list)

    @property
    def report_url(self) -> str:
        return BWTF_REPORT_URL.format(lab_id=SF_LAB_ID, site_id=self.site_id)


@dataclass
class BWTFLab:
    """A BWTF chapter lab, its grading thresholds, and its sites."""
    lab_id: int
    name: str
    jurisdiction: str
    entero_lower: Optional[float]
    entero_upper: Optional[float]
    sites: list[BWTFSite] = field(default_factory=list)


_GET_LAB_QUERY = """
query GetLab($id: ID!) {
  getLab(id: $id) {
    id
    name
    jurisdiction
    limits {
      entero { agency lowerThreshold upperThreshold }
      ecoli  { agency lowerThreshold upperThreshold }
    }
    locations {
      items {
        id
        name
        coordinate { latitude longitude }
        latestSample {
          collectionTime
          samples { substance result units method modifier }
        }
      }
    }
  }
}
"""

_HISTORY_QUERY = """
query History($lab: Int, $sort: ModelSortDirection, $limit: Int, $nextToken: String) {
  waterQualityDataByLabAndCollectionTime(
    lab: $lab, sortDirection: $sort, limit: $limit, nextToken: $nextToken
  ) {
    items {
      collectionTime
      location { id name }
      samples { substance result units }
    }
    nextToken
  }
}
"""


class SFBWTFClient:
    """Fetches San Francisco Blue Water Task Force results from the public API."""

    def __init__(self, lab_id: int = SF_LAB_ID, api_key: str = BWTF_API_KEY,
                 url: str = BWTF_GRAPHQL_URL, timeout: int = 30):
        self.lab_id = lab_id
        self.url = url
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _graphql(self, query: str, variables: dict) -> Optional[dict]:
        try:
            response = self.session.post(
                self.url,
                json={"query": query, "variables": variables},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            print(f"Error contacting BWTF API: {exc}")
            return None
        if payload.get("errors"):
            print(f"BWTF API returned errors: {payload['errors']}")
            return None
        return payload.get("data")

    def fetch_lab(self) -> Optional[BWTFLab]:
        """Fetch the SF lab, its Enterococcus thresholds, and every site's latest sample."""
        data = self._graphql(_GET_LAB_QUERY, {"id": str(self.lab_id)})
        if not data or not data.get("getLab"):
            return None
        lab = data["getLab"]
        limits = (lab.get("limits") or {}).get("entero") or {}

        sites: list[BWTFSite] = []
        for loc in (lab.get("locations") or {}).get("items", []):
            if not loc:
                continue
            coord = loc.get("coordinate") or {}
            latest = loc.get("latestSample") or {}
            latest_time = parse_datetime(latest.get("collectionTime"))

            samples: list[BWTFSample] = []
            entero_raw = entero_value = None
            for s in latest.get("samples") or []:
                raw, value = parse_result(s.get("result"))
                sample = BWTFSample(
                    collection_time=latest_time,
                    substance=s.get("substance", ""),
                    result_raw=raw,
                    result_value=value,
                    units=s.get("units") or "",
                )
                samples.append(sample)
                if sample.substance == ENTERO_SUBSTANCE:
                    entero_raw, entero_value = raw, value

            sites.append(BWTFSite(
                site_id=str(loc.get("id", "")),
                name=loc.get("name", ""),
                latitude=_as_float(coord.get("latitude")),
                longitude=_as_float(coord.get("longitude")),
                latest_time=latest_time,
                entero_raw=entero_raw,
                entero_value=entero_value,
                samples=samples,
            ))

        sites.sort(key=lambda x: x.name)
        return BWTFLab(
            lab_id=self.lab_id,
            name=lab.get("name", ""),
            jurisdiction=lab.get("jurisdiction", ""),
            entero_lower=_as_float(limits.get("lowerThreshold")),
            entero_upper=_as_float(limits.get("upperThreshold")),
            sites=sites,
        )

    def fetch_history(self, since: Optional[datetime] = None,
                      page_size: int = 200, max_pages: int = 15) -> list[dict]:
        """Return historical samples for the lab, newest first.

        If ``since`` is given, paginating stops once results are older than it.
        Each item: {"site_name", "collection_time", "substance", "result_value", "result_raw"}.
        """
        history: list[dict] = []
        next_token = None
        for _ in range(max_pages):
            variables = {"lab": self.lab_id, "sort": "DESC", "limit": page_size, "nextToken": next_token}
            data = self._graphql(_HISTORY_QUERY, variables)
            if not data:
                break
            conn = data.get("waterQualityDataByLabAndCollectionTime") or {}
            reached_cutoff = False
            for it in conn.get("items", []):
                if not it:
                    continue
                when = parse_datetime(it.get("collectionTime"))
                if since and when and when < since:
                    reached_cutoff = True
                    continue
                site_name = (it.get("location") or {}).get("name", "")
                for s in it.get("samples") or []:
                    raw, value = parse_result(s.get("result"))
                    history.append({
                        "site_name": site_name,
                        "collection_time": when,
                        "substance": s.get("substance", ""),
                        "result_value": value,
                        "result_raw": raw,
                    })
            next_token = conn.get("nextToken")
            if reached_cutoff or not next_token:
                break
        return history


def _as_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main():
    """Quick manual check."""
    client = SFBWTFClient()
    lab = client.fetch_lab()
    if not lab:
        print("Could not fetch BWTF lab data.")
        return
    print(f"Lab {lab.lab_id}: {lab.name} ({lab.jurisdiction})")
    print(f"Enterococcus thresholds: lower={lab.entero_lower} upper(SSM)={lab.entero_upper}")
    print(f"{len(lab.sites)} sites:\n")
    for site in lab.sites:
        when = site.latest_time.strftime("%Y-%m-%d") if site.latest_time else "—"
        print(f"  {site.name:32s} Entero={site.entero_raw or '—':>5} (sampled {when})")


if __name__ == "__main__":
    main()
