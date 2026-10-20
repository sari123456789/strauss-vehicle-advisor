"""AI-assisted payslip PDF import (AI Engine application layer).

Upload -> deterministic text/candidate extraction -> optional AI interpretation of redacted
candidate lines -> reviewable draft -> explicit employee approval -> one version-checked
`actual_update` through the Business port. See docs/proposals/payslip-import.md.
"""
