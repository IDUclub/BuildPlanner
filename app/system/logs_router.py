from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from app.common.exceptions.http_exception import http_exception
from app.version import __version__

router = APIRouter(prefix="/buildplanner", tags=["system"])


@router.get("/health", summary="Проверка живости")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/logs/log_file", summary="Скачать текущий файл лога")
async def download_log(request: Request) -> FileResponse:
    log_path = request.app.state.log_path
    if not log_path.exists():
        raise http_exception(404, "Файл лога ещё не создан", _input=str(log_path))
    return FileResponse(log_path, filename=log_path.name, media_type="text/plain")
