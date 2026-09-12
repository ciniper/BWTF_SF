"""alert_log conventions shared by every reader (cso_history, the forecast's
observed-CSO override, /forecast/api/actuals).

``source`` says who wrote a row. Only the real-time watcher's rows are OUR
detections; 'manual' (dashboard button dispatches) and 'thread_shadow' (the
retired Python observer thread, which double-logged what pg_live already
logged during the Aug 2026 parallel run) would duplicate or pollute the
record. Readers filter to REALTIME_SOURCES and, because two writers ran side
by side for a while, dedupe transitions on (date, station, to).
"""
REALTIME_SOURCES = ("watcher", "pg_shadow", "pg_live")
ESCALATIONS = ("posted", "cso")
