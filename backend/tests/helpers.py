import io

from fastapi.testclient import TestClient


def upload_and_confirm_property(
    client: TestClient,
    project_id: str,
    headers: dict[str, str],
    filename: str,
    content: bytes,
    content_type: str,
    confirmed_filename: str | None = None,
) -> dict:
    staged = client.post(
        f"/api/projects/{project_id}/properties",
        files={"file": (filename, io.BytesIO(content), content_type)},
        headers=headers,
    )
    assert staged.status_code == 202
    staged_payload = staged.json()
    confirmed = client.post(
        f"/api/projects/{project_id}/property-imports/{staged_payload['import_id']}/confirm",
        json={"filename": confirmed_filename or filename},
        headers=headers,
    )
    assert confirmed.status_code == 202
    return confirmed.json()
