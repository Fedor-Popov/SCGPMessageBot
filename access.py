"""Password verification and short-lived, single-operation authorizations."""

import hashlib
import hmac
import secrets
from time import monotonic


PASSWORD_SALT = bytes.fromhex("c8791974bb50403821a794f2b3507db1")
PASSWORD_HASH = bytes.fromhex(
    "89257b2cffd1ac04f330136a13842bf039e6af46bb901ae8742726368e099e0c9"
    "a2e47914331679fe080c734e6254af1560157278efedd9425301c20a81a7980"
)


def verify_password(password: str) -> bool:
    if len(password) > 256:
        return False
    candidate = hashlib.scrypt(password.encode("utf-8"), salt=PASSWORD_SALT, n=16384, r=8, p=1)
    return hmac.compare_digest(candidate, PASSWORD_HASH)


class PasswordAccess:
    """Bind each authorization to its user, chat, action and random nonce."""

    def __init__(self):
        self.grants = {}
        self.failures = {}

    def revoke(self, user_id: int, chat_id: int) -> None:
        self.grants.pop((user_id, chat_id), None)

    def authenticate(self, user_id: int, chat_id: int, action: str, password: str) -> str:
        self.revoke(user_id, chat_id)
        now = monotonic()
        attempts, until = self.failures.get(user_id, (0, 0))
        if now >= until:
            attempts = 0
        if attempts >= 5:
            raise ValueError("Too many attempts. Try again in five minutes.")
        if not verify_password(password):
            self.failures[user_id] = (attempts + 1, now + 300)
            raise ValueError("Incorrect password. Run the command again to retry.")
        self.failures.pop(user_id, None)
        nonce = secrets.token_hex(8)
        self.grants[(user_id, chat_id)] = (action, nonce, now + 900)
        return nonce

    def allowed(self, user_id: int, chat_id: int, action: str, nonce: str | None = None) -> bool:
        grant = self.grants.get((user_id, chat_id))
        return bool(grant and grant[0] == action and monotonic() < grant[2]
                    and (nonce is None or hmac.compare_digest(nonce, grant[1])))
