"""Running costs (features/alerts/costs.py, /alerts/costs): the model's thresholds and prices,
alert rates counted from the records, text segments measured from the renderer, and the gate."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.alerts import costs as C  # noqa: E402


def line(m, key):
    return next(x for x in m["lines"] if x["key"] == key)


def test_small_audiences_keep_existing_vercel_pro_but_supabase_stays_free():
    m = C.monthly_cost(49, 0.0, 115)
    assert m["total"] == 20 and line(m, "supabase")["monthly"] == 0
    m = C.monthly_cost(50, 0.0, 115)
    assert line(m, "email")["monthly"] == 0 and m["peak_daily_emails"] == 125
    assert m["brevo_daily_remaining"] == 175
    m = C.monthly_cost(100, 0.0, 115)
    assert line(m, "vercel")["monthly"] == 20 and line(m, "supabase")["monthly"] == 0
    assert abs(line(m, "domain")["monthly"] - 19.18 / 12) < 0.01
    assert C.free_until("vercel", sms_share=0, alerts_per_year=115) is None


def test_vercel_credit_and_cdn_capacity_are_independent():
    assert C.vercel_usage_cost(0, 0, 0, 0, 0)["monthly"] == 20
    # Function invocations are billed from the first unit, then offset by credit.
    assert C.vercel_usage_cost(10_000_000, 0, 0, 1_000_000, 1000)["monthly"] == 20
    billed = C.vercel_usage_cost(40_000_000, 0, 0, 1_000_000, 1000)
    assert billed["usage_dollars"] == 24 and billed["monthly"] == 24
    for requests, gb in [(1_000_001, 0), (0, 1000.001)]:
        tier = C.vercel_usage_cost(0, 0, 0, requests, gb)
        assert tier["monthly"] == 40 and tier["cdn_monthly"] == 20
        assert tier["overage_dollars"] == 0  # CDN subscription is outside the usage credit
    assert C.vercel_usage_cost(0, 0, 0, 10_000_000, 50_000)["monthly"] == 40
    assert C.vercel_usage_cost(0, 0, 0, 10_000_001, 50_000)["monthly"] == 120
    assert C.vercel_usage_cost(0, 0, 0, 50_000_001, 50_000)["monthly"] == 320
    demand = C.vercel_usage_cost(0, 0, 0, 150_000_001, 50_000)
    assert demand["cdn_on_demand"] and demand["cdn_monthly"] == 0
    assert abs(demand["monthly"] - (150_000_001 / 1e6 * 2 + 50_000 * .15)) < 1e-8


def test_supabase_upgrades_only_above_database_or_egress_allowances():
    assert C.supabase_usage_cost(.5, 5)["monthly"] == 0
    storage = C.supabase_usage_cost(.500001, 0)
    traffic = C.supabase_usage_cost(.1, 5.000001)
    assert storage["monthly"] == traffic["monthly"] == 35  # $25 plus a second Micro project
    assert storage["reasons"] == ["database exceeds 500 MB"]
    assert traffic["reasons"] == ["monthly uncached egress exceeds 5 GB"]
    included = C.supabase_usage_cost(8, 250)
    assert included["monthly"] == 35
    extra = C.supabase_usage_cost(8.01, 251)
    assert extra["pro_disk_gb"] == 9
    assert abs(extra["monthly"] - (35 + .125 + .09)) < 1e-8


def test_supabase_boundary_is_derived_from_retention_not_a_fixed_audience():
    limit = C.free_until("supabase", sms_share=0, alerts_per_year=115)
    assert limit > 100
    assert line(C.monthly_cost(limit, 0, 115), "supabase")["monthly"] == 0
    assert line(C.monthly_cost(limit + 1, 0, 115), "supabase")["monthly"] > 0
    assert C.free_until("supabase", sms_share=0, alerts_per_year=115, prune=True) > limit


def test_peak_day_boundary_includes_300_and_rounds_fractional_sends_up():
    at_limit = C.monthly_cost(120, 0, 120, busiest_month_factor=2.5)
    over_limit = C.monthly_cost(121, 0, 120, busiest_month_factor=2.5)
    assert at_limit["peak_daily_emails"] == 300 and at_limit["brevo_daily_remaining"] == 0
    assert line(at_limit, "email")["monthly"] == 0
    assert over_limit["peak_daily_emails"] == 303 and over_limit["brevo_daily_remaining"] == -3
    assert line(over_limit, "email")["monthly"] == 7.2
    assert C.free_until("email", sms_share=0, alerts_per_year=120) == 120


def test_only_email_recipients_use_the_daily_email_allowance():
    at_limit = C.monthly_cost(160, 0.25, 120)
    over_limit = C.monthly_cost(161, 0.25, 120)
    assert at_limit["email_subscribers"] == 120 and line(at_limit, "email")["monthly"] == 0
    assert over_limit["email_subscribers"] == 121 and line(over_limit, "email")["monthly"] > 0
    # The mixed-channel boundary lies between the old coarse search steps.
    assert C.free_until("email", sms_share=0.25, alerts_per_year=120) == 160
    texts_only = C.monthly_cost(1000, 1.0, 120)
    assert texts_only["peak_daily_emails"] == 0 and line(texts_only, "email")["monthly"] == 0


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
    assert kept["storage_gb"] > 8 > pruned["storage_gb"] and line(kept, "supabase")["monthly"] > 35 == line(pruned, "supabase")["monthly"]


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
    assert 'class="secnav"' not in h and 'class="subtabs"' not in h and 'title="Coordinators only — needs the passphrase">Admin</a>' in h   # Admin is in no hub; its way in is the footer (2026-11-04)
    assert j["ok"] and j["params"]["email"] == "ses" and j["params"]["plain_hyphen"] and j["month"]["subscribers"] == 2_500 and len(j["curve"]["with_texts"]) == len(C.STEPS)
    assert all(x["source"].startswith("https://") for x in j["month"]["lines"]) and j["prices_checked"] == C.PRICES_CHECKED
    assert j["email_planning"]["peak_emails_per_subscriber"] == 2.5
    assert j["email_planning"]["free_email_subscribers"] == 120
    assert j["month"]["peak_daily_emails"] == 5625
    assert 'id="peak-day"' in h and "15-minute window" in h and "not an enforced cap" in h
    assert "same minute" not in h and "brevo_paid_from" not in j["thresholds"]
    assert "pro_from" not in j["thresholds"] and 'id="infra-database"' in h
    assert j["infrastructure_prices_checked"] == C.INFRA_PRICES_CHECKED
    assert j["month"]["infrastructure"]["vercel"]["credit_dollars"] == 20
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
