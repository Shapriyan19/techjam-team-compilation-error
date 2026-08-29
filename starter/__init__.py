"""Participant starter package."""

from starter.env_file import load_env_file

# Loaded once, at import, so any entry point - the evaluator, the dry-run
# script, or a harness that imports Agent directly - sees the same credentials
# and overrides. Real environment variables always win over the file.
load_env_file()
