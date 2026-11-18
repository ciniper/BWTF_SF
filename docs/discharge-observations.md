# Discharge observations — 2026-11-13

Notes from looking at the live site during a discharge. Nothing investigated or changed yet.

## Forecast: basin-based status disagrees with the map
- The basin-based system in the forecast says **Ocean Beach is discharging**.
- The map shows **only Baker West** discharging.
- To check: which one is right, and why the basin rollup marks Ocean Beach.

## Baker: "2 new samples unpublished"
- The page says Baker has 2 new unpublished samples.
- These may just be the overflow (CSO) postings rather than new sampling results.
- To check: where the count comes from, and whether overflow postings should count as samples.
- SFPUC email this morning: Baker Beach West, Baker Beach East and Baker Beach at Lobos Creek are **proactively posted** (11/13) because of a combined sewer discharge from the **Sea Cliff II** outfall. No samples had been taken yet; the email says samples "will be collected today". So the "2 new" were most likely the postings, not results.
- The same email says no other SF beaches are posted, which also points against the forecast's Ocean Beach call.
- Watcher set for 11:45pm 11/13 to see whether the promised samples show up.

### Watcher check, 11:45pm 11/13
- **No new Baker result has been published.** In SFPUC's live beach feed (getBeaches), all three Baker stations still show sample date **11/09/26**. On DataSF (loaded 11:03pm tonight), the newest Baker samples are 11/09 (OCEAN#16, #15, #15EAST), and the newest sample citywide is 11/10. If SFPUC took the samples today as the email said, the results aren't out yet. They would normally reach the map tomorrow, 10:30am to 3pm.
- **The "2 unpublished" are Baker East and Baker at Lobos Creek.** The site shows both as "posted 6:32 AM · on a new sample, not published yet". Baker West isn't counted because the feed has a `cso` flag on it, which shows as a discharge.
- **Cause:** `shared/result_gap.py` treats every safe → posted change as caused by a lab result. The 6:32am change was the precautionary posting from the email, with no sample behind it, so the site wrongly says a new sample is waiting. Clue: lab-driven postings land around 8:10–8:40am, and this one came well before that.
- **Same problem later in the day:** Ocean Beach at Sloat, Ocean Beach at Balboa and Crane Cove were posted at about 12:45pm, also with no new samples. The site now says "5 new samples not published yet" (Ocean Beach 2, Baker 2, East Beaches 1), and all five are likely precautionary postings.
- **Possible fix (not done):** don't count a posting as a waiting sample when the station or its zone has an active discharge. Another option is to use the posting time: lab postings come around 8–9am.
- **Side note:** by tonight the feed shows `cso` flags at Fort Funston, Ocean Beach at Vicente, Ocean Beach at Lincoln, Mission Creek and Islais Creek. Ocean Beach really is discharging now, so the forecast's call holds by evening. The morning mismatch with the map still stands.

### 11/13 results are out early, through getStation (checked 11/14 ~9:55am)
SFPUC has a second endpoint, `getStation`, that returns each station's sample history (about three months) with the numbers: `COLI_TOTAL`, `COLI_FECAL`, `ENTERO`, `COLI_E`. Their map page used to call it from a "Bacteria Water Quality" button. That button is now commented out in the page code (comment `CR 250818 Remove button`). Snapshots are in `docs/discharge-observations/station/`.

**Already known:** this is TODO.md item B12, "Afternoon gap — use SFPUC's `getStation` numbers (held 2026-11-10)". It was found on 11/10 and held until a few more result days had been watched. The code doesn't call it yet. Today counts as one of those watched days (timing below).

**Querying it**
- In a browser (GET works): `https://infrastructure.sfwater.org/lims.asmx/getStation?stationId=4608`
- The map page's way: `curl -X POST -d "stationId=4608" https://infrastructure.sfwater.org/lims.asmx/getStation`
- The response is a JSON list wrapped in an XML `<string>` tag, newest sample first. `&lt;` in the numbers means "<".
- `https://infrastructure.sfwater.org/lims.asmx` lists every operation (getBeaches, getStation, getCSV) with a test form for each.
- **All stations in one call: `https://infrastructure.sfwater.org/lims.asmx/getCSV`.** Same numbers as getStation (it had the 11/13 storm rows at the same time) but for every station, about three months back (Aug 17 → now, ~750 rows). It's bare JSON with one row per analyte: `{"source": "OCEAN#18_SL", "sample_date": "11/13/26", "analyte": "ENTERO", "data": "2143"}`. It's keyed by the outfall/source code (e.g. `OCEAN#18_SL`), not the station id. getBeaches gives each station's code in its `posted`/`cso` fields, but not for every station (China is blank), so use `shared/outfalls.py` or the getStation `source` to map them. This is the feed the 2016–17 Poo Bot archive snapshotted. Its download button on SFPUC's map page is still wired up in the code.

