"""The column titles a lineup shows under, one per part, in order (STAGES_DESIGN.md Part B 41).

Display only: the Model check (export_stage_builder) and the stages report (export_stages_report) read them, and
nothing the stage build imports does. The build pins every module it imports, so a title kept in shared/lineup.py
staled all of its sets; here a title change is a page regeneration. tests/test_lineup.py fails if any build pins
this file. The part names and codes stay in shared/lineup.py (WORDS, CODES), which the build reads.
"""
from __future__ import annotations

# S1 is the rain a set reads (its weather model's forecast, the gauges before; lineup.S1_RAIN), as the protocol and
# the flowchart call it
COLUMNS = (("geography", "Basins"), ("s1", "S1 · rain"), ("s2", "S2 · overflow model"),
           ("s3", "S3 · beach split"), ("s4", "S4 · lingering table"), ("s5", "S5 · live correction rule"))
