import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

"""Run with python3 -m unittest discover -s tests -p test_getcsv_recordings.py."""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.import_getcsv_recordings import observations


class RecorderImportTests(unittest.TestCase):
    def convert(self, snapshots):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for i, stations in enumerate(snapshots):
                path = Path(directory) / f"{i}.json"
                path.write_text(json.dumps({"read_at": f"2026-11-16T12:{i:02}:00-08:00", "stations": stations}))
                paths.append(path)
            return observations(reversed(paths))

    def test_revision_reversion_partial_and_unchanged(self):
        def snap(total, entero=None):
            return {"BAY#220_SL": [{"sample_date": "11/13/26", "COLI_TOTAL": total, "ENTERO": entero}]}
        rows = self.convert([snap(">16000"), snap("16000"), snap(">16000", "<10"), snap(">16000", "<10")])
        self.assertEqual([r["value_raw"] for r in rows], [">16000", "16000", ">16000", "<10"])
        self.assertEqual([r["observation_kind"] for r in rows], ["baseline", "changed", "changed", "appeared"])
        self.assertEqual(rows[0]["observed_at"], "2026-11-16T20:00:00+00:00")

    def test_removed_indicator_but_not_aged_out_sample(self):
        rows = self.convert([
            {"A": [{"sample_date": "11/13/26", "ENTERO": "5"}, {"sample_date": "11/12/26", "ENTERO": "6"}]},
            {"A": [{"sample_date": "11/14/26", "COLI_TOTAL": "10"}, {"sample_date": "11/13/26"}]},
        ])
        removed = [r for r in rows if r["observation_kind"] == "removed"]
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0]["sample_date"], "2026-11-13")

    def test_actual_recordings_preserve_mission_correction(self):
        paths = list(Path("docs/discharge-observations/feed").glob("*-getCSV.json"))
        rows = observations(paths)
        changes = [r for r in rows if r["source_code"] == "BAY#220_SL" and r["sample_date"] == "2026-11-13" and r["analyte"] == "COLI_TOTAL"]
        self.assertEqual([r["value_raw"] for r in changes], [">16000", "16000", ">16000"])
        self.assertEqual(rows, observations(paths))


if __name__ == "__main__":
    unittest.main()
