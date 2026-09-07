import sys
from pathlib import Path

from loguru import logger


def init_logger(log_level: str, log_file: str) -> Path:
    """Настраивает loguru и возвращает путь к файлу лога (его отдаёт ``system/logs_router``)."""
    log_path = Path(log_file).resolve()
    logger.remove()
    logger.add(sys.stdout, level=log_level, enqueue=True)
    logger.add(
        log_path,
        level=log_level,
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
        enqueue=True,
    )
    return log_path
