"""Lab SOP (/learn/lab-sop, 2026-10-18): the Blue Water Task Force lab's procedure for testing a sample
for Enterococcus with Enterolert and a Quanti-Tray, from the lab's wall pages ("(Incomplete) BWTF Lab
Instructions Wall Pages", Chase, 2026-10-18): four phases, 25 steps.

Content only, so the procedure can be edited without touching the template (app/templates/learn/sop.html):
  steps   n, title, text (trusted HTML: <b>, <b class="mix"> for the blue "mixing"), flag, photos, slots, tall (portrait crops)
  photos  files in features/learn/sop_photos/ (cut from the wall pages' PDF), served only behind the passphrase at
          /learn/lab-sop/photos/<file> (app/wsgi.py, since 2026-10-20), with an optional tag
          (COARSE, IN, ...), badge ({"big", "small"}), inset (drawn small over the photo before it) and
          pos (CSS object-position for the crop)
  slots   the wall pages' empty photo frames, by their caption; replace one with a photo when it is taken
  notes   the callouts between steps: kind banner / repeat / rule / warn / caps, half = beside the next item
A step with two or more photos, or none, spans the phase's width; the rest sit two to a row, as the wall pages pair them.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Optional

from flask import render_template

P = ""   # photos are file names in PHOTO_DIR
PHOTO_DIR = Path(__file__).resolve().parent / "sop_photos"

PHASES = [
    {"n": 1, "key": "return-clean", "title": "Return & Clean", "accent": "#2f7fcb", "tint": "#e7f0fa", "items": [
        {"n": 1, "title": "Rinse gear & wipe cooler",
         "text": "Use the hose to rinse sand and seawater off waders, sample poles (extra rinse spring clamps), and jar exteriors. "
                 "Let jars dry away from the lab table until after the surface is sanitized. Wipe out the inside of the sample cooler.",
         "photos": [{"src": P + "s01_rinse_waders.jpg", "alt": "Hosing sand and seawater off a pair of waders"},
                    {"src": P + "s01_rinse_pole.jpg", "alt": "Rinsing a sample pole's spring clamp", "inset": True},
                    {"src": P + "s01_wipe_bench.jpg", "alt": "Wiping down the lab bench with a disinfecting wipe"}]},
        {"note": "banner", "text": "Begin aseptic protocol"},
        {"n": 2, "title": "Prep hands and workspace",
         "text": "After all handling of dirty gear, thoroughly wash and dry hands before touching any of the lab equipment. "
                 "Wipe the bench's surface and exteriors of sample jars with disinfectant."},
        {"n": 3, "title": "Warm up the sealer",
         "text": "Turn on the Quanti-Tray sealer to begin warming (about 10 min, ready when the light turns green); the power switch is on the back right.",
         "photos": [{"src": P + "s03_sealer.jpg", "alt": "The Quanti-Tray Sealer PLUS with its ready light green"}]},
        {"n": 4, "title": "Label trays",
         "text": "Label the backs of the Quanti-Trays with each site name, date, and time of sample, being sure not to poke through the back of the tray's seal.",
         "photos": [{"src": P + "s04_label_tray.jpg", "alt": "The back of a tray labeled sample site, time, date", "pos": "50% 8%"}]},
        {"note": "repeat", "title": "Repeat hand wash between steps 4 & 5",
         "text": "Re-wash hands after prepping the lab for testing and before handling any glassware."},
    ]},
    {"n": 2, "key": "dilute", "title": "Make Diluted Sample", "accent": "#127c8c", "tint": "#e3f3f5", "items": [
        {"n": 5, "title": "Stage items on the table",
         "text": "Place each <b>sample</b> jar (<b>black lid</b>) in its labeled space in the grid. Pair one <b class=\"mix\">mixing</b> jar "
                 "(<b class=\"mix\">blue lid</b>) in each grid cell. Put the graduated cylinder and the distilled water squirt bottle on the lab table.",
         "photos": [{"src": P + "s05_stage.jpg", "alt": "A blue-lid mixing jar, a black-lid sample jar, the squirt bottle and the graduated cylinder on the bench"}]},
        {"n": 6, "title": "Open the mixing jar", "flag": "contamination",
         "text": "Remove the lid, being sure not to touch the top of the jar or the inside of the lid, to <b class=\"risk\">avoid cross contamination</b>.",
         "photos": [{"src": P + "s06_open_jar.jpg", "alt": "Lifting the blue lid off a mixing jar without touching its rim"}]},
        {"n": 7, "title": "Measure 90 mL of distilled water", "tall": True,   # the cylinders stand tall, as on the wall page
         "text": "<b>Coarse fill:</b> dispense between <b>80–85 mL</b> from the large distilled water jug into the graduated cylinder.<br>"
                 "<b>Fine fill:</b> crouch to eye level and, using the squirt bottle of distilled water, top off the cylinder.<br>"
                 "Fill until the bottom of the meniscus is on the <b>90 mL</b> mark.",
         "photos": [{"src": P + "s07_coarse.jpg", "alt": "Filling the cylinder from the distilled water jug", "tag": "Coarse", "badge": {"big": "~85 mL", "small": "target"}},
                    {"src": P + "s07_fine.jpg", "alt": "Topping off the cylinder at eye level with the squirt bottle", "tag": "Fine", "badge": {"big": "90 mL", "small": "target"}},
                    {"src": P + "s07_meniscus.jpg", "alt": "The bottom of the meniscus on the 90 mL line"}]},
        {"note": "rule", "title": "Overshoot recovery",
         "text": "If you overshoot, pour the surplus distilled water into the sink and repeat until the bottom of the meniscus sits on the 90 mL line."},
        {"n": 8, "title": "Fill the mixing jar",
         "text": "Pour 90 mL distilled water into the <b class=\"mix\">mixing</b> jar.",
         "photos": [{"src": P + "s08_fill_jar.jpg", "alt": "Pouring the 90 mL from the cylinder into the mixing jar"}]},
        {"n": 9, "title": "Prep the pipette", "flag": "contamination",
         "text": "Fit the green pump firmly to the 10 mL pipette; don't touch or rest the lower half on anything (let the tip overhang the table edge).",
         "photos": [{"src": P + "s09_pipette.jpg", "alt": "Fitting the green pump onto the 10 mL pipette"},
                    {"src": P + "s09_tip_overhang.jpg", "alt": "The pipette tip overhanging the table edge", "inset": True}]},
        {"note": "rule", "title": "Use only one graduated cylinder per testing day",
         "text": "Unless you have reason to believe there could have been cross contamination."},
        {"n": 10, "title": "Re-suspend the sample",
         "text": "Gently invert the <b>sample</b> bottle 2–3 times.",
         "photos": [{"src": P + "s10_resuspend.jpg", "alt": "Turning the sample jar over to mix it"}]},
        {"n": 11, "title": "Draw to the 10 mL mark",
         "text": "From the top of your <b>sample</b> jar, draw water until the bottom of the meniscus is at the 10 mL mark.",
         "photos": [{"src": P + "s11_draw_10ml.jpg", "alt": "Drawing sample water up the pipette to the 10 mL mark"}]},
        {"n": 12, "title": "Dispense into the mixing jar",
         "text": "Press the white release button to dispense the 10 mL of sample into the <b class=\"mix\">mixing</b> jar.",
         "slots": ["Dispensing solution into the mixing jar"]},
        {"note": "standing", "title": "Standing rules",
         "text": "Never touch the top of the jar or the inside of the lid. Set lids down face-up, or keep them in your hand.<br>"
                 "One <b class=\"mix\">mixing</b> jar per site, plus the control. Clean hands between dilutions."},
        {"n": 13, "title": "Add Enterolert",
         "text": "One snap-pack per bottle, including the control; point it away from your face, pop, and pour into the <b class=\"mix\">mixing</b> jar.",
         "photos": [{"src": P + "s13_enterolert.jpg", "alt": "Pouring an Enterolert snap-pack into the mixing jar"}]},
        {"n": 14, "title": "Dissolve",
         "text": "Cap and gently swirl until the powder dissolves and bubbles disappear.",
         "photos": [{"src": P + "s14_dissolve.jpg", "alt": "A capped mixing jar, the reagent dissolved and yellow"}]},
        {"note": "repeat", "title": "Steps 5–14",
         "text": "Repeat for each site, and once for the control (100 mL distilled water). Clean hands between dilutions for each site and the control. "
                 "If any sample water spills, wipe the surface dry and re-sanitize."},
    ]},
    {"n": 3, "key": "fill-seal", "title": "Fill, Seal, Incubate", "accent": "#16725a", "tint": "#e4f2ec", "items": [
        {"n": 15, "title": "Bend the tray to pour in the mixture",
         "text": "Pour each bottle into its matching tray.",
         "photos": [{"src": P + "s15_pour_tray.jpg", "alt": "Pouring the mixture into a Quanti-Tray"}]},
        {"n": 16, "title": "Release air bubbles",
         "text": "Tap the small wells; let foam settle.",
         "photos": [{"src": P + "s16_tap_wells.jpg", "alt": "Tapping the filled tray so the foam settles"}]},
        {"n": 17, "title": "Run tray through the sealer",
         "text": "Place the tray into the rubber insert <b>well-side down</b>. Feed the side of the tray insert with the small cells into the sealer first. "
                 "Push gently until you feel it begin to take the tray.",
         "photos": [{"src": P + "s17_sealer_in.jpg", "alt": "Feeding the tray in its rubber insert into the sealer", "tag": "In"},
                    {"src": P + "s17_sealer_out.jpg", "alt": "The sealed tray coming out of the far side", "tag": "Out"}]},
        {"note": "warn", "text": "Take the sealed tray off the <b>far side</b>. Never pull it back through the rollers."},
        {"note": "caps", "text": "Small cells first · well-side down"},
        {"n": 18, "title": "Inspect the tray's wells and seal",
         "text": "Examine the sealed tray against the three conditions below to confirm it is valid before incubating.",
         "checks": [("good", "Good", "Every well at least ½ full"), ("valid", "Still valid", "Up to 2 completely empty wells"),
                    ("discard", "Discard", "Bad seal or 3+ empty wells")],
         "slots": ["Correct: a single tray, clear top rows, yellow bottom rows", "Incorrect: two trays side by side, top-down"]},
        {"note": "repeat", "title": "Steps 15–18", "text": "Repeat for every tray, including the control."},
        {"n": 19, "title": "Incubate at 41 °C",
         "text": "Insert all trays into the incubator together to minimize heat loss, stacked no more than 4 high. Write the start time on the data sheet and whiteboard.",
         "badge": {"big": "41 °C", "small": "hold at"},
         "slots": ["Incubator interior, tray on the rack", "Start time written on the data sheet"]},
        {"n": 20, "title": "Clean up",
         "text": "Dispose of gloves, wrappers and bottles; wash hands; wipe the bench."},
    ]},
    {"n": 4, "key": "read", "title": "Read Results · Next Day", "accent": "#7a3cc4", "tint": "#f1eafb", "items": [
        {"n": 21, "title": "Pull the trays",
         "text": "Remove from the incubator 24 h after the logged start; record the read time.",
         "badge": {"big": "24 hr", "small": "after start"},
         "slots": ["Removing trays with the clock visible"]},
        {"n": 22, "title": "Read trays under UV", "flag": "control",
         "text": "If wells fluoresce blue in the control tray, contamination has occurred and all trays in its site group are invalid.",
         "slots": ["Tray under UV, lights off"]},
        {"note": "warn", "half": True, "text": "Results are definitive at 24–28 hours; positives observed before 24 hours and negatives observed after 28 hours are also valid."},
        {"note": "warn", "half": True, "text": "Bright yellow that doesn't fluoresce does not count."},
        {"n": 23, "title": "Count large and small wells",
         "text": "Count fluorescing large wells and small wells separately, and record both. Both counts feed the IDEXX MPN table independently.",
         "slots": ["Counting wells and marking the data sheet"]},
        {"n": 24, "title": "Calculate MPN",
         "text": "If any control well fluoresces, the results from all sites completed by that control's lab tech are invalid and should not be recorded.",
         "see": ("/learn#tray", "Try the counts on the Learn page"),
         "slots": ["Negative control tray under UV, all wells dark"]},
        {"n": 25, "title": "Record & clean up",
         "text": "Confirm the control read zero, record results, dispose of trays, wash hands, and enter data at "
                 "<a href=\"https://bwtf.surfrider.org\" target=\"_blank\" rel=\"noopener\">bwtf.surfrider.org</a>."},
    ]},
]


def photo_path(name: str) -> Optional[Path]:
    """A photo the SOP names, or None: only registered files are served, so no path in the URL reaches anything else."""
    known = {ph["src"] for st in steps() for ph in st.get("photos", [])}
    return PHOTO_DIR / name if name in known and (PHOTO_DIR / name).is_file() else None


@lru_cache(maxsize=None)
def _stamp(name: str) -> str:
    return hashlib.sha1((PHOTO_DIR / name).read_bytes()).hexdigest()[:10]


def photo_url(name: str) -> str:
    return f"/learn/lab-sop/photos/{name}?v={_stamp(name)}"   # content-stamped like asset(), so the private cache can keep it a day


def steps() -> list[dict]:
    return [it for ph in PHASES for it in ph["items"] if "n" in it]


def layout(items: list[dict]) -> list[dict]:
    """Each item with its width: a step with two or more photos/slots, or none, spans the row; notes span it unless half."""
    out = []
    for it in items:
        if "note" in it:
            wide = not it.get("half") and it["note"] != "standing"
        else:
            pics = []
            for ph in it.get("photos", []):   # an inset rides on the photo before it
                ph = {**ph, "url": photo_url(ph["src"])}
                if ph.get("inset") and pics:
                    pics[-1] = {**pics[-1], "inset": ph}
                else:
                    pics.append(ph)
            it = {**it, "pics": pics}
            wide = len(pics) + len(it.get("slots", [])) != 1 or bool(it.get("checks"))
        out.append({**it, "wide": wide})
    return out


def handle_page(query, body):
    phases = [{**ph, "items": layout(ph["items"])} for ph in PHASES]
    html = render_template("learn/sop.html", phases=phases, n_steps=len(steps()))
    return 200, "text/html; charset=utf-8", html.encode()


GET_ROUTES = {"/learn/lab-sop": handle_page}
