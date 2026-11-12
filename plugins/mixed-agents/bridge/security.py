"""Best-effort credential redaction, without returning credential values."""
from functools import lru_cache
import os
from pathlib import Path
import re
import tomllib

SECRET_KEY = re.compile(r"^(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|authorization|x-api-key|token|secret|[a-z0-9_]+_API_KEY)$", re.I)
ASSIGNMENT = re.compile(r'''(["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|authorization|x-api-key|token|secret)["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,}\]&]+)''', re.I)
TOKEN_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_-]{16,}"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
)


@lru_cache(maxsize=16)
def _file_secrets(path, modified, size):
    try:
        with open(path, "rb") as stream:
            data = tomllib.load(stream)
    except (OSError, ValueError):
        return ()
    values = []

    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if SECRET_KEY.fullmatch(key) and isinstance(child, str) and child:
                    values.append(child)
                elif key == "api_key_env" and isinstance(child, str):
                    secret = os.environ.get(child)
                    if secret:
                        values.append(secret)
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(data.get("providers", {}))
    return tuple(values)


def configured_secrets(config_path):
    """Internal only; never serialize this return value or cache keys."""
    try:
        path = Path(config_path)
        info = path.stat()
        values = _file_secrets(str(path), info.st_mtime_ns, info.st_size)
    except OSError:
        values = ()
    # Environment-backed keys can rotate without a config-file change.
    try:
        with open(config_path, "rb") as stream:
            providers = tomllib.load(stream).get("providers", {})
        names = [p.get("api_key_env") for p in providers.values() if isinstance(p, dict)]
        values += tuple(os.environ[n] for n in names if isinstance(n, str) and os.environ.get(n))
    except (OSError, ValueError, AttributeError):
        pass
    return tuple(sorted({v for v in values if len(v) >= 4}, key=len, reverse=True))


def redact(text, secrets=()):
    text = str(text)
    for secret in secrets:
        text = text.replace(secret, "[redacted]")
    for pattern in TOKEN_PATTERNS:
        text = pattern.sub("[redacted]", text)
    text = re.sub(r"\bBearer[ \t]+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", text, flags=re.I)
    text = re.sub(r"(\bauthorization\s*:\s*)(?:Basic|Bearer)[ \t]+[^\s,}\r\n]+",
                  r'\1"[redacted]"', text, flags=re.I)
    text = ASSIGNMENT.sub(lambda m: m[1] + '"[redacted]"', text)
    return re.sub(r"([?&](?:key|api_key|token|access_token)=)[^&#\s\"']+", r"\1[redacted]", text, flags=re.I)


def sanitize(value, secrets=()):
    if isinstance(value, dict):
        return {redact(k, secrets) if isinstance(k, str) else k:
                "[redacted]" if SECRET_KEY.fullmatch(str(k)) else sanitize(v, secrets)
                for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v, secrets) for v in value]
    return redact(value, secrets) if isinstance(value, str) else value
