"""Google Calendar live layer (S51): windowed sync into the calendar_event tables.

Creator-owned enrichment only; NEVER read by recipient routes. Safe allow-list per
docs/s49 section 3; private-visibility events excluded at ingest (section 4).
"""
