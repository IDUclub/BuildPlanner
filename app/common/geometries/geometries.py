from typing import Any, Literal

from pydantic import BaseModel, Field

# Оркестратор не считает геометрию: он только пробрасывает GeoJSON и переписывает `properties`.
# Поэтому здесь минимальные модели без geopandas/shapely — это сознательно, см. ADR-0001 (D3).


class Geometry(BaseModel):
    type: str
    coordinates: Any = None
    geometries: list["Geometry"] | None = None


class Feature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: Geometry | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class FeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[Feature] = Field(default_factory=list)

    def __len__(self) -> int:
        return len(self.features)
