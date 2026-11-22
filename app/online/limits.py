"""Shared limits for bounded online catalogue details and artwork."""

MAX_IMAGE_BYTES = 1024**2

# A long-running show may expose multiple complete, independent lines. Keep
# each line bounded while permitting a reasonable number of such lines in one
# response and in the identity transaction that stores it.
MAX_CATALOG_ENTRIES = 8000
MAX_LINE_ENTRIES = 2000
