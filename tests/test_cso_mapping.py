"""Feed → Today/alert classification regression for the confirmed #44 mapping."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
from xml.etree.ElementTree import Element, tostring

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.landing import today_board
from features.alerts.watcher import classify
from shared.cso_mapping import effective_cso, DOGPATCH_CSO
from shared.sfpuc_api import SFPUCRealTimeAPI


def rows(aquatic=DOGPATCH_CSO, crane=None, hyde=None):
    return [dict(stationid=sid, stationname=name, cso=cso, s_color="G",
                 p_color="G", posted=None, sample_date="11/18/26", lat="37.7", lon="-122.4")
            for sid, name, cso in [("4613", "Aquatic Park", aquatic),
                                   ("4620", "Crane Cove Park", crane),
                                   ("4614", "Hyde Street Pier", hyde)]]


class MappingTests(unittest.TestCase):
    def parse(self, payload):
        root = Element("string")
        root.text = json.dumps(payload)
        api = SFPUCRealTimeAPI()
        api.session.get = Mock(return_value=Mock(content=tostring(root)))
        return api.fetch_stations()

    def test_confirmed_november_18_to_20_flag_moves_to_crane(self):
        payload = rows()
        original = copy.deepcopy(payload)
        stations = self.parse(payload)
        aquatic, crane, hyde = stations
        self.assertEqual([classify(s) for s in stations], ["ok", "cso", "ok"])
        self.assertIsNone(aquatic.cso_station_id)
        self.assertEqual(aquatic.raw_cso_station_id, DOGPATCH_CSO)
        self.assertEqual(crane.cso_station_id, DOGPATCH_CSO)
        self.assertIsNone(crane.raw_cso_station_id)
        self.assertIn("Dogpatch", crane.cso_note)
        self.assertEqual(payload, original)
        sites = {s["full_name"]: s for z in today_board(stations)["zones"] for s in z["stations"]}
        self.assertEqual(sites["Aquatic Park"]["status"], "safe")
        self.assertEqual(sites["Crane Cove Park"]["status"], "discharge")
        self.assertIn("Aquatic Park", sites["Crane Cove Park"]["cso_note"])

    def test_november_21_clear_and_overlapping_native_crane_flag(self):
        for aquatic, native, expected in [(None, None, "ok"), ("", None, "ok"),
                                          (DOGPATCH_CSO, "native", "cso"),
                                          (None, "native", "cso")]:
            with self.subTest(aquatic=aquatic, native=native):
                stations = self.parse(rows(aquatic, native))
                self.assertEqual(classify(stations[1]), expected)
                self.assertEqual(stations[1].cso_station_id, native or (DOGPATCH_CSO if aquatic else None))

    def test_postings_samples_other_flags_and_hyde_are_preserved(self):
        payload = rows(hyde="BEACH STREET CSD13")
        payload[0].update(posted="BAY#211_SL", p_color="R", s_color="W")
        aquatic, crane, hyde = self.parse(payload)
        self.assertEqual(classify(aquatic), "posted")
        self.assertEqual(aquatic.sample_date.strftime("%Y-%m-%d"), "2026-11-18")
        self.assertEqual(classify(hyde), "cso")
        self.assertEqual(classify(self.parse(rows("different-north-shore-flag"))[0]), "cso")
        self.assertEqual(classify(self.parse(rows(None, None, "BEACH STREET CSD13"))[0]), "ok")

    def test_order_and_missing_station_rows(self):
        payload = rows()
        self.assertEqual(effective_cso(payload[1], list(reversed(payload))), DOGPATCH_CSO)
        self.assertEqual(len(self.parse(payload[:1])), 1)
        self.assertFalse(self.parse(payload[:1])[0].has_cso)
        self.assertFalse(self.parse(payload[1:])[0].has_cso)
        self.assertEqual(effective_cso(dict(stationid="4619", cso=DOGPATCH_CSO), payload), DOGPATCH_CSO)


if __name__ == "__main__":
    unittest.main()
