MAX_TITLE_LENGTH = 60


def normalize_title(raw: str | None, fallback_query: str, scenario_id: int) -> str:
    """Заголовок придумывает модель тем же вызовом; здесь — только подстраховка."""
    candidate = (raw or "").strip().strip('"«»')
    if not candidate:
        candidate = " ".join(fallback_query.split()[:5]).strip()
    if not candidate:
        candidate = f"Градплан сценария {scenario_id}"
    if len(candidate) > MAX_TITLE_LENGTH:
        candidate = candidate[: MAX_TITLE_LENGTH - 1].rstrip() + "…"
    return candidate
