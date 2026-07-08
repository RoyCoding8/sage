"""Small, shared safeguards for preventing credential disclosure."""

import os
import re
from collections.abc import Iterable

_SECRET_ENV_NAMES = (
    "SAGE_QWEN_API_KEY",
    "SAGE_ALIBABA_ACCESS_KEY_ID",
    "SAGE_ALIBABA_ACCESS_KEY_SECRET",
    "SAGE_ADMIN_TOKEN",
)
_AUTHORIZATION_PATTERN = re.compile(
    r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+"
)


def redact_sensitive(value: object, extra_secrets: Iterable[str] = ()) -> str:
    """Return text with configured and explicitly supplied secrets removed."""
    text = str(value)
    secrets = [os.environ.get(name, "") for name in _SECRET_ENV_NAMES]
    secrets.extend(extra_secrets)
    ordered = sorted(
        {secret for secret in secrets if len(secret) >= 4}, key=len, reverse=True
    )
    for secret in ordered:
        text = text.replace(secret, "[REDACTED]")
    return _AUTHORIZATION_PATTERN.sub(r"\1[REDACTED]", text)
