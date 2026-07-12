"""Account settings: profile, password, and TOTP MFA."""

from __future__ import annotations

import io

import pyotp
import qrcode
import qrcode.image.svg
from sqlalchemy import select
from sqlalchemy.orm import Session

from provenova_core.models import Account, MfaCredential

from ..security import hash_password, verify_password
from .accounts import audit


def update_profile(session: Session, account: Account, *, display_name: str | None = None,
                   email: str | None = None) -> Account:
    if display_name is not None and display_name.strip():
        account.display_name = display_name.strip()
    if email and email.strip() and email.strip().lower() != account.email.lower():
        new_email = email.strip().lower()
        clash = session.scalar(select(Account).where(Account.email == new_email))
        if clash is not None and clash.id != account.id:
            raise ValueError("that email is already in use")
        account.email = new_email
        account.email_verified = False  # re-verify on change
    session.flush()
    audit(session, workspace_id=None, account_id=account.id, action="account.profile_update")
    return account


def change_password(session: Session, account: Account, *, current: str, new: str) -> None:
    if not verify_password(current, account.password_hash):
        raise ValueError("current password is incorrect")
    if len(new) < 8:
        raise ValueError("new password must be at least 8 characters")
    account.password_hash = hash_password(new)
    # Revoke every other outstanding session for this account: a password change
    # must not leave an attacker's stolen cookie valid.
    account.token_version = (account.token_version or 0) + 1
    session.flush()
    audit(session, workspace_id=None, account_id=account.id, action="account.password_change")


# -- TOTP MFA ---------------------------------------------------------------

def get_mfa(session: Session, account: Account) -> MfaCredential | None:
    return session.scalar(select(MfaCredential).where(MfaCredential.account_id == account.id))


def mfa_enabled(session: Session, account: Account) -> bool:
    cred = get_mfa(session, account)
    return bool(cred and cred.enabled)


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="Provenova")


def qr_svg(uri: str) -> str:
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


def verify_code(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(code.strip().replace(" ", ""), valid_window=1)


def enable_mfa(session: Session, account: Account, secret: str) -> MfaCredential:
    cred = get_mfa(session, account)
    if cred is None:
        cred = MfaCredential(account_id=account.id, secret=secret, enabled=True)
        session.add(cred)
    else:
        cred.secret = secret
        cred.enabled = True
    session.flush()
    audit(session, workspace_id=None, account_id=account.id, action="account.mfa_enable")
    return cred


def disable_mfa(session: Session, account: Account) -> None:
    cred = get_mfa(session, account)
    if cred is not None:
        session.delete(cred)
        session.flush()
    audit(session, workspace_id=None, account_id=account.id, action="account.mfa_disable")
