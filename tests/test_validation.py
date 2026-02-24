"""Tests for scraper.validation module."""

from __future__ import annotations

import pytest

from scraper.validation import (
    escape_label_value,
    sanitize_url,
    validate_labels,
    validate_location_name,
    validate_url,
)


# --- validate_url ---

class TestValidateUrl:
    """Tests for validate_url()."""

    def test_accepts_http(self):
        validate_url("http://example.com", "test")

    def test_accepts_https(self):
        validate_url("https://example.com", "test")

    def test_accepts_http_with_port(self):
        validate_url("http://example.com:8080/path", "test")

    def test_rejects_missing_scheme(self):
        with pytest.raises(ValueError, match="must include a scheme"):
            validate_url("example.com", "test")

    def test_rejects_ftp_scheme(self):
        with pytest.raises(ValueError, match="must be http or https"):
            validate_url("ftp://example.com", "test")

    def test_rejects_file_scheme(self):
        with pytest.raises(ValueError, match="must be http or https"):
            validate_url("file:///etc/passwd", "test")

    def test_rejects_no_hostname(self):
        with pytest.raises(ValueError, match="must include a hostname"):
            validate_url("http://", "test")

    # Cloud metadata blocking
    def test_rejects_aws_metadata(self):
        with pytest.raises(ValueError, match="blocked destination"):
            validate_url("http://169.254.169.254/latest/meta-data", "test")

    def test_rejects_gcp_metadata(self):
        with pytest.raises(ValueError, match="blocked destination"):
            validate_url("http://metadata.google.internal/computeMetadata", "test")

    def test_rejects_aws_ecs_metadata(self):
        with pytest.raises(ValueError, match="blocked destination"):
            validate_url("http://169.254.170.2/v2/credentials", "test")

    def test_rejects_azure_metadata(self):
        with pytest.raises(ValueError, match="blocked destination"):
            validate_url("http://169.254.169.253/metadata/instance", "test")

    def test_rejects_localhost(self):
        with pytest.raises(ValueError, match="blocked destination"):
            validate_url("http://localhost/path", "test")

    # IP CIDR blocking
    def test_rejects_loopback_127(self):
        with pytest.raises(ValueError, match="blocked IP"):
            validate_url("http://127.0.0.1", "test")

    def test_rejects_loopback_127_other(self):
        with pytest.raises(ValueError, match="blocked IP"):
            validate_url("http://127.0.0.2", "test")

    def test_rejects_ipv6_loopback(self):
        with pytest.raises(ValueError, match="blocked IP"):
            validate_url("http://[::1]", "test")

    def test_rejects_link_local_169_254(self):
        with pytest.raises(ValueError, match="blocked IP"):
            validate_url("http://169.254.1.1", "test")

    # Private networks are warned but allowed
    def test_allows_private_10(self):
        validate_url("http://10.0.0.1:8080", "test")

    def test_allows_private_172(self):
        validate_url("http://172.16.0.1:8080", "test")

    def test_allows_private_192(self):
        validate_url("http://192.168.1.1:8080", "test")

    # Normal external URLs
    def test_allows_external_ip(self):
        validate_url("http://8.8.8.8", "test")

    def test_allows_external_hostname(self):
        validate_url("https://api.open-meteo.com/v1/forecast", "test")

    def test_error_message_includes_field_name(self):
        with pytest.raises(ValueError, match="my_field"):
            validate_url("ftp://evil.com", "my_field")

    # IPv6 blocked hostname
    def test_rejects_aws_ipv6_metadata(self):
        with pytest.raises(ValueError, match="blocked destination"):
            validate_url("http://[fd00:ec2::1]", "test")


# --- sanitize_url ---

class TestSanitizeUrl:
    """Tests for sanitize_url()."""

    def test_no_credentials_unchanged(self):
        url = "http://example.com:8080/path"
        assert sanitize_url(url) == url

    def test_strips_username_password(self):
        result = sanitize_url("http://user:pass@example.com:8080/path")
        assert "user" not in result
        assert "pass" not in result
        assert "example.com:8080/path" in result

    def test_strips_username_only(self):
        result = sanitize_url("http://user@example.com/path")
        assert "user" not in result
        assert "example.com" in result

    def test_preserves_port(self):
        result = sanitize_url("http://user:pass@example.com:9090/api")
        assert ":9090" in result


# --- validate_location_name ---

class TestValidateLocationName:
    """Tests for validate_location_name()."""

    def test_valid_name(self):
        validate_location_name("New York", 0)

    def test_valid_name_underscore(self):
        validate_location_name("my_location", 0)

    def test_valid_name_hyphen(self):
        validate_location_name("my-location", 0)

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="is required"):
            validate_location_name("", 0)

    def test_rejects_special_chars(self):
        with pytest.raises(ValueError, match="invalid characters"):
            validate_location_name("loc@tion!", 0)

    def test_rejects_leading_space(self):
        with pytest.raises(ValueError, match="invalid characters"):
            validate_location_name(" leading", 0)

    def test_error_includes_index(self):
        with pytest.raises(ValueError, match="locations\\[3\\]"):
            validate_location_name("", 3)

    def test_rejects_too_long(self):
        with pytest.raises(ValueError, match="invalid characters"):
            validate_location_name("a" * 65, 0)


# --- validate_labels ---

class TestValidateLabels:
    """Tests for validate_labels()."""

    def test_valid_labels(self):
        validate_labels({"region": "us-east"}, "TestLoc")

    def test_empty_labels(self):
        validate_labels({}, "TestLoc")

    def test_rejects_too_many_labels(self):
        labels = {f"key_{i}": f"val_{i}" for i in range(21)}
        with pytest.raises(ValueError, match="21 labels"):
            validate_labels(labels, "TestLoc")

    def test_rejects_invalid_key(self):
        with pytest.raises(ValueError, match="invalid label key"):
            validate_labels({"bad-key": "value"}, "TestLoc")

    def test_rejects_key_starting_with_digit(self):
        with pytest.raises(ValueError, match="invalid label key"):
            validate_labels({"1key": "value"}, "TestLoc")

    def test_rejects_too_long_value(self):
        with pytest.raises(ValueError, match="exceeds 256 characters"):
            validate_labels({"key": "x" * 257}, "TestLoc")

    def test_allows_underscore_key(self):
        validate_labels({"_internal": "val"}, "TestLoc")


# --- escape_label_value ---

class TestEscapeLabelValue:
    """Tests for escape_label_value()."""

    def test_no_special_chars(self):
        assert escape_label_value("hello") == "hello"

    def test_escapes_backslash(self):
        assert escape_label_value("a\\b") == "a\\\\b"

    def test_escapes_double_quote(self):
        assert escape_label_value('a"b') == 'a\\"b'

    def test_escapes_newline(self):
        assert escape_label_value("a\nb") == "a\\nb"

    def test_escapes_all_together(self):
        assert escape_label_value('a\\b"c\nd') == 'a\\\\b\\"c\\nd'

    def test_converts_non_string(self):
        assert escape_label_value(42) == "42"
