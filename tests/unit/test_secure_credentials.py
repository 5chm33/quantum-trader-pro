from __future__ import annotations

import os
import secrets
from pathlib import Path

import pytest

from quantum_trader.adapters.secure_credentials import (
    ALPACA_KEY_ID_CREDENTIAL,
    ALPACA_SECRET_KEY_CREDENTIAL,
    CREDENTIALS_DIRECTORY_ENV,
    OPERATOR_CONTROL_KEY_CREDENTIAL,
    SecureCredentialDirectory,
    SecureCredentialError,
)


def create_credentials(directory: Path) -> tuple[str, str, bytes]:
    directory.mkdir(mode=0o700)
    key_id = f"fixture-{secrets.token_hex(8)}"
    secret_key = secrets.token_urlsafe(32)
    control_key = secrets.token_bytes(32)
    for name, content in (
        (ALPACA_KEY_ID_CREDENTIAL, key_id.encode()),
        (ALPACA_SECRET_KEY_CREDENTIAL, secret_key.encode()),
        (OPERATOR_CONTROL_KEY_CREDENTIAL, control_key),
    ):
        path = directory / name
        path.write_bytes(content)
        path.chmod(0o600)
    return key_id, secret_key, control_key


def test_secure_bundle_loads_only_restrictive_named_files_and_redacts_repr(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "credentials"
    key_id, secret_key, control_key = create_credentials(directory)
    source = SecureCredentialDirectory.from_systemd_environment(
        {CREDENTIALS_DIRECTORY_ENV: str(directory)}
    )
    bundle = source.load_bundle()
    assert bundle.alpaca.key_id == key_id
    assert bundle.alpaca.secret_key == secret_key
    assert bundle.operator_control_key == control_key
    rendered = repr(bundle)
    assert "[REDACTED]" in rendered
    assert key_id not in rendered
    assert secret_key not in rendered
    assert control_key.hex() not in rendered


def test_credential_directory_requires_absolute_owned_nonwritable_directory(
    tmp_path: Path,
) -> None:
    with pytest.raises(SecureCredentialError, match="absolute"):
        SecureCredentialDirectory(Path("relative"))
    missing = tmp_path / "missing"
    with pytest.raises(SecureCredentialError, match="unavailable"):
        SecureCredentialDirectory(missing)

    writable = tmp_path / "writable"
    writable.mkdir(mode=0o700)
    writable.chmod(0o777)
    with pytest.raises(SecureCredentialError, match="writable"):
        SecureCredentialDirectory(writable)

    regular = tmp_path / "regular"
    regular.write_text("not a directory", encoding="utf-8")
    with pytest.raises(SecureCredentialError, match="directory"):
        SecureCredentialDirectory(regular)

    with pytest.raises(SecureCredentialError, match="not configured"):
        SecureCredentialDirectory.from_systemd_environment({})


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink and mode contract")
def test_credential_files_reject_symlinks_and_broad_permissions(tmp_path: Path) -> None:
    directory = tmp_path / "credentials"
    create_credentials(directory)
    source = SecureCredentialDirectory(directory)

    secret_file = directory / ALPACA_SECRET_KEY_CREDENTIAL
    secret_file.chmod(0o640)
    with pytest.raises(SecureCredentialError, match="0600"):
        source.load_bundle()
    secret_file.chmod(0o600)

    target = tmp_path / "outside"
    target.write_bytes(secrets.token_bytes(32))
    target.chmod(0o600)
    control_file = directory / OPERATOR_CONTROL_KEY_CREDENTIAL
    control_file.unlink()
    control_file.symlink_to(target)
    with pytest.raises(SecureCredentialError, match="symlink"):
        source.load_bundle()


def test_text_credentials_reject_multiline_whitespace_and_invalid_utf8(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "credentials"
    create_credentials(directory)
    source = SecureCredentialDirectory(directory)
    secret_file = directory / ALPACA_SECRET_KEY_CREDENTIAL

    secret_file.write_text("first\nsecond\n", encoding="utf-8")
    secret_file.chmod(0o600)
    with pytest.raises(SecureCredentialError, match="one line"):
        source.load_bundle()

    secret_file.write_text("contains space", encoding="utf-8")
    secret_file.chmod(0o600)
    with pytest.raises(SecureCredentialError, match="whitespace"):
        source.load_bundle()

    secret_file.write_bytes(b"\xff\xfe")
    secret_file.chmod(0o600)
    with pytest.raises(SecureCredentialError, match="UTF-8"):
        source.load_bundle()


def test_credential_names_sizes_and_file_types_fail_closed(tmp_path: Path) -> None:
    directory = tmp_path / "credentials"
    create_credentials(directory)
    source = SecureCredentialDirectory(directory)

    with pytest.raises(SecureCredentialError, match="name"):
        source.read_text("../escape")
    with pytest.raises(ValueError, match="bounds"):
        source.read_bytes(ALPACA_KEY_ID_CREDENTIAL, minimum_size=10, maximum_size=1)

    control_file = directory / OPERATOR_CONTROL_KEY_CREDENTIAL
    control_file.write_bytes(b"short")
    control_file.chmod(0o600)
    with pytest.raises(SecureCredentialError, match="size"):
        source.load_bundle()

    control_file.unlink()
    control_file.mkdir(mode=0o700)
    with pytest.raises(SecureCredentialError, match="regular"):
        source.load_bundle()
