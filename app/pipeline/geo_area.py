"""Площадь GeoJSON-полигонов в квадратных метрах, без geopandas и shapely.

Оркестратор намеренно лёгкий: он не считает геометрию, он её перекладывает.
Единственное исключение — площадь блоков, от неё зависят цели застройки
(`residents`, `coverage_area`), а без них GenBuilder не запускает генерацию.
Тянуть ради одной формулы geopandas со всем стеком GDAL несоразмерно.

Считаем по сферической формуле (spherical excess) на средней радиусе Земли.
Для кварталов и районов ошибка относительно эллипсоида — доли процента,
что заведомо точнее самих нормативов плотности, на которые площадь умножается.
"""

from math import radians, sin
from typing import Any, Iterable, Sequence

EARTH_RADIUS_M = 6_371_008.8

SQUARE_METERS_IN_HECTARE = 10_000.0


def feature_collection_area_by_key(
    features: Iterable[dict[str, Any]],
    key: str,
) -> dict[str, float]:
    """Суммарная площадь фич (м²), сгруппированная по значению `properties[key]`."""
    areas: dict[str, float] = {}
    for feature in features:
        group = (feature.get("properties") or {}).get(key)
        if not isinstance(group, str):
            continue
        areas[group] = areas.get(group, 0.0) + geometry_area_m2(feature.get("geometry"))
    return areas


def geometry_area_m2(geometry: Any) -> float:
    """Площадь Polygon или MultiPolygon. Всё остальное (и `None`) — ноль."""
    if not isinstance(geometry, dict):
        return 0.0

    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")

    if geometry_type == "Polygon":
        return _polygon_area_m2(coordinates)
    if geometry_type == "MultiPolygon":
        if not isinstance(coordinates, Sequence):
            return 0.0
        return sum(_polygon_area_m2(polygon) for polygon in coordinates)
    return 0.0


def _polygon_area_m2(rings: Any) -> float:
    """Внешнее кольцо минус дырки. Отрицательный результат невозможен — обрезаем нулём."""
    if not isinstance(rings, Sequence) or not rings:
        return 0.0
    outer = _ring_area_m2(rings[0])
    holes = sum(_ring_area_m2(ring) for ring in rings[1:])
    return max(outer - holes, 0.0)


def _ring_area_m2(ring: Any) -> float:
    """Сферическая площадь замкнутого кольца; направление обхода не важно."""
    points = _valid_points(ring)
    if len(points) < 3:
        return 0.0
    if points[0] != points[-1]:
        points.append(points[0])

    total = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(points, points[1:]):
        total += radians(lon2 - lon1) * (2.0 + sin(radians(lat1)) + sin(radians(lat2)))
    return abs(total) * EARTH_RADIUS_M * EARTH_RADIUS_M / 2.0


def _valid_points(ring: Any) -> list[tuple[float, float]]:
    """Отбрасывает всё, что не похоже на пару координат: чужой GeoJSON бывает грязным."""
    if not isinstance(ring, Sequence):
        return []
    points: list[tuple[float, float]] = []
    for point in ring:
        if not isinstance(point, Sequence) or isinstance(point, str) or len(point) < 2:
            continue
        lon, lat = point[0], point[1]
        if isinstance(lon, bool) or isinstance(lat, bool):
            continue
        if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
            continue
        points.append((float(lon), float(lat)))
    return points