**"sample_date" in getBeaches vs getStation/getCSV**
- In both, the value is a **collection date**: the day the water was sampled, not the day the result came in.
- getStation/getCSV list **every** sample with its numbers, and a new row shows up as soon as the lab enters it.
- getBeaches's `sample_date` is the one sample the map is **currently showing** for that station. It moves forward only when SFPUC updates the map. On 11/14 that was 2:03pm for Baker, about 4 hours after the 11/13 rows were in getStation.
- So any time the two disagree, a newer result exists that the map doesn't show yet. That gap is exactly the "not published yet" window, and comparing the two would tell us directly when there's a real waiting sample. It would replace the guess that `shared/result_gap.py` makes from posting changes.
- Station IDs: 4601 Fort Funston · 4602 OB Sloat · 4603 OB Vicente · 4604 OB Balboa · 4605 OB Lincoln · 4606 OB Pacheco · 4607 China · 4608 Baker West · 4609 Baker East · 4610 Baker Lobos · 4611 Crissy West · 4612 Crissy East · 4613 Aquatic Park · 4614 Hyde St Pier · 4615 Jackrabbit · 4616 Windsurfer Circle · 4617 Sunnydale · 4618 Mission Creek · 4619 Islais Creek · 4620 Crane Cove. The three "not routinely sampled" stations (4601, 4603, 4606) return no rows unless SFPUC storm-samples them (Vicente and Pacheco on 11/13).

**B12 timing data point (this result day)**
- 11/10, from TODO: getStation gained the Nov 9 row at the same check as the map date (1:48pm), and filled in one indicator at a time.
- 11/14: the 11/13 rows were in getStation by about 9:55am **with all three indicators filled**. At that point getBeaches still showed 11/09 and DataSF stopped at 11/10. So this time getStation came **before** the map date. The feed recorder will show when getBeaches catches up.

| Station | Sample | Entero | Fecal coliform | Total coliform |
|---|---|---|---|---|
| Baker Beach West (4608) | 11/13 | 10 | 15 | 90 |
| Baker Beach East (4609) | 11/13 | 86 | 15 | 80 |
| Baker Beach at Lobos Creek (4610) | 11/13 | 52 | 5 | 550 |
| China Beach (4607) | none; latest is 11/09 | 20 | 10 | 380 |

- So the promised samples **were** taken on 11/13. All three are under the state single-sample limits (Entero 104, fecal 400, total 10,000). Baker East at 86 is the closest.
- At the same moment, getBeaches still shows sample date 11/09 for all three, and DataSF only goes up to 11/10. **getStation got the results before both.**
- To consider: reading getStation would let us show results hours before the map updates, and it fixes the "not published yet" gap at its source. Things to check first: how early results show up there compared with getBeaches and DataSF, and whether SFPUC treats these numbers as final when they appear. Taking the button off their map might mean they'd rather the public not see them yet.

## SFPUC shows discharges as a separate symbol (11/14, 9:39am)
Snapshots in `docs/discharge-observations/`: our Today map, SFPUC's map (west side and Baker close-up), and the raw getBeaches feed.

- **What we see:** our map shows Baker West as "discharge". SFPUC's map shows Baker West as a **green dot**, with a **red CSO triangle** next to it.
- **How SFPUC draws it** (inline script on beachesandbay.html):
  - The **dot** comes only from the sample and posting fields: `s_color` Y/R/W, or `posted` + `p_color`. Green when neither applies.
  - A separate **CSO triangle** (`img/sign-cso.gif`) is added whenever the feed's `cso` field isn't null. It isn't drawn on the station. It goes at a hard-coded point per station (the outfall), e.g. Baker West 4608 → (-122.488506, 37.7938). Its popup says "Combined Sewer Discharge has occurred within the last 24-72 hours."
  - So one beach can be a green dot (last sample fine, not posted) with a discharge triangle beside it.
