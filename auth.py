from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets


PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 600_000
LOGIN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+-]{2,63}$")


def normalize_login_id(login_id: str) -> str:
    return login_id.strip().casefold()


def validate_login_id(login_id: str) -> str:
    normalized = normalize_login_id(login_id)
    if not LOGIN_ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            "ログインIDは3〜64文字の半角英数字と . _ @ + - で入力してください。"
        )
    return normalized


def validate_password(password: str) -> None:
    if len(password) < 8:
        raise ValueError("パスワードは8文字以上で入力してください。")
    if len(password) > 128:
        raise ValueError("パスワードは128文字以内で入力してください。")


def hash_password(password: str) -> str:
    validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return "$".join(
        (
            PASSWORD_ALGORITHM,
            str(PASSWORD_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded_password: str | None) -> bool:
    if not encoded_password:
        return False
    try:
        algorithm, iterations_text, salt_text, digest_text = encoded_password.split(
            "$", 3
        )
        if algorithm != PASSWORD_ALGORITHM:
            return False
        iterations = int(iterations_text)
        if iterations <= 0:
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
    except (TypeError, ValueError, UnicodeEncodeError):
        return False

    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(actual, expected)


# Used for nonexistent accounts so failed logins take roughly the same amount
# of work and do not reveal whether a login ID exists through response timing.
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(24))
