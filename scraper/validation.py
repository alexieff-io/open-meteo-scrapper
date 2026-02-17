"""Input validation utilities for security hardening."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger()

_ALLOWED_URL_SCHEMES = {"http", "https"}

_VALID_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_ -]{0,63}$")
_VALID_LABEL_KEY_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
_MAX_LABEL_VALUE_LENGTH = 256
_MAX_LABELS_PER_LOCATION = 20


def validate_url(url: str, field_name: str) -> None:
    """Validate a URL for safe use as an HTTP target.

    Rejects non-HTTP(S) schemes to prevent protocol-level SSRF.
    Logs a warning for private/link-local IP addresses.

    Args:
        url: The URL to validate.
        field_name: Config field name for error messages.

    Raises:
        ValueError: If the URL has an invalid scheme or format.
    """
    parsed = urlparse(url)

    if not parsed.scheme:
        raise ValueError(f"{field_name}: URL must include a scheme (http or https)")
    if parsed.scheme not in _ALLOWED_URL_SCHEMES:
        raise ValueError(
            f"{field_name}: URL scheme must be http or https, got '{parsed.scheme}'"
        )
    if not parsed.hostname:
        raise ValueError(f"{field_name}: URL must include a hostname")

    # Warn about IPs that look like cloud metadata or link-local
    hostname = parsed.hostname
    if hostname in ("169.254.169.254", "metadata.google.internal"):
        raise ValueError(
            f"{field_name}: cloud metadata endpoints are not allowed"
        )
    if hostname.startswith("169.254."):
        raise ValueError(f"{field_name}: link-local addresses are not allowed")


def sanitize_url(url: str) -> str:
    """Strip embedded credentials from a URL for safe logging.

    Args:
        url: The URL that may contain credentials.

    Returns:
        The URL with any userinfo (user:password@) removed.
    """
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        # Rebuild netloc without credentials
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        sanitized = parsed._replace(netloc=netloc)
        return sanitized.geturl()
    return url


def validate_location_name(name: str, index: int) -> None:
    """Validate a location name for safe use in metric labels.

    Args:
        name: The location name to validate.
        index: The index in the locations list for error messages.

    Raises:
        ValueError: If the name is invalid.
    """
    if not name:
        raise ValueError(f"locations[{index}].name is required")
    if not _VALID_NAME_PATTERN.match(name):
        raise ValueError(
            f"locations[{index}].name contains invalid characters: '{name}'. "
            "Use only alphanumeric, underscore, hyphen, or space (max 64 chars)."
        )


def validate_labels(labels: dict[str, str], location_name: str) -> None:
    """Validate label keys and values for safe use in Prometheus metrics.

    Args:
        labels: The label dictionary to validate.
        location_name: Location name for error context.

    Raises:
        ValueError: If any label key or value is invalid.
    """
    if len(labels) > _MAX_LABELS_PER_LOCATION:
        raise ValueError(
            f"Location '{location_name}' has {len(labels)} labels "
            f"(max {_MAX_LABELS_PER_LOCATION})"
        )
    for key, value in labels.items():
        if not _VALID_LABEL_KEY_PATTERN.match(key):
            raise ValueError(
                f"Location '{location_name}': invalid label key '{key}'. "
                "Use only alphanumeric and underscore, starting with a letter or underscore."
            )
        if len(str(value)) > _MAX_LABEL_VALUE_LENGTH:
            raise ValueError(
                f"Location '{location_name}': label value for '{key}' "
                f"exceeds {_MAX_LABEL_VALUE_LENGTH} characters"
            )


def escape_label_value(value: str) -> str:
    """Escape special characters in a Prometheus label value.

    Per the Prometheus exposition format spec, label values must have
    backslashes, double quotes, and newlines escaped.

    Args:
        value: The raw label value.

    Returns:
        The escaped label value safe for use in exposition format.
    """
    value = str(value)
    value = value.replace("\\", "\\\\")
    value = value.replace('"', '\\"')
    value = value.replace("\n", "\\n")
    return value