- **Feed right now:** Baker West is `cso=OCEAN#16_SL`, `posted=None`, so SFPUC shows a green dot plus a triangle. Baker East is `posted`, `p_color=G`, so it's an orange diamond. Baker at Lobos Creek's posting has come down since last night.
- **What we do:** `app/landing.py:338` returns `"discharge"` whenever `has_cso` is set, which overrides the posting/sample status. The two layers get merged into one.
- **Could we do the same?** Yes, all the data is already in the feed we read. We'd keep the station status (safe / posted / not sampled) on the dot and draw discharge as its own marker. That marker could sit at the outfall, using SFPUC's per-station points or our own outfall coordinates, or be a badge on the dot. The zone tiles and headline would then count "discharging" and "posted" separately. Most of the work would be design: legend, labels, and how a zone summarizes "safe dot + discharge nearby".
- Related: this might also explain the Ocean Beach confusion. Several Ocean Beach stations have `cso` flags while their dots are green, white or orange.

### Impact of taking discharge off the beach status (assessed 11/17)
Meaning: the beach dot shows only SFPUC's posting, and an active discharge gets its own symbol at the outfall, like SFPUC's map.

- **Map and Today page (small, contained).** One function decides it: `_station_status` in `app/landing.py` makes any station with a CSO flag "discharge", overriding the posting. Zone tiles, the "Sewage discharge at N beaches" headline and counts all come from it. The map side is `app/templates/_today_board.html` (colors, stacking, "· discharge" labels, legend). The map already has an **Outfalls** layer (34 permitted outfalls), and getBeaches names the outfall behind each flag (e.g. `OCEAN#19_SL` for Lincoln), so an active discharge can be a highlighted marker on that layer.
- **Alert watcher (bigger).** `classify` in `features/alerts/watcher.py` collapses each station to one state, cso > posted > ok. That's stored per station in Supabase (`watcher_state`), and every email, the Today past-week replay (`features/today/page.py` maps `cso` → `discharge`) and the "samples unpublished" guess (`shared/result_gap.py`) are built on its transitions. Fully separating them means tracking the CSO flag and the posting as two values: a storage change, plus updates to replay, result_gap, emails and their tests.
- **Upside of the watcher split:** a discharge becomes its own event, so Lincoln's 8:35/9:09 flag flip could be one discharge with one email (see "Too many alert emails").
- **Outfall mapping detail.** The Outfalls layer draws all 34 permitted outfalls from `shared/outfalls.py`, each with permit coordinates and a popup of its past year of reported discharges. But the live `cso` flag names the **beach station** (e.g. `OCEAN#19_SL` = the Lincoln station), not the structure that discharged. `outfalls_for_station` links them: Lincoln, Vicente and Fort Funston each have one outfall (Lincoln, Vicente, Lake Merced), so the exact outfall can be highlighted. Baker West has two (Sea Cliff Brick Sewer CSD-006 and Sea Cliff #2 CSD-007), Mission Creek seven, Islais Creek five, so we can only say one of those discharged. SFPUC's map sidesteps this with one triangle per station at a fixed point. Sea Cliff #1 (CSD-005) is the China Beach outfall; it maps only to China Beach (4607).
- **Not affected:** forecast models and scorecard. Their "discharge" is zone-level history from the CSO records, not the map status.
- **Safety question first:** on 11/17 Lincoln had a CSO flag but no posting. SFPUC-style, it would be a green dot next to a red triangle, which can read as "safe to swim", while our own CSO email says "avoid water contact". Middle option: the beach dot keeps the posting color and gets a red ring or badge when its outfall has an active discharge.
- **Also frees red** for high bacteria (see the yellow vs orange user feedback).
- **Suggested order:** change the map and Today first and leave the watcher as is (emails and replay keep working). Split the watcher later, together with email throttling.

## Feed recording
- `scripts/record_sfpuc_feed.py` reads SFPUC's getBeaches every 2 minutes. It writes a snapshot to `docs/discharge-observations/feed/` only when a station's `cso`, `s_color`, `posted`, `p_color` or `sample_date` changes. Each snapshot also stores our `/api/realtime` from the same moment. `feed/changes.log` lists each change in one line (station, field, old → new).
- Every 2 minutes it also reads **getCSV** (all stations, one call) and then **getStation** (all 20 stations), one right after the other. Each keeps a station's two newest sample dates and writes a snapshot (`*-getCSV.json`, `*-getStation.json`) when a new sample date appears or an indicator fills in. Every change goes into `changes.log`, tagged `[getBeaches]`, `[getCSV]` or `[getStation]`. A result day then reads in order: color flip, numbers in each feed, map sample date. That's the timing B12 is waiting for.
- **Open questions the log will answer:**
  - Does getCSV fill in at the same time as getStation, or does one lag?
  - Are the numbers always complete by the time the map's `sample_date` moves? On 11/10, getStation's row appeared at the same check as the map date and filled in one indicator at a time, with Entero last. On 11/14 the numbers were complete about 4 hours before the map date.
  - Do early values ever get revised?
### SFPUC: postings use preliminary counts
SFPUC's own words (source link to add):
> "In order to provide as rapid a response as possible the City proactively posts (and de-posts) beaches and makes public notifications based upon preliminary bacteria counts made available before final results are confirmed. The public is better served overall by timely notifications based upon preliminary counts than by the procedural delay needed to act upon confirmed counts."

What this may explain (to be checked against the recording):
- **Why the posting moves first on a normal result day** (about 8:30am, hours before the sample date or numbers appear). The posting decision uses preliminary counts that aren't in any feed we read.
- **Why the numbers show up later and sometimes one indicator at a time** (11/10). getStation/getCSV may be showing values as they're confirmed, not the preliminary ones.
- **Open question:** are the getStation/getCSV values preliminary or confirmed when they first appear? If they're confirmed, a posting can disagree with the numbers we'd show for a few hours, or for good if the final count differs. If they're preliminary, they could change later. The recorder logs any change to a value it has already seen (`ENTERO 86→…`), which would show a revision.
- For B12 this matters for the wording: show the numbers as "lab results" with their time, and don't claim they're the counts SFPUC acted on.

- Started 11/14 9:47am (getBeaches only). getStation was added at 9:16pm, getCSV at about 9:35pm, and all three run together from about 9:50pm 11/14 for 72 hours. Restarted 11/17 12:47am to run through Friday 11/20 ~11:59pm. Last night's 11:45pm watcher read is backfilled as the first file.

### Result day 11/14: what the recording shows (11/13 storm samples)
| Time | Feed | What changed |
|---|---|---|
| by ~9:55am | getStation | 11/13 numbers in for Baker (all three indicators); checked by hand, before the recorder read it |
| 2:03pm | getBeaches | Baker West / East / Lobos sample date 11/09 → 11/13 |
| 2:33pm | getBeaches | Ocean Beach (all five, including Vicente and Pacheco), Mission Creek, Islais Creek, Crane Cove sample date → 11/13 |
| 4:48pm | getBeaches | Baker West CSO flag cleared; Baker East and Crane Cove postings came down |
| (overnight to 9:39am) | getBeaches | Baker Lobos posting came down at some point before the recording started |

- **The order was the reverse of the usual one.** Normally the posting changes around 8–9am, then the sample date updates, then the numbers reach DataSF. This time the numbers came first, then the sample date about 4 hours later, and the postings came down about 2¾ hours after that, at 4:48pm. A precautionary posting probably comes down only after the results are in, not in the morning slot.
- So for B12: on this storm-sampling day getStation was ahead of the map's sample date by **at least 4 hours**.

### All 11/13 storm results (getCSV, read 11/14 ~9:30pm)
State single-sample limits: Entero 104, fecal 400, total 10,000. Over-limit values are in bold.

| Station | Source | Entero | Fecal | Total |
|---|---|---|---|---|
| Mission Creek | BAY#220 | **>24196** | **>6000** | **>16000** |
| Islais Creek | BAY#320 | **>24196** | **>6000** | **>16000** |
| OB Balboa | OCEAN#18 | **2143** | **4400** | **14600** |
| OB Lincoln | OCEAN#19 | **1842** | **4000** | **10400** |
| OB Pacheco | OCEAN#20 | **1187** | **2400** | 7300 |
| OB Sloat | OCEAN#21.1 | **1169** | **2600** | 6100 |
| OB Vicente | OCEAN#21 | **594** | **2700** | 6400 |
| Crane Cove | BAY#230 | 98 | 55 | 490 |
| Baker East | OCEAN#15EAST | 86 | 15 | 80 |
| Baker Lobos | OCEAN#15 | 52 | 5 | 550 |
| Baker West | OCEAN#16 | 10 | 15 | 90 |

- **Every Ocean Beach station sampled was far over.** Mission Creek and Islais Creek were off the top of the lab's scale (">" values). Baker, the only place the morning email named, came back clean. (Earlier I wrote that Vicente and Pacheco were "the only real exceedances". That was wrong; I had only looked at a few stations.)
- So the **forecast's Ocean Beach discharge call was right**. The morning map and email just hadn't caught up with it.
- **Vicente (4603) and Pacheco (4606)** are "not routinely sampled" stations that SFPUC sampled because of the storm. Normally getStation returns no rows for them. In the map feed they're `s_color=W` with `posted` + `p_color=G`, and SFPUC's code draws those as orange posted diamonds.
- What our site makes of them is the next thing to check.
- The recorder reads **getCSV** (all stations, one call) and **getStation** (20 calls), one after the other, every 2 minutes. Log entries are tagged `[getCSV]` and `[getStation]`.
- Restart after a reboot: `python3 scripts/record_sfpuc_feed.py --out docs/discharge-observations/feed` (it picks up from the last snapshot).

### Result day 11/15: what the recording shows (11/14 samples)
From `feed/changes.log`, all three feeds running the whole day, no failed reads.

| Time | Feed | What changed |
|---|---|---|
| 10:13am | getBeaches | Postings came down at OB Sloat, Vicente, Balboa |
| 10:28am | getBeaches | Vicente CSO flag cleared |
| 1:14:35pm | getBeaches | Sample date 11/13 → 11/14 for all five OB stations, Mission Creek, Islais Creek |
| 1:14:37pm | getCSV + getStation | 11/14 rows appear, **total coliform only** |
| 1:16:47pm | getCSV + getStation | Fecal coliform filled in |
| 1:40:33pm | getCSV + getStation | **Enterococcus filled in last** |
| 4:37pm | getBeaches | **New discharge:** Baker West CSO flag + posted; Baker East and Lobos posted |
| 4:41pm | getBeaches | Pacheco posting came down |
| 6:57pm | getBeaches | OB Sloat, Vicente, Balboa, Pacheco posted again |
| 6:59pm | getBeaches | Crane Cove posted |
| 7:02pm | getBeaches | Vicente CSO flag back |

11/14 results (state limits: Entero 104, fecal 400, total 10,000; over-limit in bold):

| Station | Entero | Fecal | Total |
|---|---|---|---|
| Islais Creek | **>24196** | **>6000** | **>16000** |
| Mission Creek | **738** | **3100** | **>16000** |
| OB Lincoln | **121** | 75 | 2300 |
| OB Pacheco | 98 | 40 | 360 |
| OB Sloat | 97 | 40 | 680 |
| OB Vicente | 75 | 65 | 390 |
| OB Balboa | 74 | 40 | 390 |

**Findings**
1. **The postings came down about 3 hours before any numbers existed in any feed** (10:13am vs 1:14pm). That fits SFPUC's statement that they act on preliminary counts we can't see.
2. **No head start this time.** The numbers and the map's sample date moved within 2 seconds of each other. On 11/14 the numbers were at least 4 hours ahead. So reading getStation/getCSV sometimes gains hours and sometimes nothing; it isn't a reliable lead.
3. **Enterococcus arrives last**, about 26 minutes after total coliform (same order as 11/10). For that window after the map date moves, the number that matters most for ocean beaches is still missing. Anything we build should say "Entero pending" rather than treat the row as complete.
4. **getCSV and getStation changed on exactly the same reads** every time (11/15 here, and 11/14 storm rows were in both together). One getCSV call is enough; getStation adds nothing.
5. **No revisions yet.** Every value went from empty to a number and stayed. Only two recorded result days so far.
6. **OB Lincoln came in at 121, over the Entero limit**, but no posting change for Lincoln shows in the log. Not yet checked what SFPUC's map or our site shows for it.
7. **A second discharge started the evening of 11/15** (Baker at 4:37pm, Ocean Beach and Crane Cove ~7pm). The recorder runs through Friday 11/20, so it should catch another full cycle: discharge, storm sampling, results, postings down.
8. The evening's changes also fed the "too many emails" note below: 7 posting changes in about 9 hours, some minutes apart.

### Result day 11/16: what the recording shows (11/15 samples)
| Time | Feed | What changed |
|---|---|---|
| 11:52am | getCSV only | Mission Creek **11/13** total coliform `>16000` → `16000` |
| 11:54am | getCSV only | ...and back to `>16000` |
| 12:52:42pm | getBeaches | Sample date → 11/15 for Fort Funston, OB Lincoln, Mission Creek, Islais Creek |
| 12:52:45pm | getCSV + getStation | 11/15 rows appear, **total coliform only** |
| 2:58pm | getBeaches | Mission Creek CSO flag cleared |
| 3:32pm | getCSV + getStation | **Enterococcus** filled in |
| 3:45–3:48pm | getStation, then getCSV | Fecal coliform filled in |
| 8:31pm, 8:35pm | all three | Reads failed: SFPUC answered `Bad` (a short outage; later reads worked) |

11/15 results: Islais Creek Entero **10,462**, Mission Creek **146**, OB Lincoln 41, Fort Funston 10 (storm-sampled; it's `OCEAN#22_SL` in getCSV).

**Findings**
1. **The getCSV-only change was a revision, the first one recorded.** Mission Creek's 11/13 total coliform lost its `>` qualifier for about 2 minutes, then got it back. getStation never showed it: its read of Mission comes a few seconds after getCSV's, so the value was most likely fixed in between. This looks like someone editing the lab record, not a difference between the two feeds. So **values can change after they're published**, at least the qualifier, three days later.
2. **The two feeds read the same live data**; they only differ when data is entered mid-read. At 3:45pm getStation caught Mission and Islais fecal (read late in its 20-call loop) while getCSV, read seconds earlier, didn't yet; getCSV caught all four at the next read. One getCSV call is still enough.
3. **Entero is not always last.** On 11/16 it came before fecal coliform (11/15 and 11/10 had it last). The steady part is that total coliform arrives with the map's sample date, and the other two come later in either order.
4. **Entero came 2h40m after the map's sample date moved** (12:52pm → 3:32pm), much longer than 11/15's 26 minutes. For most of the afternoon the map showed a new sample date with no Entero number anywhere. Anything we build must handle "sample date moved, Entero pending" as a normal state for hours.
5. **Same as 11/15, no head start:** the numbers and the map date moved within 3 seconds. Only the storm-sample day (11/14) had numbers hours ahead.

## Morning of 11/17: map mismatch and a repeat Lincoln email
Screenshot (phone, ~9:34am): `docs/discharge-observations/2026-11-17-0934-sfpuc-vs-ours-phone.png`.

**What the feed did** (`feed/changes.log`):
| Time | Change |
|---|---|
| 8:21am | Postings came down at all five OB stations, Fort Funston and all three Baker sites |
| 8:24am | Baker West and Fort Funston CSO flags cleared |
| 8:30am | Crissy Field East posted |
| 8:35am | **OB Lincoln CSO flag cleared** |
| 9:09am | **OB Lincoln CSO flag back on**; Fort Funston posted (yellow) |
| 9:16am | Fort Funston listed **twice** in getBeaches, once yellow and once orange; one entry (orange) by 9:24am |
| 9:27am | Fort Funston CSO flag back on; Vicente posted |
| 9:29am | Vicente posting down again, 2 minutes later |

**Repeat Lincoln email:** confirmed. SFPUC cleared Lincoln's discharge flag at 8:35am and set it again 34 minutes later, and we emailed on the second one. A 10–15 minute hold wouldn't have merged these; a rule like "don't re-alert the same station for the same discharge within N hours" would. Goes with the "too many emails" note below.

**Map mismatch at ~9:34am:**
- Feed state then: Lincoln and Vicente have a CSO flag but **no posting**; the OB beach dots are green.
- **SFPUC's map** shows green dots for the beaches plus separate red CSO triangles at the outfalls (and Fort Funston's orange posted diamond).
- **Our map** shows "OB Lincoln · discharge" and "OB Vicente · discharge" as red beach dots. Same issue as "SFPUC shows discharges as a separate symbol": we turn "discharge in the last 24–72 hours near here" into a beach status.
- **Our Today card** said "Vicente · OB Lincoln · 2 samples unpublished". getCSV had nothing newer than 11/15 for them at that point (its last change was 11/16 3:48pm), so this is the posting/CSO-flip false alarm again (`shared/result_gap.py`); B12 step 4 would fix it.
- **Feed quirk:** at 9:16am getBeaches listed Fort Funston twice with different posting colors, probably SFPUC editing it mid-read. Anything reading the feed should handle a duplicate station (we should check which one our code keeps).

## Surfrider field report, 11/13
- Full report: Google Doc "Field Report Storm baker-china 11-13-26" (Drive: SF BWTF Data Science / Field Report baker-china 11-13-26).
- In short: China Beach had three pipes flowing but no City discharge flag, posting or sample; Surfrider measured Entero 6,131. Baker was de-posted on a morning sample (52) while Surfrider's midday sample read 265.
- **To do:** add the construction observed on site to the report.
- **To do:** verify the outfall details in the report (which pipes are combined-sewer outfalls vs storm drains, and the Sea Cliff #1 / #2 assignments).
- **Sea Cliff #1 location check (11/17).** An SFPUC staff member told the user the photographed pipes weren't Sea Cliff #1. No public photo of the outfall itself turned up. What we have:
  - Our registry (`shared/outfalls.py`, from the NPDES permit table) puts Sea Cliff #1 (CSD-005) at 37.78778, -122.49167, "discharges at China Beach per permit". That's about 95 m west-northwest of the field report's sampling spot (37.78728, -122.49077, in front of the "Near East Pipe"), i.e. toward the **west cove** the report judged unsafe to enter. Permit coordinates are only good to roughly ±30 m.
  - SFPUC's Seacliff 1 & 2 pump station factsheet (`docs/discharge-observations/sfpuc-seacliff-pump-stations-factsheet-2024.pdf`, sfpuc.org/seacliffpumps) maps the **existing Seacliff 1 pump station at the far west end, on the bluff past the end of Sea Cliff Ave**, "in an area that is difficult to access". Seacliff 2 is near 26th Ave. The factsheet has a photo of Seacliff 2 "viewed from the beach", none of Seacliff 1 or any outfall.
  - So the three pipes on the main beach were most likely storm drains (or drainage from the NPS China Beach rehab, below), consistent with what SFPUC said. That doesn't change the 6,131 result; it changes what the pipes were.
  - **Construction (probably what the report saw):** SFPUC is replacing the Seacliff 1 pump station with a new underground one under Sea Cliff Ave plus ~1,000 ft of force main, and rehabbing Seacliff 2; both due to finish late 2026. The factsheet says the new Seacliff 1 station will give "additional protection from system overflows during heavy storms". Separately, the National Park Service and Golden Gate National Parks Conservancy are running a ~$25M China Beach rehabilitation, also under construction.
  - **Still open:** ask SFPUC where the Sea Cliff #1 outfall pipe is (and whether it's visible from the beach), and whether any construction bypass or temporary drainage discharges onto China Beach. The SF sea-level-rise report's wastewater chapter (sfplanning SLRVCA_Report_07.pdf) lists a "Sea Cliff #1 Outfall" figure; it was too large to fetch here, so it's unchecked.

## Discharge alert email
- On a phone, the discharge email didn't look very red. It should read as urgent at a glance.

## User feedback: yellow vs orange diamonds are hard to tell apart (11/16)
- A map user wrote in: on the map, it's hard to tell whether a Surfrider diamond is yellow (medium bacteria) or orange (high bacteria). They suggested changing orange to red.
- **Conflict to consider:** red is already the discharge/sewage color. Making "high bacteria" red too could blur the two. Options to weigh: a darker or more saturated orange, a different shape or icon for high, or giving discharge its own symbol (see "SFPUC shows discharges as a separate symbol") so red can mean high bacteria.
- Related: the discharge email also didn't look red enough on a phone (below), so check the colors together.

## Too many alert emails on a storm day
- Because our watcher checks so often, a storm day sends tons of emails.
- SFPUC sends far fewer updates (e.g. one morning posting email covering all three Baker sites), so subscribers aren't spammed.
- To consider: batching or throttling our alerts (e.g. one email per status change per area, or a daily cap) so a storm day doesn't flood people.
- **Postings flip on and off quickly, and each flip is an email.** On 11/15 (from `feed/changes.log`): Ocean Beach postings came down at 10:13am, Vicente's CSO cleared at 10:28am, Baker went back up at 4:37pm, Pacheco came down at 4:41pm, Ocean Beach went back up at 6:57pm, Crane Cove at 6:59pm, and Vicente's CSO returned at 7:02pm. That's 7 changes in about 9 hours, several only minutes apart. A short hold before alerting (e.g. wait ~10–15 minutes and send one email for everything that changed) would merge the ones that land close together.
- **Re-alert rule (decided 11/17, held):** if a station already had a discharge flag earlier the same Pacific calendar day, don't send another discharge email for it that day. If the flag comes back the next day, it emails as usual.
  - Why a calendar day and not 72 hours: if SFPUC takes the flag down and a real discharge happens the next day, people should hear about it.
  - The rule has to check when the flag was up, not when we last emailed. Lincoln's and Fort Funston's earlier discharge emails went out on Fri 11/13, so "already emailed today" would have caught neither of 11/17's repeats.
  - What it catches on 11/17:
    - Lincoln: off 8:34am, back on 9:11am, which sent the repeat email.
    - Fort Funston: off 8:24am, back on 9:27am, which sent another repeat.
    - (Times are from the Postings Timeline. The recorder logged 8:35 and 9:09 for Lincoln.)
  - Known edge: a flag that drops at 11:50pm and comes back at 12:10am still emails twice. That's acceptable.
  - How: the live sender is the Postgres tick (`db/migrations/004_pg_live_dispatch.sql`). It emails on every move to a worse status and has no memory of past alerts. A new migration would check `alert_log` for a discharge flag on that station (an upgrade into discharge, or a clear from it) earlier the same Pacific day.
  - **Audit, Fri 11/13 to Tue 11/17, someone subscribed to every station.** Rebuilt from the Online Postings Timeline (`/cso-history/api/events`, read 11/17 4:38pm). The sender sends one email per minute that has at least one new posting or discharge.

    | Day | Today | Same-day rule | Rule + 15-min hold |
    |---|---|---|---|
    | Fri 11/13 | 6 | 6 | 2 |
    | Sun 11/15 | 5 | 4 | 2 |
    | Tue 11/17 | 3 | 1 | 1 |
    | **Total** | **14** | **11** | **5** |

    Sat and Mon had only clears, so no emails.
    - The rule drops three emails: Vicente at 7:03pm on 11/15, and Lincoln at 9:11am and Fort Funston at 9:27am on 11/17.
    - Friday's six emails all came from first flags of the day, so the rule doesn't touch them. They cluster in two bursts (6:32–6:35am and 12:45–12:49pm), which is what the 15-minute hold would merge. That column is approximate: changes within 15 minutes of the first change go into one email. The window starts at the first change and doesn't restart on later ones, so an alert is never held more than 15 minutes.
    - **Trade-off to check:** Vicente's flag came back 8½ hours after it cleared at 10:28am, so it may have been a real second discharge. An all-stations subscriber still got the 6:58pm Ocean Beach email, but someone subscribed only to Vicente would have heard nothing. Check whether SFPUC reported a second discharge that evening.
    - Not cross-checked against the actual delivery records. The Supabase connector here points at a different project.
    - **Tool (built 11/17):** Admin → Email simulator (`/alerts/email-sim`, `features/alerts/email_sim.py`). Pick a date range, what someone subscribes to (every station, a zone or one station), a hold, the window type and the same-day rule. It compares the count with today's sender. `tests/test_email_sim.py` pins this audit's 14 / 11 / 5.
  - The new migration would still log the status change, so the Today replay and event start and end times stay right. The Python watcher (`features/alerts/watcher.py`) runs in shadow mode and would just need to match.
  - **Orange → red emails too.** On our map orange is posted and red is discharge. Posted → discharge counts as a worse status, so it emails right away, usually a few minutes after the posting email. Since Friday: Baker West 6:32 → 6:35am Fri, Lincoln 12:45 → 12:48pm Fri, Baker West 4:37 → 4:38pm Sun, two emails each time. The 15-minute hold merges each pair into one email that shows the discharge. The same-day rule treats posted → discharge as a discharge flag. Red → orange (discharge → posted) sends nothing, now or under the plan.
- **Idea (11/17, held): add a "Still in effect" section to every alert email.** Today an email lists only the stations that changed in that minute for that subscriber (`bwtf_dispatch_live` and `bwtf_render_alert`, `db/migrations/021_live_status_to_today.sql`). Nothing shows their other sites that are still posted or discharging.
  - The subject and lead stay "what just changed", so a new alert still stands out.
  - Below that, a new section lists the subscriber's other stations that are currently posted or discharging, read from the live station status when the email is sent.
  - This fits the same-day rule: a flag that comes back skips its own email, but it still shows in "Still in effect" on the next one. It also fits the hold: each email gives the whole picture at that moment.
  - Limit: clears don't send email, so the list is only current when an email goes out. If everything clears, nobody hears until the next alert.

## Getting results sooner (plan, held)
- **Full plan now in TODO.md B12** ("Afternoon gap — merge SFPUC's `getCSV` numbers under DataSF"). Diagram of the gap and the fix: `docs/discharge-observations/sample-gap.html` (published at https://claude.ai/artifact/LQtazG1muUA15bw5J4Q237).
- **What we found:** getStation/getCSV carry the bacteria numbers, and on 11/14 they had them at least 4 hours before the map's `sample_date` moved (details above; TODO.md B12).
- **Plan:**
  1. Let the recorder finish (through Friday 11/20) to collect more result days.
  2. Read getCSV (one call for all stations) and show numbers as "lab results, in at <time>" as soon as they appear, ahead of the map.
  3. Detect "new sample not published yet" by comparing getCSV's newest date with getBeaches's `sample_date`, replacing the posting-based guess in `shared/result_gap.py`.
- **Before building:** check whether early values get revised (preliminary vs confirmed; see the SFPUC quote) and whether SFPUC minds the numbers being shown before the map (they removed the button).

## Labels
- The map labels don't look good.
- The label branch (`claude/today-map-labels-dead75` / `design/today-map-labels`) should help. Re-check after it merges.
