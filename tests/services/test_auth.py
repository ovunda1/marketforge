"""API keys (M10): 32 random bytes shown once; the database keeps a SHA-256 of the key
and a non-secret prefix to find the row, never the key."""

from __future__ import annotations

import hashlib

from services.api.auth import generate_api_key, hash_api_key, new_account_row, split_api_key, verify_api_key


def test_a_key_is_random_prefixed_and_stored_only_as_a_hash() -> None:
    key, prefix, digest = generate_api_key()
    assert key.startswith(f"mf_{prefix}_") and len(key) > 50
    assert digest == hashlib.sha256(key.encode()).hexdigest()
    assert key not in digest and generate_api_key()[0] != key
    assert split_api_key(key) == prefix
    assert split_api_key("not-a-key") is None


def test_verification_compares_hashes() -> None:
    key, _, digest = generate_api_key()
    assert verify_api_key(key, digest)
    assert not verify_api_key(key + "x", digest)
    assert hash_api_key(key) == digest


def test_a_new_account_row_never_holds_the_key() -> None:
    row, key = new_account_row(name="alice", role="trader")
    assert row["name"] == "alice" and row["role"] == "trader"
    assert key not in repr(row)
    assert row["api_key_hash"] == hash_api_key(key) and key.startswith(f"mf_{row['api_key_prefix']}_")


def test_the_smoke_tools_percentiles_are_nearest_rank() -> None:
    from services.smoke import percentile

    values = [float(x) for x in range(1, 101)]
    assert (percentile(values, 50), percentile(values, 99), percentile(values, 100)) == (50.0, 99.0, 100.0)
    assert percentile([7.0], 99) == 7.0
