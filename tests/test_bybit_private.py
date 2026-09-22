import hashlib
import hmac

from trading_copilot.services.bybit_private import BybitReadOnlyClient


def test_canonical_query_is_sorted_and_skips_none():
    query = BybitReadOnlyClient.canonical_query(
        {"settleCoin": "USDT", "category": "linear", "cursor": None}
    )
    assert query == "category=linear&settleCoin=USDT"


def test_signature_matches_bybit_v5_get_payload():
    client = BybitReadOnlyClient(
        api_key="test-key",
        api_secret="test-secret",
        recv_window=5000,
    )
    timestamp = 1_700_000_000_000
    query = "category=linear&settleCoin=USDT"
    expected_payload = f"{timestamp}test-key5000{query}"
    expected = hmac.new(
        b"test-secret",
        expected_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    assert client.signature(timestamp_ms=timestamp, query_string=query) == expected


def test_client_requires_credentials():
    try:
        BybitReadOnlyClient(api_key="", api_secret="")
    except ValueError as exc:
        assert "required" in str(exc).lower()
    else:
        raise AssertionError("client should reject missing credentials")
