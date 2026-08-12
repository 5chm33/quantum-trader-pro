"""Strict file-based secret loading for a future paper-only operator service."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from quantum_trader.adapters.alpaca_paper import AlpacaPaperCredentials
from quantum_trader.domain.operator import MIN_OPERATOR_KEY_BYTES

CREDENTIALS_DIRECTORY_ENV = "CREDENTIALS_DIRECTORY"
MAX_TEXT_SECRET_BYTES = 4096
MAX_CONTROL_KEY_BYTES = 4096
ALPACA_KEY_ID_CREDENTIAL = "alpaca_key_id"
ALPACA_SECRET_KEY_CREDENTIAL = "_".join(("alpaca", "secret", "key"))
OPERATOR_CONTROL_KEY_CREDENTIAL = "operator_control_key"


class SecureCredentialError(RuntimeError):
    """A credential source violated the strict out-of-band secret contract."""


@dataclass(frozen=True, slots=True, repr=False)
class SecureCredentialBundle:
    alpaca: AlpacaPaperCredentials
    operator_control_key: bytes

    def __post_init__(self) -> None:
        if len(self.operator_control_key) < MIN_OPERATOR_KEY_BYTES:
            raise SecureCredentialError("operator control key is too short")

    def __repr__(self) -> str:
        return "SecureCredentialBundle(alpaca=[REDACTED], operator_control_key=[REDACTED])"


class SecureCredentialDirectory:
    """Read named credential files from one absolute, non-symlink directory."""

    def __init__(self, directory: Path) -> None:
        if not directory.is_absolute():
            raise SecureCredentialError("credential directory must be absolute")
        if not _secure_open_supported():
            raise SecureCredentialError(
                "secure descriptor-relative credential reads are unavailable"
            )
        try:
            metadata = directory.lstat()
        except OSError as exc:
            raise SecureCredentialError("credential directory is unavailable") from exc
        _validate_directory(metadata)
        self._directory = directory
        self._directory_identity = (metadata.st_dev, metadata.st_ino)

    @classmethod
    def from_systemd_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> SecureCredentialDirectory:
        source = os.environ if environment is None else environment
        raw = source.get(CREDENTIALS_DIRECTORY_ENV)
        if raw is None or not raw.strip():
            raise SecureCredentialError(f"{CREDENTIALS_DIRECTORY_ENV} is not configured")
        return cls(Path(raw))

    def load_bundle(
        self,
        *,
        key_id_name: str | None = None,
        secret_key_name: str | None = None,
        operator_key_name: str | None = None,
    ) -> SecureCredentialBundle:
        return SecureCredentialBundle(
            alpaca=AlpacaPaperCredentials(
                key_id=self.read_text(key_id_name or ALPACA_KEY_ID_CREDENTIAL),
                secret_key=self.read_text(secret_key_name or ALPACA_SECRET_KEY_CREDENTIAL),
            ),
            operator_control_key=self.read_bytes(
                operator_key_name or OPERATOR_CONTROL_KEY_CREDENTIAL,
                minimum_size=MIN_OPERATOR_KEY_BYTES,
                maximum_size=MAX_CONTROL_KEY_BYTES,
            ),
        )

    def read_text(self, name: str) -> str:
        raw = self.read_bytes(
            name,
            minimum_size=1,
            maximum_size=MAX_TEXT_SECRET_BYTES,
        )
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecureCredentialError("text credential is not valid UTF-8") from exc
        if decoded.endswith("\n"):
            decoded = decoded[:-1]
        if not decoded or "\n" in decoded or "\r" in decoded:
            raise SecureCredentialError("text credential must contain exactly one line")
        if any(character.isspace() for character in decoded):
            raise SecureCredentialError("text credential must not contain whitespace")
        return decoded

    def read_bytes(
        self,
        name: str,
        *,
        minimum_size: int,
        maximum_size: int,
    ) -> bytes:
        normalized = _credential_name(name)
        if minimum_size < 1 or maximum_size < minimum_size:
            raise ValueError("credential size bounds are invalid")
        directory_fd = self._open_directory()
        try:
            return _read_secure_file(
                directory_fd,
                normalized,
                minimum_size=minimum_size,
                maximum_size=maximum_size,
            )
        finally:
            os.close(directory_fd)

    def _open_directory(self) -> int:
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            directory_fd = os.open(self._directory, flags)
        except OSError as exc:
            raise SecureCredentialError(
                "credential directory could not be opened securely"
            ) from exc
        try:
            metadata = os.fstat(directory_fd)
            _validate_directory(metadata)
            if (metadata.st_dev, metadata.st_ino) != self._directory_identity:
                raise SecureCredentialError("credential directory identity changed")
        except Exception:
            os.close(directory_fd)
            raise
        return directory_fd


def _read_secure_file(
    directory_fd: int,
    name: str,
    *,
    minimum_size: int,
    maximum_size: int,
) -> bytes:
    try:
        path_metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as exc:
        raise SecureCredentialError("required credential file is unavailable") from exc
    if stat.S_ISLNK(path_metadata.st_mode):
        raise SecureCredentialError("credential file must not be a symlink")
    file_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        file_fd = os.open(name, file_flags, dir_fd=directory_fd)
    except OSError as exc:
        raise SecureCredentialError("credential file could not be opened securely") from exc
    try:
        opened_metadata = os.fstat(file_fd)
        _validate_file(
            opened_metadata,
            minimum_size=minimum_size,
            maximum_size=maximum_size,
        )
        if (opened_metadata.st_dev, opened_metadata.st_ino) != (
            path_metadata.st_dev,
            path_metadata.st_ino,
        ):
            raise SecureCredentialError("credential file identity changed before read")
        with os.fdopen(file_fd, "rb", closefd=False) as handle:
            raw = handle.read(maximum_size + 1)
        final_metadata = os.fstat(file_fd)
        if (
            final_metadata.st_size != opened_metadata.st_size
            or final_metadata.st_mtime_ns != opened_metadata.st_mtime_ns
            or len(raw) != opened_metadata.st_size
        ):
            raise SecureCredentialError("credential file changed during the read")
        return raw
    finally:
        os.close(file_fd)


def _validate_directory(metadata: os.stat_result) -> None:
    if stat.S_ISLNK(metadata.st_mode):
        raise SecureCredentialError("credential directory must not be a symlink")
    if not stat.S_ISDIR(metadata.st_mode):
        raise SecureCredentialError("credential path must be a directory")
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise SecureCredentialError("credential directory must not be group- or world-writable")
    _require_owner(metadata.st_uid, "credential directory")


def _validate_file(
    metadata: os.stat_result,
    *,
    minimum_size: int,
    maximum_size: int,
) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise SecureCredentialError("credential file must be regular")
    _require_owner(metadata.st_uid, "credential file")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise SecureCredentialError("credential file permissions must be 0600 or stricter")
    if not minimum_size <= metadata.st_size <= maximum_size:
        raise SecureCredentialError("credential file size is outside allowed bounds")


def _credential_name(value: str) -> str:
    normalized = value.strip()
    if (
        not normalized
        or normalized in {".", ".."}
        or "/" in normalized
        or "\\" in normalized
        or not normalized.replace("_", "").replace("-", "").isalnum()
    ):
        raise SecureCredentialError("credential name has invalid characters")
    return normalized


def _require_owner(owner_uid: int, label: str) -> None:
    get_euid = getattr(os, "geteuid", None)
    if get_euid is not None and owner_uid != get_euid():
        raise SecureCredentialError(f"{label} is not owned by the service user")


def _secure_open_supported() -> bool:
    return (
        all(hasattr(os, attribute) for attribute in ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW"))
        and os.open in os.supports_dir_fd
    )
