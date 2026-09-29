import os
import base64
from cryptography.fernet import Fernet
from autoapply.logging import get_logger

log = get_logger(__name__)

KEY_FILE = r'c:\Users\Tanish Mutha\Downloads\Automated apllications\.vault_key'

def get_encryption_key() -> bytes:
    key = os.environ.get('VAULT_MASTER_KEY')
    if not key:
        if os.path.exists(KEY_FILE):
            with open(KEY_FILE, 'r') as f:
                key = f.read().strip()
            os.environ['VAULT_MASTER_KEY'] = key
        else:
            key = Fernet.generate_key().decode('utf-8')
            with open(KEY_FILE, 'w') as f:
                f.write(key)
            os.environ['VAULT_MASTER_KEY'] = key
            log.warning("Generated a persistent vault key at .vault_key. Keep it safe!")
    return key.encode('utf-8')

def encrypt_value(value: str) -> str:
    if not value: return value
    f = Fernet(get_encryption_key())
    return f.encrypt(value.encode('utf-8')).decode('utf-8')

def decrypt_value(encrypted_value: str) -> str:
    if not encrypted_value: return encrypted_value
    try:
        f = Fernet(get_encryption_key())
        return f.decrypt(encrypted_value.encode('utf-8')).decode('utf-8')
    except Exception as e:
        log.error("decryption_error", error=str(e))
        return "<DECRYPTION_FAILED>"

def mask_sensitive(value: str, visible_chars: int = 4) -> str:
    if not value: return value
    if len(value) <= visible_chars: return "****"
    return "X" * (len(value) - visible_chars) + value[-visible_chars:]
