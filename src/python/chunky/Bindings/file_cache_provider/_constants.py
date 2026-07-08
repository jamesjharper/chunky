"""Private SQLite and cache-entry constants."""

import re

SCHEMA_VERSION = 1
ENTRY_COMMITTED = "committed"
ENTRY_INVALID = "invalid"
ENTRY_DELETING = "deleting"
SAFE_EXTENSION = re.compile(r"^\.[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
