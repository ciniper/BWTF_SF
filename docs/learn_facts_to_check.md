# Learn page: facts to check

The Learn page (/learn, features/learn/page.py + app/templates/learn/page.html) went live on 2026-10-08,
linked from Today's page below the hub cards, with these still to check. It stays `noindex` until they are;
drop that meta tag from the template once the list is done. Tick each line as it is confirmed.

- [ ] From the repo, already used on the site: the two plants and their outfall counts (shared/outfalls.py), the four state limits and Surfrider's caution tier (shared/standards.py), when the city began each indicator (Samples and Site Report Card notes), and both charts' numbers (the BeachWatch and CIWQS files).
- [ ] Confirmed by SFPUC's Sewer System Master Plan (Summary Report, Final Draft, March 2010): over 90% of the city is combined; the transport/storage structures ring the perimeter "like a moat", hold flow until the storm passes, and give discharges the equivalent of primary treatment, which is why they are called CSDs; flow is pumped to the plants; the Oceanside plant's outfall runs about four miles offshore (1986) and the Southeast plant's two outfalls go to the bay. Check that the 2010 numbers still hold before quoting any.
- [ ] The map: outfall positions come from shared/outfalls.py; the plant sites and the offshore pipes are placed by eye from the master plan's Figures 2-1 and 2-5 and need real coordinates; the brown ring is schematic and does not trace the real transport/storage structures or force mains. Storm replays use CIWQS start times and durations as reported, with volume spread evenly over each discharge; the handful of reports without a usable start time or duration are left out.
- [ ] The timeline: every stop names its source. The master plan stops follow its text and its Figure 1-1 (Southeast's year is 1952 in the text and 1962 in that figure; the text wins); the 2023 and 2026 stops are this site's own dates, to confirm with the team.
- [ ] The photos are SFPUC's, from the 2010 master plan; Chase cleared their use (2026-10-08). The plan credits its photographers as a group, not per photo. The plant photos are cut from its Figure 1-1 (which dates Southeast 1962; the text says 1952). Two captions are descriptions of uncaptioned chapter-opener photos, to confirm with SFPUC: the brick sewer and the surf.
- [ ] "How the lab counts": the method as IDEXX describes Enterolert in a Quanti-Tray/2000 (49 wells of about 1.86 mL and 48 of about 0.186 mL, a 1:10 dilution for seawater, 24 hours at 41 °C, long-wave UV). The readings are computed by maximum likelihood, the method behind the MPN tables: check them against IDEXX's printed table before quoting. "Millions by morning" is to confirm with a lab. The tray's well layout is schematic.
- [ ] To confirm with both the city lab and Surfrider's: that each uses this method for Enterococcus, as both the story and "Try it yourself" assume.
- [ ] To confirm with a reviewer: each indicator's "what" and "why" lines, and E. coli's "most strains are harmless".
- [ ] Photo sources to ask: SFPUC, the SF Public Library's historical photograph collection, OpenSFHistory. Use only with permission and credit each photo.

Not a main tab: adding "Learn" to the top bar and the phone bar would need the top bar's 320–1280 px fit re-checked.
