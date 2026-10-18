"""
Tests for SecretsFilter.
"""

import json
import pytest
from pathlib import Path
from security.secrets_filter import SecretsFilter


class TestSecretsFilter:
    """Test suite for secrets filtering."""

    def test_basic_string_scrubbing(self):
        """Test scrubbing secrets from plain text."""
        filter = SecretsFilter()
        filter.add_secret("my_secret_password")

        text = "The password is my_secret_password and it should be hidden"
        result = filter.scrub(text)

        assert "my_secret_password" not in result
        assert "[REDACTED:VAULT_SECRET]" in result
        assert result == "The password is [REDACTED:VAULT_SECRET] and it should be hidden"

    def test_multiple_secrets(self):
        """Test scrubbing multiple different secrets."""
        filter = SecretsFilter()
        filter.add_secret("secret1")
        filter.add_secret("secret2")

        text = "secret1 is here and secret2 is there"
        result = filter.scrub(text)

        assert "secret1" not in result
        assert "secret2" not in result
        assert result == "[REDACTED:VAULT_SECRET] is here and [REDACTED:VAULT_SECRET] is there"

    def test_multiple_occurrences(self):
        """Test scrubbing when a secret appears multiple times."""
        filter = SecretsFilter()
        filter.add_secret("repeated")

        text = "repeated and repeated and repeated"
        result = filter.scrub(text)

        assert "repeated" not in result
        assert result.count("[REDACTED:VAULT_SECRET]") == 3

    def test_empty_text(self):
        """Test scrubbing empty or None text."""
        filter = SecretsFilter()
        filter.add_secret("secret")

        assert filter.scrub("") == ""
        assert filter.scrub(None) is None

    def test_no_secrets(self):
        """Test that text without secrets passes through unchanged."""
        filter = SecretsFilter()
        filter.add_secret("my_api_key_xyz")

        text = "This text has no secrets"
        result = filter.scrub(text)

        assert result == text

    def test_scrub_json_dict(self):
        """Test scrubbing secrets from dict structures."""
        filter = SecretsFilter()
        filter.add_secret("api_key_12345")

        data = {
            "username": "alice",
            "password": "api_key_12345",
            "nested": {
                "token": "api_key_12345"
            }
        }

        result = filter.scrub_json(data)

        assert result["username"] == "alice"
        assert result["password"] == "[REDACTED:VAULT_SECRET]"
        assert result["nested"]["token"] == "[REDACTED:VAULT_SECRET]"
        assert "api_key_12345" not in json.dumps(result)

    def test_scrub_json_list(self):
        """Test scrubbing secrets from list structures."""
        filter = SecretsFilter()
        filter.add_secret("secret_value")

        data = ["public", "secret_value", {"key": "secret_value"}]

        result = filter.scrub_json(data)

        assert result[0] == "public"
        assert result[1] == "[REDACTED:VAULT_SECRET]"
        assert result[2]["key"] == "[REDACTED:VAULT_SECRET]"

    def test_scrub_json_primitives(self):
        """Test that non-string primitives pass through."""
        filter = SecretsFilter()
        filter.add_secret("secret")

        assert filter.scrub_json(42) == 42
        assert filter.scrub_json(3.14) == 3.14
        assert filter.scrub_json(True) is True
        assert filter.scrub_json(None) is None

    def test_scrub_jsonl_line(self):
        """Test scrubbing a JSONL line."""
        filter = SecretsFilter()
        filter.add_secret("secret_token")

        line = json.dumps({"command": "login", "token": "secret_token"})
        result = filter.scrub_jsonl_line(line)

        result_data = json.loads(result)
        assert result_data["command"] == "login"
        assert result_data["token"] == "[REDACTED:VAULT_SECRET]"

    def test_scrub_jsonl_invalid_json(self):
        """Test scrubbing invalid JSON falls back to text scrubbing."""
        filter = SecretsFilter()
        filter.add_secret("secret")

        line = "This is not JSON but has secret in it"
        result = filter.scrub_jsonl_line(line)

        assert "secret" not in result
        assert "[REDACTED:VAULT_SECRET]" in result

    def test_add_env_secrets(self, monkeypatch):
        """Test adding secrets from environment variables."""
        filter = SecretsFilter()

        monkeypatch.setenv("API_KEY", "env_secret_123")
        monkeypatch.setenv("PASSWORD", "env_password_456")

        filter.add_env_secrets("API_KEY", "PASSWORD")

        text = "The API_KEY is env_secret_123 and PASSWORD is env_password_456"
        result = filter.scrub(text)

        assert "env_secret_123" not in result
        assert "env_password_456" not in result

    def test_add_env_secrets_missing_var(self, monkeypatch):
        """Test that missing env vars are handled gracefully."""
        filter = SecretsFilter()

        # Don't set NONEXISTENT_VAR
        filter.add_env_secrets("NONEXISTENT_VAR")

        # Should not raise, just not add anything
        assert len(filter.secrets) == 0

    def test_duplicate_secrets(self):
        """Test that duplicate secrets are not added twice."""
        filter = SecretsFilter()

        filter.add_secret("duplicate")
        filter.add_secret("duplicate")

        assert len(filter.secrets) == 1

    def test_empty_secret_not_added(self):
        """Test that empty strings are not added as secrets."""
        filter = SecretsFilter()

        filter.add_secret("")
        filter.add_secret(None)

        assert len(filter.secrets) == 0

    def test_partial_secret_match(self):
        """Test that partial matches are handled correctly."""
        filter = SecretsFilter()
        filter.add_secret("secret")

        # "secret" is a substring of "secretive" - should still scrub
        text = "This is secretive behavior"
        result = filter.scrub(text)

        # This demonstrates current behavior - it WILL scrub substrings
        # If we want word-boundary matching, we'd need to change the implementation
        assert "[REDACTED:VAULT_SECRET]" in result

    def test_case_sensitive_scrubbing(self):
        """Test that scrubbing is case-sensitive."""
        filter = SecretsFilter()
        filter.add_secret("Secret")

        text1 = "Secret is here"
        text2 = "secret is here"

        result1 = filter.scrub(text1)
        result2 = filter.scrub(text2)

        assert "Secret" not in result1
        assert "secret" in result2  # Different case, not scrubbed
