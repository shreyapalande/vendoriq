"""Smoke check: are GEMINI_API_KEY and GEMINI_MODEL set and working?

Makes ONE small real Gemini call through analyze() (the only function allowed to
call the model), on a made-up one-page document and one control. This also checks
that the live API accepts our JSON response schema.

Not a pytest test on purpose: tests never call Gemini (CLAUDE.md).

Usage, from the repo root:
    .venv/bin/python scripts/check_gemini.py       (Windows: .venv\\Scripts\\python scripts\\check_gemini.py)
"""

import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

import httpx  # noqa: E402
from google.genai import errors  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from app.ai.analyze import AnalysisError, analyze  # noqa: E402
from app.ai.citations import verify_citation  # noqa: E402
from app.ai.controls import CONTROLS  # noqa: E402
from app.config import get_settings  # noqa: E402

# Made up for this check; not taken from the eval vendors.
PAGES = ["Security overview\nMulti-factor authentication is required for all employees "
         "when they access internal systems and the production environment."]


def main() -> int:
    try:
        settings = get_settings()
    except ValidationError:
        print("FAIL: GEMINI_API_KEY and/or GEMINI_MODEL are not set (in the environment or the repo-root .env).")
        return 1
    print(f"model: {settings.gemini_model}  (key loaded, {len(settings.gemini_api_key)} characters; not printed)")

    mfa = [c for c in CONTROLS if c.code == "MFA"]
    try:
        result, usage = analyze(PAGES, mfa, date.today())
    except errors.APIError as exc:
        # 400 INVALID_ARGUMENT can mean a bad key or a schema the API rejects;
        # 404 NOT_FOUND usually means a wrong model name.
        print(f"FAIL: API error {exc.code} {exc.status}: {exc.message}")
        return 1
    except httpx.TimeoutException:
        print("FAIL: the request timed out on every attempt.")
        return 1
    except AnalysisError as exc:
        print(f"FAIL: the model answered, but the answer was unusable: {exc}")
        print(f"raw response: {exc.raw_text}")
        return 1

    finding = result.findings[0]
    print(f"OK: {finding.control_code} -> {finding.label.value}  (expected SUPPORTED)")
    print(f"reason: {finding.reason}")
    for citation in finding.citations:
        check = verify_citation(citation.quote, citation.page, PAGES)
        print(f"citation p{citation.page} [{check.match.value}]: {citation.quote!r}")
    print(f"tokens in/out: {usage.input_tokens}/{usage.output_tokens}  latency: {usage.duration_ms} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
