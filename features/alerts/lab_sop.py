"""Lab SOP (/alerts/lab-sop, an Admin tab since 2026-11-04; Learn's /learn/lab-sop from 2026-10-18, which now redirects
here): the Blue Water Task Force lab's procedure for testing a sample
for Enterococcus with Enterolert and a Quanti-Tray, from the lab's wall pages ("(Incomplete) BWTF Lab
Instructions Wall Pages", Chase, 2026-10-18): four phases, 25 steps.

Content only, so the procedure can be edited without touching the template (app/templates/alerts/lab_sop.html):
  steps   n, title, text (trusted HTML: <b>, <b class="mix"> for the blue "mixing"), flag, photos, slots, tall (portrait crops)
  photos  files in features/alerts/sop_photos/ (cut from the wall pages' PDF), served only behind the passphrase at
          /alerts/lab-sop/photos/<file> (app/wsgi.py, since 2026-10-20), with an optional tag
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
        {"n": 1, "title": "Rinse Gear & Wipe Cooler",
         "text": "Use the hose to flush sand and seawater off the waders, sample poles, and jar exteriors. Flush the spring clamps two times. Keep the jars away from the lab table until they are dry and the table surface is clean. Wipe the inner surface of the sample cooler.",
         "photos": [{"src": P + "s01_rinse_waders.jpg", "alt": "A person uses a hose to flush sand and seawater off waders"},
                    {"src": P + "s01_rinse_pole.jpg", "alt": "Rinsing a sample pole's spring clamp", "inset": True},
                    {"src": P + "s01_wipe_bench.jpg", "alt": "A person wipes the lab bench with a disinfectant wipe"}]},
        {"note": "banner", "text": "Begin Aseptic Protocol"},
        {"n": 2, "title": "Prepare Your Hands and the Workspace",
         "text": "After you touch dirty equipment, wash and dry your hands fully. Do this before you touch the lab equipment. Wipe the bench surface and the exteriors of the sample jars with disinfectant."},
        {"n": 3, "title": "Warm Up the Sealer",
         "text": "Turn on the Quanti-Tray sealer to make it warm. The power switch is on the back, at the right. The sealer is ready when the light is green (approximately 10 min).",
         "photos": [{"src": P + "s03_sealer.jpg", "alt": "The Quanti-Tray Sealer PLUS with its ready light green"}]},
        {"n": 4, "title": "Label Trays",
         "text": "On the back of each Quanti-Tray, write the site name and the date and time of the sample. Do not push through the seal on the back of the tray.",
         "photos": [{"src": P + "s04_label_tray.jpg", "alt": "The back of a tray with the sample site, time, and date", "pos": "50% 8%"}]},
        {"note": "repeat", "title": "Repeat Hand Wash Between Steps 4 & 5",
         "text": "Wash your hands again after you prepare the lab for the test. Do this before you touch glassware."},
    ]},
    {"n": 2, "key": "dilute", "title": "Make Diluted Sample", "accent": "#127c8c", "tint": "#e3f3f5", "items": [
        {"n": 5, "title": "Stage Items on the Table",
         "text": "Put each <b>sample</b> jar (<b>black lid</b>) in its labeled space in the grid. Put one <b class=\"mix\">mixing</b> jar (<b class=\"mix\">blue lid</b>) in each grid cell with a sample jar. Put the graduated cylinder and the distilled water squirt bottle on the lab table.",
         "photos": [{"src": P + "s05_stage.jpg", "alt": "A blue-lid mixing jar, a black-lid sample jar, the squirt bottle and the graduated cylinder on the bench"}]},
        {"n": 6, "title": "Open the Mixing Jar", "flag": "contamination",
         "text": "Remove the lid. Do not touch the top of the jar or the inner surface of the lid. This <b class=\"risk\">prevents cross contamination</b>.",
         "photos": [{"src": P + "s06_open_jar.jpg", "alt": "A person removes the blue lid from a mixing jar without touching its rim"}]},
        {"n": 7, "title": "Measure 90 mL of Distilled Water", "tall": True,   # the cylinders stand tall, as on the wall page
         "text": "<b>Coarse Fill:</b> put <b>80–85 mL</b> from the large distilled water jug into the graduated cylinder.<br><b>Fine Fill:</b> put your eyes at the level of the cylinder. Use the squirt bottle of distilled water to add water to the cylinder.<br>Fill until the bottom of the meniscus is on the <b>90 mL</b> mark.",
         "photos": [{"src": P + "s07_coarse.jpg", "alt": "Filling the cylinder from the distilled water jug", "tag": "Coarse", "badge": {"big": "~85 mL", "small": "Target"}},
                    {"src": P + "s07_fine.jpg", "alt": "Topping off the cylinder at eye level with the squirt bottle", "tag": "Fine", "badge": {"big": "90 mL", "small": "Target"}},
                    {"src": P + "s07_meniscus.jpg", "alt": "The bottom of the meniscus on the 90 mL line"}]},
        {"note": "rule", "title": "Overshoot Recovery",
         "text": "If you add too much water, pour the extra distilled water into the sink. Do the step again until the bottom of the meniscus is on the 90 mL line."},
        {"n": 8, "title": "Fill the Mixing Jar",
         "text": "Pour 90 mL distilled water into the <b class=\"mix\">mixing</b> jar.",
         "photos": [{"src": P + "s08_fill_jar.jpg", "alt": "Pouring the 90 mL from the cylinder into the mixing jar"}]},
        {"n": 9, "title": "Prep the Pipette", "flag": "contamination",
         "text": "Attach the green pump tightly to the 10 mL pipette. Do not touch the lower half of the pipette, and do not put it on a surface. Let the tip extend past the edge of the table.",
         "photos": [{"src": P + "s09_pipette.jpg", "alt": "Fitting the green pump onto the 10 mL pipette"},
                    {"src": P + "s09_tip_overhang.jpg", "alt": "The pipette tip overhanging the table edge", "inset": True}]},
        {"note": "rule", "title": "Use Only One Graduated Cylinder per Testing Day",
         "text": "Use a new cylinder only if you think that cross contamination possibly occurred."},
        {"n": 10, "title": "Mix the Sample Again",
         "text": "Carefully turn the <b>sample</b> bottle upside down 2–3 times.",
         "photos": [{"src": P + "s10_resuspend.jpg", "alt": "Turning the sample jar over to mix it"}]},
        {"n": 11, "title": "Draw to the 10 mL Mark",
         "text": "Pull water from the top of your <b>sample</b> jar until the bottom of the meniscus is at the 10 mL mark.",
         "photos": [{"src": P + "s11_draw_10ml.jpg", "alt": "Drawing sample water up the pipette to the 10 mL mark"}]},
        {"n": 12, "title": "Dispense into the Mixing Jar",
         "text": "Push the white release button to put the 10 mL of sample into the <b class=\"mix\">mixing</b> jar.",
         "slots": ["Dispensing solution into the mixing jar"]},
        {"note": "standing", "title": "Standing Rules",
         "text": "Do not touch the top of the jar or the inner surface of the lid. Put lids down with the inner surface up, or hold them in your hand.<br>Use one <b class=\"mix\">mixing</b> jar for each site, plus one for the control. Clean your hands between dilutions."},
        {"n": 13, "title": "Add Enterolert",
         "text": "Use one snap-pack for each bottle, including the control. Point the snap-pack away from your face. Break it open and pour it into the <b class=\"mix\">mixing</b> jar.",
         "photos": [{"src": P + "s13_enterolert.jpg", "alt": "Pouring an Enterolert snap-pack into the mixing jar"}]},
        {"n": 14, "title": "Dissolve",
         "text": "Put the cap on the jar. Carefully turn the jar in a circle until the powder dissolves and there are no bubbles.",
         "photos": [{"src": P + "s14_dissolve.jpg", "alt": "A capped mixing jar, the reagent dissolved and yellow"}]},
        {"note": "repeat", "title": "Steps 5–14",
         "text": "Do these steps again for each site, and one time for the control (100 mL distilled water). Clean your hands between the dilutions for each site and for the control. If sample water spills, wipe the surface dry and clean it with disinfectant again."},
    ]},
    {"n": 3, "key": "fill-seal", "title": "Fill, Seal, Incubate", "accent": "#16725a", "tint": "#e4f2ec", "items": [
        {"n": 15, "title": "Bend the Tray to Pour in the Mixture",
         "text": "Pour each bottle into its matching tray.",
         "photos": [{"src": P + "s15_pour_tray.jpg", "alt": "Pouring the mixture into a Quanti-Tray"}]},
        {"n": 16, "title": "Release Air Bubbles",
         "text": "Tap the small wells. Let the foam settle.",
         "photos": [{"src": P + "s16_tap_wells.jpg", "alt": "Tapping the filled tray so the foam settles"}]},
        {"n": 17, "title": "Run Tray Through the Sealer",
         "text": "Put the tray into the rubber insert, <b>well side down</b>. Put the side of the insert with the small cells into the sealer first. Push carefully until you feel the sealer start to pull the tray.",
         "photos": [{"src": P + "s17_sealer_in.jpg", "alt": "Feeding the tray in its rubber insert into the sealer", "tag": "In"},
                    {"src": P + "s17_sealer_out.jpg", "alt": "The sealed tray coming out of the far side", "tag": "Out"}]},
        {"note": "warn", "text": "Remove the sealed tray from the <b>far side</b>. Do not pull it back through the rollers."},
        {"note": "caps", "text": "Small Cells First · Well-Side Down"},
        {"n": 18, "title": "Inspect the Tray's Wells and Seal",
         "text": "Before you incubate the tray, compare it with the three conditions below. Make sure that it is valid.",
         "checks": [("good", "Good", "Each Well Is ½ Full or More"), ("valid", "Still Valid", "A Maximum of 2 Fully Empty Wells"),
                    ("discard", "Discard", "Bad Seal or 3+ Empty Wells")],
         "slots": ["Correct: a single tray, clear top rows, yellow bottom rows", "Incorrect: two trays side by side, top-down"]},
        {"note": "repeat", "title": "Steps 15–18", "text": "Do this step for each tray, including the control."},
        {"n": 19, "title": "Incubate at 41 °C",
         "text": "Put all trays into the incubator at the same time. This decreases heat loss. Do not put more than 4 trays in a stack. Write the start time on the data sheet and the whiteboard.",
         "badge": {"big": "41 °C", "small": "Hold at"},
         "slots": ["Incubator interior, tray on the rack", "Start time written on the data sheet"]},
        {"n": 20, "title": "Clean Up",
         "text": "Discard the gloves, wrappers and bottles. Wash your hands. Wipe the bench."},
    ]},
    {"n": 4, "key": "read", "title": "Read Results · Next Day", "accent": "#7a3cc4", "tint": "#f1eafb", "items": [
        {"n": 21, "title": "Pull the Trays",
         "text": "Remove the trays from the incubator 24 h after the recorded start time. Record the read time.",
         "badge": {"big": "24 hr", "small": "After Start"},
         "slots": ["Removing trays with the clock visible"]},
        {"n": 22, "title": "Read Trays Under UV", "flag": "control",
         "text": "If wells in the control tray glow blue, contamination occurred. All trays in its site group are not valid.",
         "slots": ["Tray under UV, lights off"]},
        {"note": "warn", "half": True, "text": "Results are final at 24–28 hours. Positive results before 24 hours are also valid. Negative results after 28 hours are also valid."},
        {"note": "warn", "half": True, "text": "Bright yellow that does not glow does not count."},
        {"n": 23, "title": "Count Large and Small Wells",
         "text": "Count the glowing large wells and small wells separately. Record the two counts. The IDEXX MPN table uses each count separately.",
         "slots": ["Counting wells and marking the data sheet"]},
        {"n": 24, "title": "Calculate MPN",
         "text": "If a control well glows, the results from all sites that this lab tech tested with that control are not valid. Do not record them.",
         "see": ("/learn#tray", "Try the counts on the Learn page"),
         "slots": ["Negative control tray under UV, all wells dark"]},
        {"n": 25, "title": "Record & Clean Up",
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
    return f"/alerts/lab-sop/photos/{name}?v={_stamp(name)}"   # content-stamped like asset(), so the private cache can keep it a day


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
    html = render_template("alerts/lab_sop.html", phases=phases, n_steps=len(steps()))
    return 200, "text/html; charset=utf-8", html.encode()


GET_ROUTES = {"/alerts/lab-sop": handle_page}
REDIRECTS = {"/learn/lab-sop": "/alerts/lab-sop"}   # its Learn address, 2026-10-18 → 11-04 (the Collect page and old links)
