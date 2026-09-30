"""The alert email in the site's voice (migration 020, button to /today in 021; features/alerts/render._fallback is its
byte-for-byte port — db/scripts/test_render_parity.py checks the two agree once 021 is applied).
Offline: the Python port only."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.alerts import render  # noqa: E402
import re  # noqa: E402

UNSUB = "https://bwtf-sf.vercel.app/unsubscribe?t=abc"
WHEN = "Tue Sep 30, 7:12 AM PDT"
POSTED = {"station_id": "4602", "station_name": "Ocean Beach at Sloat Boulevard", "to": "posted"}
CSO = {"station_id": "4619", "station_name": "Islais Creek", "to": "cso"}


def test_one_station_is_named_and_several_are_counted():
    one = render._fallback([POSTED], False, "Ocean Beach", UNSUB)
    assert one["subject"] == "Beach alert: Ocean Beach at Sloat Boulevard — posted"
    assert 'Ocean Beach at Sloat Boulevard is <span style="color:#d4763a">posted</span>.</h1>' in one["html_body"]   # the name in black, the status in colour
    assert 'border:2px solid #d4763a;border-radius:20px' in one["html_body"]                       # the card's border takes the grade
    two = render._fallback([POSTED, CSO], True, None, UNSUB)
    assert two["subject"] == "TEST Beach alert: sewage discharge at 1 beach, posted at 1 beach"
    assert '<span style="color:#b5310a">Sewage discharge</span> at 1 beach. <span style="color:#d4763a">Posted</span> at 1 beach.' in two["html_body"]   # two parallel phrases, no "1 beach. 1 beach"
    assert "Discharging: Islais Creek. Posted: Ocean Beach at Sloat Boulevard. From SFPUC's beach map" in two["html_body"]
    assert 'border:2px solid #b5310a;border-radius:20px' in two["html_body"] and "TEST SF Beach Water Quality Alert" in two["html_body"]
    three = render._fallback([CSO, CSO, POSTED, POSTED], False, "East Beaches", UNSUB)
    assert three["subject"] == "Beach alert: sewage discharge at 2 beaches, posted at 2 beaches" and '<span style="color:#d4763a">Posted</span> at 2 beaches.' in three["html_body"]


def test_the_email_speaks_the_sites_language():
    h = render._fallback([POSTED, CSO], False, "Ocean Beach", UNSUB, WHEN)
    html, text, sms = h["html_body"], h["text_body"], h["sms_text"]
    assert "SF Beach Water Quality Alert" in html and "Surfrider SF &middot; Blue Water Task Force" in html          # the header Chase asked for
    assert ">Live status</a>" in html and 'href="https://bwtf-sf.vercel.app/today"' in html                         # one button, to the Today page (021)
    assert 'SFPUC\'s own map: <a href="https://webapps.sfpuc.org/sapps/beachesandbay.html"' in html                # the city's map in the small print
    assert "always win" not in html and "always win" not in text and "take precedence" in html and "take precedence" in text
    assert "Beach alert &middot; Tue Sep 30, 7:12 AM PDT &middot; Ocean Beach zone" in html and "you chose the Ocean Beach zone" in html   # the moment, then the zone
    assert text.split("\n")[1] == WHEN   # the text body carries it on its own line under the subject
    no_stamp = render._fallback([POSTED], False, "Ocean Beach", UNSUB)["html_body"]
    assert "Beach alert &middot; Ocean Beach zone" in no_stamp   # no stamp, no dot
    assert re.fullmatch(r"[A-Z][a-z]{2} [A-Z][a-z]{2} \d{1,2}, \d{1,2}:\d{2} [AP]M P[DS]T", render.alert_stamp())   # the live clock, Pacific, in the dispatcher's shape
    assert 'href="https://bwtf-sf.vercel.app/manage?t=abc"' in html and 'href="https://bwtf-sf.vercel.app/unsubscribe?t=abc"' in html
    assert html.count("/static/emailmaps/") == 2 and "/static/brand/bwtf_144x144.png" in html
    assert "<b>From SFPUC:</b> beach users should be aware that during and immediately after rainfall, nearshore bacteria concentrations may be elevated, even when there has not been a combined sewer discharge." in html and "72 hours" not in html                                              # their published sentence, not our figure
    assert "SFPUC posts a beach when samples show bacteria above State standards, and sometimes as a precaution" in html   # the reason under the status, never a claimed cause
    assert text.startswith("Beach alert: sewage discharge at 1 beach, posted at 1 beach\nTue Sep 30, 7:12 AM PDT\n\n- Ocean Beach at Sloat Boulevard: posted.\n- Islais Creek: sewage discharge.")
    assert sms == "Beach alert: Ocean Beach at Sloat Boulevard — posted; Islais Creek — sewage discharge. Live status: https://bwtf-sf.vercel.app/today"
    for k in ("Avoid water contact and check conditions", "New events at your selected sites", "View SFPUC Beach Map"):
        assert k not in html                                                                                       # the old copy is gone


def test_without_an_unsubscribe_link_the_footer_says_how_to_stop():
    h = render._fallback([POSTED], False, None, None)
    assert "Reply to this email with &quot;unsubscribe&quot; to stop them." in h["html_body"] and 'Reply to this email with "unsubscribe" to stop alerts.' in h["text_body"]
    assert 'href="https://bwtf-sf.vercel.app/today"' in h["html_body"] and "these beaches" not in h["html_body"]    # the public site stands in for the link's host


def test_migration_and_port_carry_the_same_stamp_and_the_same_words():
    sql = (ROOT / "db/migrations/021_live_status_to_today.sql").read_text()
    assert render.RENDERER_VERSION == "021" and "renderer_ver text := '021'" in sql
    assert sql.count("|| '/today'") == 1 and sql.count("E'/today\\n") == 1 and sql.count("'/today\" style=") == 1   # SMS, text, button
    body = sql.split("create or replace function public.bwtf_render_alert(")[1]
    for phrase in ("SF Beach Water Quality Alert", ">Live status</a>", "take precedence", "Surfrider SF &middot; Blue Water Task Force",
                   '<span style="color:#b5310a">sewage discharge</span>.', '<span style="color:#d4763a">posted</span>.', "From SFPUC:"):
        assert phrase in body, phrase
    assert "always win" not in body and "View SFPUC Beach Map" not in body


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failures else f"{failures} FAILED"); sys.exit(1 if failures else 0)
