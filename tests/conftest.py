import os

# The trust gate short-circuits rule execution for un-reviewed configs.
# Tests exercise the pipeline directly, so bypass it globally.
os.environ.setdefault("BULLY_TRUST_ALL", "1")
