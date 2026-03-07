"""Tests for bot/config.py utilities."""

import os

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("DISCORD_CHANNEL_ID", "123456")
os.environ.setdefault("GLOWWORM_API_URL", "http://localhost:8000")
os.environ.setdefault("GLOWWORM_API_KEY", "test-api-key")

import pytest

from bot.config import _parse_bool


class TestParseBool:
    @pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", "YES"])
    def test_truthy_values(self, value: str) -> None:
        assert _parse_bool("TEST", value) is True

    @pytest.mark.parametrize("value", ["false", "False", "FALSE", "0", "no", "NO"])
    def test_falsy_values(self, value: str) -> None:
        assert _parse_bool("TEST", value) is False

    @pytest.mark.parametrize("value", ["banana", "maybe", "2", "tru", "fals"])
    def test_invalid_values_raise(self, value: str) -> None:
        with pytest.raises(RuntimeError, match="TEST"):
            _parse_bool("TEST", value)

    def test_error_message_includes_var_name(self) -> None:
        with pytest.raises(RuntimeError, match="MY_VAR"):
            _parse_bool("MY_VAR", "oops")

    def test_strips_whitespace_truthy(self) -> None:
        assert _parse_bool("TEST", "  true  ") is True

    def test_strips_whitespace_falsy(self) -> None:
        assert _parse_bool("TEST", "  false  ") is False
