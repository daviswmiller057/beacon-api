from datetime import datetime
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.models import VikunjaTask


class VikunjaError(RuntimeError):
    pass


class VikunjaTaskNotFound(VikunjaError):
    pass


class VikunjaClient:
    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()

        self.base_url = settings.vikunja_api_url.rstrip("/")
        self.default_project_id = settings.vikunja_default_project_id
        self.headers = {
            "Authorization": f"Bearer {settings.vikunja_api_token}",
            "Accept": "application/json",
        }

    def get_task(self, task_id: int) -> VikunjaTask:
        try:
            response = httpx.get(
                f"{self.base_url}/tasks/{task_id}",
                headers=self.headers,
                timeout=15.0,
            )
        except httpx.RequestError as exc:
            raise VikunjaError(
                f"Could not connect to Vikunja: {exc}"
            ) from exc

        if response.status_code == 404:
            raise VikunjaTaskNotFound(
                f"Vikunja task {task_id} was not found"
            )

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise VikunjaError(
                f"Vikunja returned HTTP {response.status_code}: "
                f"{response.text[:300]}"
            ) from exc

        return self._to_task(response.json())

    def list_tasks(self) -> list[VikunjaTask]:
        tasks: list[VikunjaTask] = []
        page = 1
        while True:
            try:
                response = httpx.get(
                    f"{self.base_url}/tasks",
                    headers=self.headers,
                    params={"page": page, "per_page": 100},
                    timeout=15.0,
                )
            except httpx.RequestError as exc:
                raise VikunjaError(
                    f"Could not connect to Vikunja: {exc}"
                ) from exc
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise VikunjaError(
                    f"Vikunja returned HTTP {response.status_code}: "
                    f"{response.text[:300]}"
                ) from exc
            payload = response.json()
            if not isinstance(payload, list):
                raise VikunjaError("Vikunja task list response was not a list")
            tasks.extend(self._to_task(item) for item in payload)
            if len(payload) < 100:
                break
            page += 1
        return tasks

    def create_task(
        self,
        title: str,
        due_date: datetime | None = None,
    ) -> VikunjaTask:
        if self.default_project_id is None:
            raise VikunjaError(
                "VIKUNJA_DEFAULT_PROJECT_ID is required to create tasks"
            )
        payload: dict[str, Any] = {"title": title}
        if due_date is not None:
            payload["due_date"] = due_date.isoformat()
        try:
            response = httpx.put(
                f"{self.base_url}/projects/{self.default_project_id}/tasks",
                headers=self.headers,
                json=payload,
                timeout=15.0,
            )
        except httpx.RequestError as exc:
            raise VikunjaError(f"Could not connect to Vikunja: {exc}") from exc
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise VikunjaError(
                f"Vikunja returned HTTP {response.status_code}: "
                f"{response.text[:300]}"
            ) from exc
        try:
            body = response.json()
            if not isinstance(body, dict):
                raise TypeError("task response was not an object")
            return self._to_task(body)
        except (ValueError, KeyError, TypeError) as exc:
            raise VikunjaError("Vikunja returned an invalid task") from exc

    def _to_task(self, task: dict[str, Any]) -> VikunjaTask:
        return VikunjaTask(
            id=task["id"],
            title=task["title"],
            description=task.get("description") or "",
            due_date=self._parse_datetime(task.get("due_date")),
            priority=task.get("priority") or 0,
            done=bool(task.get("done", False)),
            project_id=task.get("project_id"),
            labels=task.get("labels") or [],
        )

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not value or value == "0001-01-01T00:00:00Z":
            return None

        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )

        if parsed.year <= 1:
            return None

        return parsed
