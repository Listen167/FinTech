"""密码登录、短时锁定、认证审计和短期 JWT 服务。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from backend.app.db.models import (
    AuthEventRecord,
    AuthSessionRecord,
    PasswordCredentialRecord,
    RefreshTokenRecord,
    UserRecord,
)
from backend.app.security.passwords import PasswordManager
from backend.app.security.tokens import AccessTokenManager


DEMO_CREDENTIALS = {
    "user_001": "lin_an",
    "user_002": "zhou_ning",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AuthenticationError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    session_id: str
    token_id: str
    credential_version: int
    display_name: str
    phone_masked: str | None
    role: str
    risk_level: str

    def public_user(self) -> dict:
        return {
            "id": self.user_id,
            "display_name": self.display_name,
            "phone_masked": self.phone_masked,
            "role": self.role,
            "risk_level": self.risk_level,
        }


class AuthService:
    def __init__(
        self,
        engine: Engine,
        token_manager: AccessTokenManager,
        password_manager: PasswordManager | None = None,
        max_failed_attempts: int = 5,
        lock_minutes: int = 10,
        session_days: int = 7,
    ) -> None:
        if not 3 <= max_failed_attempts <= 10:
            raise ValueError("AUTH_MAX_FAILED_ATTEMPTS_OUT_OF_RANGE")
        if not 1 <= lock_minutes <= 60:
            raise ValueError("AUTH_LOCK_MINUTES_OUT_OF_RANGE")
        self.engine = engine
        self.tokens = token_manager
        self.passwords = password_manager or PasswordManager()
        self.max_failed_attempts = max_failed_attempts
        self.lock_minutes = lock_minutes
        self.session_days = session_days
        self._audit_key = token_manager.secret.encode("utf-8")

    def _fingerprint(self, value: str | None) -> str | None:
        if not value:
            return None
        return hmac.new(self._audit_key, value.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _token_hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _new_refresh_token(session_id: str) -> tuple[str, str]:
        token = f"{session_id}.{secrets.token_urlsafe(48)}"
        return token, AuthService._token_hash(token)

    @staticmethod
    def _new_csrf_token() -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        return token, AuthService._token_hash(token)

    @staticmethod
    def _revoke_session(
        database: Session,
        auth_session: AuthSessionRecord,
        reason: str,
        now: datetime,
    ) -> None:
        if auth_session.revoked_at is None:
            auth_session.revoked_at = now
            auth_session.revoke_reason = reason
        refresh_tokens = database.scalars(
            select(RefreshTokenRecord).where(
                RefreshTokenRecord.session_id == auth_session.id,
                RefreshTokenRecord.status == "ACTIVE",
            )
        ).all()
        for refresh_token in refresh_tokens:
            refresh_token.status = "REVOKED"
            refresh_token.revoked_at = now

    @staticmethod
    def _event(
        user_id: str | None,
        event_type: str,
        result: str,
        ip_hash: str | None,
        details: dict | None = None,
    ) -> AuthEventRecord:
        return AuthEventRecord(
            event_id=f"auth_evt_{uuid4().hex}",
            user_id=user_id,
            event_type=event_type,
            result=result,
            ip_hash=ip_hash,
            details=details or {},
            created_at=_utc_now(),
        )

    def initialize_demo_credentials(self, passwords: dict[str, str]) -> list[dict]:
        if set(passwords) != set(DEMO_CREDENTIALS):
            raise ValueError("DEMO_CREDENTIAL_SET_INVALID")
        password_hashes = {
            user_id: self.passwords.hash(password)
            for user_id, password in passwords.items()
        }
        initialized: list[dict] = []
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            for user_id, username in DEMO_CREDENTIALS.items():
                user = database.get(UserRecord, user_id)
                if user is None:
                    raise ValueError(f"DEMO_USER_NOT_FOUND:{user_id}")
                credential = database.get(PasswordCredentialRecord, user_id)
                if credential is None:
                    credential = PasswordCredentialRecord(
                        user_id=user_id,
                        username=username,
                        password_hash=password_hashes[user_id],
                        credential_version=1,
                        failed_attempts=0,
                        locked_until=None,
                        password_changed_at=_utc_now(),
                    )
                    database.add(credential)
                else:
                    credential.username = username
                    credential.password_hash = password_hashes[user_id]
                    credential.credential_version += 1
                    credential.failed_attempts = 0
                    credential.locked_until = None
                    credential.password_changed_at = _utc_now()
                    sessions = database.scalars(
                        select(AuthSessionRecord).where(
                            AuthSessionRecord.user_id == user_id,
                            AuthSessionRecord.revoked_at.is_(None),
                        )
                    ).all()
                    for auth_session in sessions:
                        self._revoke_session(
                            database,
                            auth_session,
                            "CREDENTIAL_REINITIALIZED",
                            _utc_now(),
                        )
                database.add(
                    self._event(
                        user_id,
                        "CREDENTIAL_INITIALIZED",
                        "SUCCEEDED",
                        None,
                        {"credential_version": credential.credential_version},
                    )
                )
                initialized.append({"user_id": user_id, "username": username})
        return initialized

    def authenticate(
        self,
        username: str,
        password: str,
        client_ip: str | None,
        user_agent: str | None,
    ) -> dict:
        normalized_username = username.strip().lower()
        now = _utc_now()
        failure_code: str | None = None
        authenticated: tuple[UserRecord, PasswordCredentialRecord, AuthSessionRecord] | None = None
        refresh_token_value: str | None = None
        csrf_token_value: str | None = None

        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            row = database.execute(
                select(PasswordCredentialRecord, UserRecord)
                .join(UserRecord, UserRecord.id == PasswordCredentialRecord.user_id)
                .where(func.lower(PasswordCredentialRecord.username) == normalized_username)
                .with_for_update()
            ).one_or_none()

            if row is None:
                self.passwords.consume_dummy_verify(password)
                database.add(
                    self._event(
                        None,
                        "LOGIN",
                        "INVALID_CREDENTIALS",
                        self._fingerprint(client_ip),
                        {"username_fingerprint": self._fingerprint(normalized_username)},
                    )
                )
                failure_code = "INVALID_CREDENTIALS"
            else:
                credential, user = row
                if credential.locked_until and credential.locked_until > now:
                    database.add(
                        self._event(
                            user.id,
                            "LOGIN",
                            "LOCKED",
                            self._fingerprint(client_ip),
                            {"locked_until": credential.locked_until.isoformat()},
                        )
                    )
                    failure_code = "ACCOUNT_TEMPORARILY_LOCKED"
                else:
                    if credential.locked_until:
                        credential.locked_until = None
                        credential.failed_attempts = 0
                    password_valid = self.passwords.verify(password, credential.password_hash)
                    if not password_valid:
                        credential.failed_attempts += 1
                        result = "INVALID_CREDENTIALS"
                        if credential.failed_attempts >= self.max_failed_attempts:
                            credential.locked_until = now + timedelta(minutes=self.lock_minutes)
                            result = "LOCKED"
                        database.add(
                            self._event(
                                user.id,
                                "LOGIN",
                                result,
                                self._fingerprint(client_ip),
                                {"failed_attempts": credential.failed_attempts},
                            )
                        )
                        failure_code = (
                            "ACCOUNT_TEMPORARILY_LOCKED"
                            if result == "LOCKED"
                            else "INVALID_CREDENTIALS"
                        )
                    elif user.status != "ACTIVE":
                        database.add(
                            self._event(
                                user.id,
                                "LOGIN",
                                "INACTIVE",
                                self._fingerprint(client_ip),
                            )
                        )
                        failure_code = "ACCOUNT_UNAVAILABLE"
                    else:
                        credential.failed_attempts = 0
                        credential.locked_until = None
                        auth_session_id = f"auths_{uuid4().hex}"
                        refresh_token_value, refresh_token_hash = self._new_refresh_token(auth_session_id)
                        csrf_token_value, csrf_token_hash = self._new_csrf_token()
                        auth_session = AuthSessionRecord(
                            id=auth_session_id,
                            user_id=user.id,
                            refresh_token_hash=refresh_token_hash,
                            csrf_token_hash=csrf_token_hash,
                            user_agent=(user_agent or "")[:255] or None,
                            ip_hash=self._fingerprint(client_ip),
                            created_at=now,
                            last_used_at=now,
                            expires_at=now + timedelta(days=self.session_days),
                            revoked_at=None,
                            revoke_reason=None,
                        )
                        database.add(auth_session)
                        database.add(
                            RefreshTokenRecord(
                                token_hash=refresh_token_hash,
                                session_id=auth_session.id,
                                user_id=user.id,
                                parent_token_hash=None,
                                status="ACTIVE",
                                created_at=now,
                                expires_at=auth_session.expires_at,
                                rotated_at=None,
                                revoked_at=None,
                            )
                        )
                        database.add(
                            self._event(
                                user.id,
                                "LOGIN",
                                "SUCCEEDED",
                                self._fingerprint(client_ip),
                                {"session_id": auth_session.id},
                            )
                        )
                        authenticated = (user, credential, auth_session)

        if failure_code or authenticated is None:
            raise AuthenticationError(failure_code or "INVALID_CREDENTIALS")
        if refresh_token_value is None or csrf_token_value is None:
            raise AuthenticationError("REFRESH_TOKEN_ISSUE_FAILED")

        user, credential, auth_session = authenticated
        access_token = self.tokens.create(user.id, auth_session.id, credential.credential_version)
        context = AuthContext(
            user_id=user.id,
            session_id=auth_session.id,
            token_id="",
            credential_version=credential.credential_version,
            display_name=user.display_name,
            phone_masked=user.phone_masked,
            role=user.role,
            risk_level=user.risk_level,
        )
        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": self.tokens.expires_in_seconds,
            "user": context.public_user(),
            "_refresh_token": refresh_token_value,
            "_csrf_token": csrf_token_value,
        }

    def refresh_access_token(
        self,
        refresh_token: str,
        csrf_token: str,
        client_ip: str | None,
        user_agent: str | None,
    ) -> dict:
        if "." not in refresh_token:
            self._record_token_rejection(None, client_ip, "MALFORMED_REFRESH_TOKEN")
            raise AuthenticationError("INVALID_REFRESH_TOKEN")
        session_id, _ = refresh_token.split(".", 1)
        if not session_id.startswith("auths_"):
            self._record_token_rejection(None, client_ip, "MALFORMED_REFRESH_SESSION")
            raise AuthenticationError("INVALID_REFRESH_TOKEN")

        now = _utc_now()
        presented_hash = self._token_hash(refresh_token)
        csrf_hash = self._token_hash(csrf_token)
        failure_code: str | None = None
        refreshed: tuple[UserRecord, PasswordCredentialRecord, AuthSessionRecord] | None = None
        new_refresh_token: str | None = None
        new_csrf_token: str | None = None

        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            auth_session = database.scalar(
                select(AuthSessionRecord)
                .where(AuthSessionRecord.id == session_id)
                .with_for_update()
            )
            if auth_session is None:
                database.add(
                    self._event(
                        None,
                        "TOKEN_REFRESH",
                        "REJECTED",
                        self._fingerprint(client_ip),
                        {"reason": "AUTH_SESSION_NOT_FOUND"},
                    )
                )
                failure_code = "INVALID_REFRESH_TOKEN"
            elif auth_session.revoked_at is not None or auth_session.expires_at <= now:
                database.add(
                    self._event(
                        auth_session.user_id,
                        "TOKEN_REFRESH",
                        "REJECTED",
                        self._fingerprint(client_ip),
                        {"reason": "AUTH_SESSION_UNAVAILABLE"},
                    )
                )
                failure_code = "AUTH_SESSION_UNAVAILABLE"
            else:
                token_record = database.get(RefreshTokenRecord, presented_hash)
                if token_record is None or token_record.session_id != auth_session.id:
                    database.add(
                        self._event(
                            auth_session.user_id,
                            "TOKEN_REFRESH",
                            "REJECTED",
                            self._fingerprint(client_ip),
                            {"reason": "REFRESH_TOKEN_NOT_FOUND"},
                        )
                    )
                    failure_code = "INVALID_REFRESH_TOKEN"
                elif token_record.status != "ACTIVE" or auth_session.refresh_token_hash != presented_hash:
                    token_record.status = "REUSED"
                    token_record.revoked_at = now
                    self._revoke_session(database, auth_session, "REFRESH_TOKEN_REUSED", now)
                    database.add(
                        self._event(
                            auth_session.user_id,
                            "REFRESH_REUSE_DETECTED",
                            "BLOCKED",
                            self._fingerprint(client_ip),
                            {"session_id": auth_session.id},
                        )
                    )
                    failure_code = "REFRESH_TOKEN_REUSED"
                elif not auth_session.csrf_token_hash or not secrets.compare_digest(
                    auth_session.csrf_token_hash, csrf_hash
                ):
                    database.add(
                        self._event(
                            auth_session.user_id,
                            "TOKEN_REFRESH",
                            "REJECTED",
                            self._fingerprint(client_ip),
                            {"reason": "CSRF_TOKEN_MISMATCH"},
                        )
                    )
                    failure_code = "CSRF_TOKEN_MISMATCH"
                else:
                    row = database.execute(
                        select(UserRecord, PasswordCredentialRecord)
                        .join(
                            PasswordCredentialRecord,
                            PasswordCredentialRecord.user_id == UserRecord.id,
                        )
                        .where(UserRecord.id == auth_session.user_id)
                    ).one_or_none()
                    if row is None or row[0].status != "ACTIVE":
                        self._revoke_session(database, auth_session, "ACCOUNT_UNAVAILABLE", now)
                        failure_code = "ACCOUNT_UNAVAILABLE"
                    else:
                        user, credential = row
                        token_record.status = "ROTATED"
                        token_record.rotated_at = now
                        new_refresh_token, new_refresh_hash = self._new_refresh_token(
                            auth_session.id
                        )
                        new_csrf_token, new_csrf_hash = self._new_csrf_token()
                        auth_session.refresh_token_hash = new_refresh_hash
                        auth_session.csrf_token_hash = new_csrf_hash
                        auth_session.last_used_at = now
                        auth_session.user_agent = (user_agent or "")[:255] or None
                        database.add(
                            RefreshTokenRecord(
                                token_hash=new_refresh_hash,
                                session_id=auth_session.id,
                                user_id=user.id,
                                parent_token_hash=presented_hash,
                                status="ACTIVE",
                                created_at=now,
                                expires_at=auth_session.expires_at,
                                rotated_at=None,
                                revoked_at=None,
                            )
                        )
                        database.add(
                            self._event(
                                user.id,
                                "TOKEN_REFRESH",
                                "SUCCEEDED",
                                self._fingerprint(client_ip),
                                {"session_id": auth_session.id},
                            )
                        )
                        refreshed = (user, credential, auth_session)

        if failure_code or refreshed is None:
            raise AuthenticationError(failure_code or "INVALID_REFRESH_TOKEN")
        if new_refresh_token is None or new_csrf_token is None:
            raise AuthenticationError("REFRESH_TOKEN_ISSUE_FAILED")

        user, credential, auth_session = refreshed
        return {
            "access_token": self.tokens.create(
                user.id, auth_session.id, credential.credential_version
            ),
            "token_type": "Bearer",
            "expires_in": self.tokens.expires_in_seconds,
            "user": AuthContext(
                user_id=user.id,
                session_id=auth_session.id,
                token_id="",
                credential_version=credential.credential_version,
                display_name=user.display_name,
                phone_masked=user.phone_masked,
                role=user.role,
                risk_level=user.risk_level,
            ).public_user(),
            "_refresh_token": new_refresh_token,
            "_csrf_token": new_csrf_token,
        }

    def validate_access_token(self, token: str, client_ip: str | None = None) -> AuthContext:
        try:
            payload = self.tokens.decode(token)
        except jwt.PyJWTError as error:
            self._record_token_rejection(None, client_ip, type(error).__name__)
            raise AuthenticationError("INVALID_ACCESS_TOKEN") from error

        now = _utc_now()
        user_id = str(payload["sub"])
        session_id = str(payload["sid"])
        failure_code: str | None = None
        context: AuthContext | None = None
        event_user_id: str | None = None
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            row = database.execute(
                select(AuthSessionRecord, UserRecord, PasswordCredentialRecord)
                .join(UserRecord, UserRecord.id == AuthSessionRecord.user_id)
                .join(PasswordCredentialRecord, PasswordCredentialRecord.user_id == UserRecord.id)
                .where(AuthSessionRecord.id == session_id)
            ).one_or_none()
            if row is None:
                failure_code = "AUTH_SESSION_NOT_FOUND"
            else:
                auth_session, user, credential = row
                event_user_id = user.id
                if auth_session.user_id != user_id:
                    failure_code = "TOKEN_SUBJECT_MISMATCH"
                elif auth_session.revoked_at is not None:
                    failure_code = "AUTH_SESSION_REVOKED"
                elif auth_session.expires_at <= now:
                    failure_code = "AUTH_SESSION_EXPIRED"
                elif user.status != "ACTIVE":
                    failure_code = "ACCOUNT_UNAVAILABLE"
                elif credential.credential_version != payload["credential_version"]:
                    failure_code = "CREDENTIAL_VERSION_MISMATCH"
                else:
                    auth_session.last_used_at = now
                    context = AuthContext(
                        user_id=user.id,
                        session_id=auth_session.id,
                        token_id=str(payload["jti"]),
                        credential_version=credential.credential_version,
                        display_name=user.display_name,
                        phone_masked=user.phone_masked,
                        role=user.role,
                        risk_level=user.risk_level,
                    )
            if failure_code:
                database.add(
                    self._event(
                        event_user_id,
                        "TOKEN_VALIDATE",
                        "REJECTED",
                        self._fingerprint(client_ip),
                        {"reason": failure_code},
                    )
                )

        if failure_code or context is None:
            raise AuthenticationError(failure_code or "INVALID_ACCESS_TOKEN")
        return context

    def logout(self, context: AuthContext, client_ip: str | None = None) -> None:
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            auth_session = database.get(AuthSessionRecord, context.session_id)
            if auth_session and auth_session.user_id == context.user_id and auth_session.revoked_at is None:
                self._revoke_session(database, auth_session, "USER_LOGOUT", _utc_now())
            database.add(
                self._event(
                    context.user_id,
                    "LOGOUT",
                    "SUCCEEDED",
                    self._fingerprint(client_ip),
                    {"session_id": context.session_id},
                )
            )

    def logout_all(self, context: AuthContext, client_ip: str | None = None) -> int:
        revoked_count = 0
        now = _utc_now()
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            sessions = database.scalars(
                select(AuthSessionRecord).where(
                    AuthSessionRecord.user_id == context.user_id,
                    AuthSessionRecord.revoked_at.is_(None),
                )
            ).all()
            for auth_session in sessions:
                self._revoke_session(database, auth_session, "USER_LOGOUT_ALL", now)
                revoked_count += 1
            database.add(
                self._event(
                    context.user_id,
                    "LOGOUT_ALL",
                    "SUCCEEDED",
                    self._fingerprint(client_ip),
                    {"revoked_sessions": revoked_count},
                )
            )
        return revoked_count

    def _record_token_rejection(
        self, user_id: str | None, client_ip: str | None, reason: str
    ) -> None:
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            database.add(
                self._event(
                    user_id,
                    "TOKEN_VALIDATE",
                    "REJECTED",
                    self._fingerprint(client_ip),
                    {"reason": reason},
                )
            )
