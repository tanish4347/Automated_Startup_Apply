import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from itsdangerous import URLSafeTimedSerializer

# Use env var for secret, fallback to random dev key if not set
SECRET_KEY = os.environ.get("VAULT_SECRET_KEY", "dev-secret-key-for-local-vault").encode('utf-8')

salt = b'automated-job-application-vault'
kdf = PBKDF2HMAC(
    algorithm=hashes.SHA256(),
    length=32,
    salt=salt,
    iterations=480000,
)
fernet_key = base64.urlsafe_b64encode(kdf.derive(SECRET_KEY))
fernet = Fernet(fernet_key)

def encrypt_value(value: str) -> str:
    if value is None:
        return None
    return fernet.encrypt(value.encode('utf-8')).decode('utf-8')

def decrypt_value(encrypted_value: str) -> str:
    if encrypted_value is None:
        return None
    try:
        return fernet.decrypt(encrypted_value.encode('utf-8')).decode('utf-8')
    except Exception:
        return "[Decryption Error]"

def mask_sensitive(value: str, visible_chars: int = 4) -> str:
    if not value:
        return value
    if len(value) <= visible_chars:
        return "*" * len(value)
    return "*" * (len(value) - visible_chars) + value[-visible_chars:]

serializer = URLSafeTimedSerializer(SECRET_KEY)

def generate_session_token(user_id: str) -> str:
    return serializer.dumps({"user_id": user_id})

def verify_session_token(token: str, max_age: int = 3600) -> dict:
    try:
        return serializer.loads(token, max_age=max_age)
    except Exception:
        return None
