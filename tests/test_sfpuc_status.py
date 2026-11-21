"""SFPUC map precedence, including Vicente's posted/not-routinely-sampled row.

Offline: exercise the real feed parser and Today board using the public row
observed on 2026-11-21. Run directly or through scripts/check.sh.
"""
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock
from xml.etree.ElementTree import Element, tostring

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.landing import today_board
from shared.sfpuc_api import SFPUCRealTimeAPI, StationStatus


VICENTE = {
    "stationid": "4603", "stationname": "Ocean Beach at Vicente Street",
    "cso": None, "s_color": "W", "posted": "OCEAN#21_SL", "p_color": "R",
    "sample_date": "11/19/26", "lat": "37.73782", "lon": "-122.50825",
}


class SFPUCStatusTests(unittest.TestCase):
    def setUp(self):
        self.api = SFPUCRealTimeAPI()

    def test_posting_overrides_not_routinely_sampled(self):
        for sample_color in ("W", "w"):
            for posted_color in ("R", "G", "r", "g"):
                with self.subTest(sample=sample_color, posted=posted_color):
                    row = dict(VICENTE, s_color=sample_color, p_color=posted_color)
                    self.assertEqual(self.api._parse_station_status(row), StationStatus.POSTED)

    def test_unposted_station_stays_gray_despite_a_past_sample(self):
        for posted, color in ((None, "G"), ("", "R"), ("OCEAN#21_SL", None), ("OCEAN#21_SL", "Y")):
            with self.subTest(posted=posted, color=color):
                row = dict(VICENTE, posted=posted, p_color=color)
                self.assertEqual(self.api._parse_station_status(row), StationStatus.NOT_ROUTINELY_SAMPLED)

    def test_explicit_sample_colors_keep_the_citys_precedence(self):
        for color, expected in (("Y", StationStatus.NOT_SAMPLED), ("R", StationStatus.POSTED)):
            for posted in (None, "OCEAN#21_SL"):
                with self.subTest(color=color, posted=posted):
                    self.assertEqual(self.api._parse_station_status(dict(VICENTE, s_color=color, posted=posted)), expected)
        for color in (None, "G", "", "unexpected"):
            with self.subTest(color=color):
                self.assertEqual(self.api._parse_station_status(dict(VICENTE, s_color=color)), StationStatus.POSTED)
                self.assertEqual(self.api._parse_station_status(dict(VICENTE, s_color=color, posted=None)), StationStatus.SAFE)

    def test_feed_to_today_marks_vicente_posted_and_preserves_sample_history(self):
        for cso, posted, expected in ((None, "OCEAN#21_SL", "posted"),
                                      (None, None, "unknown"),
                                      ("Vicente CSD", "OCEAN#21_SL", "discharge")):
            with self.subTest(cso=cso, posted=posted):
                root = Element("string")
                root.text = json.dumps([dict(VICENTE, cso=cso, posted=posted)])
                self.api.session.get = Mock(return_value=Mock(content=tostring(root)))
                stations = self.api.fetch_stations()
                self.assertEqual(len(stations), 1)
                board = today_board(stations, now=datetime(2026, 11, 21, 9))
                ocean = next(z for z in board["zones"] if z["key"] == "ocean")
                site = next(s for s in ocean["stations"] if s["source"] == "OCEAN#21_SL")
                self.assertEqual(site["status"], expected)
                self.assertEqual(site["sampled"], "2026-11-19")
                self.assertEqual(site["sampled_age_days"], 2)
                if expected == "posted":
                    self.assertEqual(ocean["status_text"], "1 beach posted")
                    self.assertIn("OB Vicente", board["lead"])


if __name__ == "__main__":
    unittest.main()
