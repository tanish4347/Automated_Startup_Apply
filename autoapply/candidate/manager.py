from sqlalchemy.orm import Session

from autoapply.models.vault import VaultIdentity


def get_active_identity(session: Session) -> VaultIdentity:
    """Return the candidate identity from the Vault tables (edited via the dashboard)."""
    identity = session.query(VaultIdentity).first()
    if identity is None:
        raise RuntimeError("No candidate identity in the Vault. Fill it in at /profile on the dashboard.")
    if not identity.full_name or not identity.email:
        raise ValueError("Vault identity is incomplete: name and email are required (dashboard /profile).")
    return identity
