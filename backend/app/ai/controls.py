"""The 12 security controls VendorIQ assesses, and the 4 labels a finding can have.

Plain Python only (no FastAPI, database or AI imports), so the API, the worker,
the eval script and the PDF generator can all import it.
"""

from dataclasses import dataclass
from enum import StrEnum


# Defined once, here. app/ai/schemas.py imports this Enum rather than redefining it,
# so the labels can never drift apart (D-09).
#
# What each label means is defined in evals/LABELING.md, and the prompt in
# app/ai/analyze.py includes those same definitions, so the model is judged by the
# rules the expected labels follow.
#
# Keep the docstring below short and model-facing: the Gemini SDK sends it to the
# model as the schema description of the `label` field.
class Label(StrEnum):
    """The label of a finding for one control, as defined in the instructions."""

    SUPPORTED = "SUPPORTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONTRADICTORY_EVIDENCE = "CONTRADICTORY_EVIDENCE"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"


@dataclass(frozen=True)
class Control:
    code: str
    title: str
    description: str


CONTROLS: list[Control] = [
    Control(
        "MFA",
        "Multi-factor authentication",
        "Multi-factor authentication is required for workforce access to systems and data.",
    ),
    Control(
        "ENCRYPTION_AT_REST",
        "Encryption at rest",
        "Customer data is encrypted when stored, with managed keys.",
    ),
    Control(
        "ENCRYPTION_IN_TRANSIT",
        "Encryption in transit",
        "Data is encrypted in transit using current TLS.",
    ),
    Control(
        "ACCESS_REVIEWS",
        "Access reviews",
        "User access is reviewed periodically and removed promptly when no longer needed.",
    ),
    Control(
        "INCIDENT_RESPONSE",
        "Incident response",
        "A documented incident response plan exists, is tested, and defines customer notification.",
    ),
    Control(
        "BACKUP_DR",
        "Backup and disaster recovery",
        "Data is backed up regularly and a disaster recovery plan is tested.",
    ),
    Control(
        "VULN_MGMT",
        "Vulnerability management",
        "Vulnerabilities are scanned and patched on a defined schedule, with regular penetration tests.",
    ),
    Control(
        "LOGGING_MONITORING",
        "Logging and monitoring",
        "Security-relevant events are logged, retained and monitored for alerts.",
    ),
    Control(
        "DATA_RETENTION",
        "Data retention and deletion",
        "Customer data is retained only as long as needed and deleted on request or contract end.",
    ),
    Control(
        "SUBPROCESSORS",
        "Subprocessors",
        "Third parties that process customer data are listed and assessed.",
    ),
    Control(
        "SECURITY_TRAINING",
        "Security awareness training",
        "Staff complete security awareness training at least annually.",
    ),
    Control(
        "CERTIFICATION",
        "Independent certification",
        "The vendor holds a current independent certification or audit report (e.g. SOC 2, ISO 27001).",
    ),
]

CONTROL_CODES: list[str] = [control.code for control in CONTROLS]
