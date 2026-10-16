"""Admin / Super Admin rules for demoting and deleting accounts (service and HTTP routes)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import users as users_endpoints
from app.database.session import get_db
from app.dependencies.rbac import RequestUser, get_current_user
from app.dependencies.services import get_user_service
from app.enums import UserRole
from app.exceptions import AppException, ForbiddenError, app_exception_handler
from app.schemas.user import RoleUpdate, UserUpdate
from app.services.user_management_service import UserService

SUPER = UserRole.SUPER_ADMIN.value
ADMIN = UserRole.ADMIN.value
USER = UserRole.USER.value


def _user(uid: int, role: str, username: str = "someone") -> SimpleNamespace:
    return SimpleNamespace(
        id=uid,
        username=username,
        full_name=f"User {uid}",
        role=role,
        is_active=True,
        is_deleted=False,
        email=f"{username}@apcotex.com",
        title=None,
        phone=None,
        department=None,
        notes=None,
        outlook_sync_permission="own",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_login_at=None,
    )


def _service(target: SimpleNamespace) -> UserService:
    svc = UserService.__new__(UserService)
    svc.db = MagicMock()
    svc.db.execute.return_value.all.return_value = []
    svc.users = MagicMock()
    svc.users.get_or_raise.return_value = target
    svc.users.get_by_username.return_value = None
    svc.users.get_by_email.return_value = None

    def _soft_delete(u):
        u.is_deleted = True
        return u

    def _update(u, data):
        for key, value in data.items():
            setattr(u, key, value)
        return u

    svc.users.soft_delete.side_effect = _soft_delete
    svc.users.update.side_effect = _update
    svc.user_segments = MagicMock()
    svc.user_distributors = MagicMock()
    svc.audit = MagicMock()
    svc.list_segments_for_user = lambda _uid: []
    svc.list_distributors_for_user = lambda _uid: []
    return svc


# ---- demotion: PUT /users/{id}/role (set_role) --------------------------------------


def test_admin_cannot_demote_admin_via_set_role():
    svc = _service(_user(11, ADMIN))
    with pytest.raises(ForbiddenError):
        svc.set_role(11, RoleUpdate(role=UserRole.USER), actor="Admin", actor_role=ADMIN)
    svc.users.update.assert_not_called()


@pytest.mark.parametrize("new_role", [UserRole.USER, UserRole.ADMIN])
def test_admin_cannot_change_super_admin_role_via_set_role(new_role):
    svc = _service(_user(22, SUPER, "superadmin"))
    with pytest.raises(ForbiddenError):
        svc.set_role(22, RoleUpdate(role=new_role), actor="Admin", actor_role=ADMIN)
    svc.users.update.assert_not_called()


def test_super_admin_can_demote_admin_via_set_role():
    svc = _service(_user(11, ADMIN))
    updated = svc.set_role(11, RoleUpdate(role=UserRole.USER), actor="SA", actor_role=SUPER)
    assert updated.role == USER


def test_admin_can_still_promote_sales_owner_via_set_role():
    svc = _service(_user(5, USER))
    updated = svc.set_role(5, RoleUpdate(role=UserRole.ADMIN), actor="Admin", actor_role=ADMIN)
    assert updated.role == ADMIN


# ---- demotion: PUT /users/{id} (update_user) ----------------------------------------


def test_admin_cannot_demote_admin_via_update_user():
    svc = _service(_user(11, ADMIN))
    with pytest.raises(ForbiddenError):
        svc.update_user(11, UserUpdate(role=UserRole.USER), actor="Admin", actor_role=ADMIN)
    svc.users.update.assert_not_called()


def test_admin_cannot_demote_super_admin_via_update_user():
    svc = _service(_user(22, SUPER, "superadmin"))
    with pytest.raises(ForbiddenError):
        svc.update_user(22, UserUpdate(role=UserRole.USER), actor="Admin", actor_role=ADMIN)
    svc.users.update.assert_not_called()


def test_super_admin_can_demote_admin_via_update_user():
    svc = _service(_user(11, ADMIN))
    updated = svc.update_user(11, UserUpdate(role=UserRole.USER), actor="SA", actor_role=SUPER)
    assert updated.role == USER


def test_admin_can_still_edit_other_admin_profile_fields():
    """No new restriction: only demotion is blocked, not ordinary profile edits."""
    svc = _service(_user(11, ADMIN))
    updated = svc.update_user(
        11, UserUpdate(email="new.address@apcotex.com"), actor="Admin", actor_role=ADMIN
    )
    assert updated.email == "new.address@apcotex.com"
    assert updated.role == ADMIN


# ---- deletion ------------------------------------------------------------------------


def test_admin_cannot_delete_admin():
    svc = _service(_user(11, ADMIN))
    with pytest.raises(ForbiddenError):
        svc.delete_user(11, actor="Admin", actor_role=ADMIN, actor_id=1)
    svc.users.soft_delete.assert_not_called()


@pytest.mark.parametrize("actor_role,actor_id", [(ADMIN, 1), (SUPER, 99)])
def test_nobody_can_delete_super_admin(actor_role, actor_id):
    svc = _service(_user(22, SUPER, "superadmin"))
    with pytest.raises(ForbiddenError):
        svc.delete_user(22, actor="x", actor_role=actor_role, actor_id=actor_id)
    svc.users.soft_delete.assert_not_called()


def test_super_admin_can_delete_admin():
    svc = _service(_user(11, ADMIN))
    deleted = svc.delete_user(11, actor="SA", actor_role=SUPER, actor_id=22)
    assert deleted.is_deleted is True


def test_admin_can_still_delete_sales_owner():
    svc = _service(_user(5, USER))
    svc.delete_user(5, actor="Admin", actor_role=ADMIN, actor_id=1)
    svc.users.soft_delete.assert_called_once()


# ---- HTTP routes enforce the same rules ---------------------------------------------


def _client(caller: RequestUser, target: SimpleNamespace) -> TestClient:
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(users_endpoints.router, prefix="/api")
    app.dependency_overrides[get_user_service] = lambda: _service(target)
    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[get_current_user] = lambda: caller
    return TestClient(app, raise_server_exceptions=False)


ADMIN_CALLER = RequestUser(role=UserRole.ADMIN, name="Admin", user_id=1)
SUPER_CALLER = RequestUser(role=UserRole.SUPER_ADMIN, name="Super Admin", user_id=22)


def test_routes_block_admin_demotions_and_deletions():
    admin_target = _user(11, ADMIN)
    super_target = _user(22, SUPER, "superadmin")
    cases = [
        ("put", "/api/users/11/role", {"role": "user"}, admin_target),
        ("put", "/api/users/22/role", {"role": "user"}, super_target),
        ("put", "/api/users/22/role", {"role": "admin"}, super_target),
        ("put", "/api/users/11", {"role": "user"}, admin_target),
        ("put", "/api/users/22", {"role": "user"}, super_target),
        ("delete", "/api/users/11", None, admin_target),
        ("delete", "/api/users/22", None, super_target),
    ]
    for method, url, body, target in cases:
        client = _client(ADMIN_CALLER, target)
        response = getattr(client, method)(url, json=body) if body else getattr(client, method)(url)
        assert response.status_code == 403, (method, url, response.status_code, response.text)


def test_super_admin_routes_still_work():
    assert _client(SUPER_CALLER, _user(11, ADMIN)).delete("/api/users/11").status_code == 200
    assert _client(SUPER_CALLER, _user(22, SUPER)).delete("/api/users/22").status_code == 403
    role = _client(SUPER_CALLER, _user(11, ADMIN)).put("/api/users/11/role", json={"role": "user"})
    assert role.status_code == 200, role.text
