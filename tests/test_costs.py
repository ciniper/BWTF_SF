"""Running costs (features/alerts/costs.py, /alerts/costs): the model's thresholds and prices,
alert rates counted from the records, text segments measured from the renderer, and the gate."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.alerts import costs as C  # noqa: E402


def line(m, key):
    return next(x for x in m["lines"] if x["key"] == key)


def test_everything_is_free_below_50_and_the_plans_start_where_chase_put_them():
    m = C.monthly_cost(49, 0.0, 115)
    assert m["total"] == 0 and all(x["monthly"] == 0 for x in m["lines"])
    m = C.monthly_cost(50, 0.0, 115)                              # Brevo turns paid at 50 (Chase, 2026-10-01)
    assert abs(line(m, "email")["monthly"] - 9 * 0.8) < 0.01 and line(m, "vercel")["monthly"] == 0 and line(m, "domain")["monthly"] == 0
    m = C.monthly_cost(100, 0.0, 115)                             # Vercel Pro, Supabase Pro and a domain at 100
    assert line(m, "vercel")["monthly"] == 20 and line(m, "supabase")["monthly"] == 25 and abs(line(m, "domain")["monthly"] - 19.18 / 12) < 0.01
    assert C.BREVO_PAID_FROM == 50 and C.PRO_FROM == 100 and C.free_until("vercel", sms_share=0, alerts_per_year=115) == 99


def test_email_plans_are_sized_for_the_busiest_month_and_ses_bills_per_message():
    m = C.monthly_cost(1_000, 0.0, 120, busiest_month_factor=2.5)     # 10,000 a month on average, 25,000 in the busiest
    assert abs(line(m, "email")["monthly"] - 56 * 0.8) < 0.01 and "50,000 a month" in line(m, "email")["plan"]
    ses = C.monthly_cost(1_000, 0.0, 120, email="ses")
    assert abs(line(ses, "email")["monthly"] - 1.0) < 0.01                                # 10,000 × $0.10 / 1,000
    big = C.monthly_cost(500_000, 0.0, 120)
    assert "extrapolated" in line(big, "email")["plan"]


def test_texts_cost_per_segment_and_the_em_dash_doubles_them():
    assert C.sms_segments("a" * 160) == ("GSM-7", 1) and C.sms_segments("a" * 161) == ("GSM-7", 2)
    assert C.sms_segments("Beach alert — posted") == ("UCS-2", 1) and C.sms_segments("—" + "a" * 70) == ("UCS-2", 2)
    prof = C.message_profile()
    assert prof["segments_now"] > 2 * prof["segments_plain"] * 0.9 and prof["segments_plain"] >= 1      # measured from the live renderer
    assert [e["encoding"] for e in prof["examples"]] == ["UCS-2"] * 3 and 3 < prof["email_kb"] < 15
    now = C.monthly_cost(1_000, 0.25, 120); plain = C.monthly_cost(1_000, 0.25, 120, plain_hyphen=True)
    texts = 250 * 120 / 12
    assert abs(line(now, "sms")["monthly"] - (2.15 + texts * prof["segments_now"] * (0.0083 + C.PRICES["twilio"]["carrier_fee"]))) < 0.01
    assert line(plain, "sms")["monthly"] < line(now, "sms")["monthly"] * 0.6


def test_alert_rates_come_from_the_records():
    r = C.rates()
    assert r["years"][0] == 2017 and r["years"][1] >= 2024
    assert set(r["per_zone"]) == {"ocean", "baker_china", "north", "east"} and all(20 < n < 150 for n in r["per_zone"].values())
    assert r["per_zone"]["east"] == max(r["per_zone"].values())                     # Islais, Mission and Candlestick: the busiest zone
    assert 150 < r["all_zones"] < 400 and r["busiest_year"]["alerts"] >= r["all_zones"] and 1.5 < r["busiest_month_factor"] < 4


def test_storage_grows_with_kept_copies_and_pruning_keeps_it_small():
    kept = C.monthly_cost(20_000, 0.0, 115); pruned = C.monthly_cost(20_000, 0.0, 115, prune=True)   # a year of copies vs three months
    assert kept["storage_gb"] > 8 > pruned["storage_gb"] and line(kept, "supabase")["monthly"] > 25 == line(pruned, "supabase")["monthly"]


def test_the_page_and_its_api_sit_behind_the_alerts_passphrase():
    from app.wsgi import app
    with app.test_client() as c:
        locked = c.get("/alerts/costs").data.decode()
        assert "passphrase" in locked.lower() and 'id="curve"' not in locked
        assert c.get("/alerts/api/costs").status_code == 401
        with c.session_transaction() as s:
            s["alerts_unlocked"] = True
        h = c.get("/alerts/costs?subscribers=2500&sms_share=0.1").data.decode()
        j = c.get("/alerts/api/costs?subscribers=2500&sms_share=0.1&email=ses&hyphen=1&prune=1").get_json()
        d = c.get("/alerts").data.decode()
    assert 'id="curve"' in h and 'class="seg-toggle" data-k="sms_share"' in h and 'href="/alerts/costs" class="on"' in h and 'name="robots" content="noindex"' in h
    assert 'href="/alerts" class="on gated"' in h                                      # the Sewage Alert System tab stays lit on its costs tab
    assert j["ok"] and j["params"]["email"] == "ses" and j["params"]["plain_hyphen"] and j["month"]["subscribers"] == 2_500 and len(j["curve"]["with_texts"]) == len(C.STEPS)
    assert all(x["source"].startswith("https://") for x in j["month"]["lines"]) and j["prices_checked"] == C.PRICES_CHECKED
    assert d.index('data-view="sim"') < d.index('href="/alerts/costs"')                 # the third tab on the dashboard


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("  ok  ", name)
            except Exception as e:  # noqa: BLE001
                failures += 1; print("FAIL", name + ":", type(e).__name__ + ":", e)
    print("ALL PASS" if not failures else f"{failures} FAILED")
    sys.exit(1 if failures else 0)
