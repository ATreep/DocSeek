from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.main import app


def _admin_headers(client: TestClient) -> dict[str, str]:
    token = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_superuser_is_immutable_and_can_assign_role_through_group():
    with TestClient(app) as client:
        headers = _admin_headers(client)
        roles = client.get("/api/admin/roles", headers=headers)
        assert roles.status_code == 200
        superuser = next(role for role in roles.json() if role["name"] == "Superuser")
        assert client.patch(f"/api/admin/roles/{superuser['id']}", json={"capabilities": []}, headers=headers).status_code == 409
        role = client.post("/api/admin/roles", json={"name": f"Custom-{uuid4().hex}", "capabilities": ["project.view"]}, headers=headers)
        assert role.status_code == 201
        group = client.post("/api/admin/groups", json={"name": f"Group-{uuid4().hex}"}, headers=headers).json()
        user = client.post("/api/admin/users", json={"username": f"analyst-{uuid4().hex}", "password": "secret-pass"}, headers=headers).json()
        assert client.post(f"/api/admin/groups/{group['id']}/members", json={"user_id": user["id"]}, headers=headers).status_code == 200
        assert client.post(f"/api/admin/groups/{group['id']}/roles", json={"role_id": role.json()["id"]}, headers=headers).status_code == 200
        login = client.post("/api/auth/login", json={"username": user["username"], "password": "secret-pass"})
        assert login.status_code == 200
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {login.json()['token']}"})
        assert me.json()["capabilities"] == ["project.view"]


def test_non_admin_cannot_manage_users_and_profile_password_change_invalidates_old_password():
    with TestClient(app) as client:
        admin = _admin_headers(client)
        user = client.post("/api/admin/users", json={"username": f"profile-{uuid4().hex}", "password": "old-pass"}, headers=admin).json()
        login = client.post("/api/auth/login", json={"username": user["username"], "password": "old-pass"}).json()
        headers = {"Authorization": f"Bearer {login['token']}"}
        assert client.get("/api/admin/users", headers=headers).status_code == 403
        changed = client.post("/api/profile/password", json={"current_password": "old-pass", "new_password": "new-pass"}, headers=headers)
        assert changed.status_code == 200
        assert client.post("/api/auth/login", json={"username": user["username"], "password": "old-pass"}).status_code == 401
        assert client.post("/api/auth/login", json={"username": user["username"], "password": "new-pass"}).status_code == 200
