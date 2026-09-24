from fastapi import Request

from backend.app import schemas as s
from backend.app.services import task_lifecycle as life


def install(app, settings, read, mutate, ok):
    @app.get("/api/v1/tasks/{id}/lifecycle")
    def status(id: str, request: Request):
        with read(request) as (db, owner, _):
            task = life.get_task(db, id, owner, settings.provider_mode)
            return ok(request, life.lifecycle_view(db, task))

    @app.get("/api/v1/tasks/{id}/delete-preview")
    def preview(id: str, request: Request):
        with read(request) as (db, owner, _):
            task = life.get_task(db, id, owner, settings.provider_mode)
            return ok(request, life.delete_preview(db, settings, task))

    def update(id, request, body, action):
        def run(db, owner):
            task = life.get_task(db, id, owner, settings.provider_mode)
            return life.change(db, settings, task, action, body), 200

        return mutate(request, body.model_dump(), run)

    @app.post("/api/v1/tasks/{id}/archive")
    def archive(id: str, request: Request, body: s.RevisionIn):
        return update(id, request, body, "archive")

    @app.post("/api/v1/tasks/{id}/restore")
    def restore(id: str, request: Request, body: s.RevisionIn):
        return update(id, request, body, "restore")

    @app.post("/api/v1/tasks/{id}/trash")
    def trash(id: str, request: Request, body: s.TaskTrashIn):
        return update(id, request, body, "trash")
