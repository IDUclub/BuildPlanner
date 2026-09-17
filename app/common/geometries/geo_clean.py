"""Чистка GeoJSON-полигонов перед записью в Urban API, без shapely.

GenPlanner изредка отдаёт кольца с почти-дублями соседних вершин: координаты
отличаются на ~1e-10 градуса (доли микрона на местности) — это сегменты нулевой
длины и микро-спайки. Мягкий валидатор `testing.idulab.ru` их глотал, строгий
GEOS дев-контура бракует («Передана некорректная геометрия»). `shapely.make_valid`
решил бы это одной строкой, но тянуть shapely против ADR-0001 (D3) нельзя —
поэтому схлопываем соседние вершины вручную.
"""

from typing import Any, Sequence

# ~1e-7 градуса ≈ 1 см на местности: заведомо крупнее почти-дублей (1e-10..1e-11)
# и заведомо мельче значимого расстояния между вершинами кварталов и зданий.
DEDUP_EPS_DEG = 1e-7


def clean_geometry(geometry: Any) -> Any | None:
    """Убирает почти-дубли вершин у Polygon/MultiPolygon.

    Возвращает очищенную геометрию (новый dict, вход не мутируется) либо `None`,
    если после чистки не осталось валидного внешнего кольца — такую фигуру писать
    нельзя. Не-полигоны и мусор отдаёт как есть: их валидирует уже Urban API.
    """
    if not isinstance(geometry, dict):
        return geometry
    geometry_type = geometry.get("type")

    if geometry_type == "Polygon":
        rings = _clean_polygon(geometry.get("coordinates"))
        return None if rings is None else {**geometry, "coordinates": rings}

    if geometry_type == "MultiPolygon":
        polygons = geometry.get("coordinates")
        if not isinstance(polygons, Sequence) or isinstance(polygons, str):
            return geometry
        kept = [cleaned for cleaned in (_clean_polygon(p) for p in polygons) if cleaned is not None]
        return None if not kept else {**geometry, "coordinates": kept}

    return geometry


def _clean_polygon(rings: Any) -> list | None:
    """Внешнее кольцо обязательно; вырожденные дырки просто выкидываем."""
    if not isinstance(rings, Sequence) or isinstance(rings, str) or not rings:
        return None
    outer = _dedup_ring(rings[0])
    if outer is None:
        return None
    holes = [ring for ring in (_dedup_ring(r) for r in rings[1:]) if ring is not None]
    return [outer, *holes]


def _dedup_ring(ring: Any) -> list | None:
    """Схлопывает соседние почти-дубли и замыкает кольцо.

    Кольцу нужно минимум 3 различные вершины плюс замыкающая, равная первой.
    Если после чистки различных вершин меньше трёх — кольцо вырождено, возвращаем
    `None`. Полная запись вершины (включая возможный z) сохраняется как есть —
    сравниваем только по lon/lat.
    """
    if not isinstance(ring, Sequence) or isinstance(ring, str):
        return None
    kept: list = []
    for point in ring:
        if not _is_point(point):
            continue
        if not kept or not _close(kept[-1], point):
            kept.append(list(point))
    # Замыкающая вершина совпадает с первой — при дедупе она осталась хвостом; снимаем её,
    # чтобы не считать за отдельную, и заодно ловим случай слегка «уехавшего» замыкания.
    while len(kept) >= 2 and _close(kept[0], kept[-1]):
        kept.pop()
    if len(kept) < 3:
        return None
    kept.append(list(kept[0]))
    return kept


def _is_point(point: Any) -> bool:
    if not isinstance(point, Sequence) or isinstance(point, str) or len(point) < 2:
        return False
    lon, lat = point[0], point[1]
    if isinstance(lon, bool) or isinstance(lat, bool):
        return False
    return isinstance(lon, (int, float)) and isinstance(lat, (int, float))


def _close(first: Sequence, second: Sequence) -> bool:
    return abs(first[0] - second[0]) <= DEDUP_EPS_DEG and abs(first[1] - second[1]) <= DEDUP_EPS_DEG
