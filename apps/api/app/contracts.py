from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pyproj import Geod


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


Identifier = Annotated[str, Field(min_length=1, max_length=80)]
Text = Annotated[str, Field(max_length=250)]
Unit = Annotated[float, Field(ge=0, le=1)]


class Area(Contract):
    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-80, le=80)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-80, le=80)

    @model_validator(mode="after")
    def bounded(self):
        if not (self.west < self.east and self.south < self.north):
            raise ValueError("Use an ordered bounding box without crossing the antimeridian")
        if Geod(ellps="WGS84").inv(self.west, self.south, self.east, self.north)[2] > 20_000:
            raise ValueError("Choose a smaller neighbourhood: at most 20 km across the diagonal")
        return self

    def contains(self, lon, lat):
        return self.west <= lon <= self.east and self.south <= lat <= self.north


class Landmark(Contract):
    id: Identifier
    label: str = Field(max_length=120)
    type: str = Field(max_length=60)
    x: Unit
    y: Unit
    evidence: Text


class Relation(Contract):
    from_id: Identifier
    to_id: Identifier
    kind: Literal["connected_to", "near", "north_of", "south_of", "east_of", "west_of", "unknown"]
    evidence: Text


class SketchGraph(Contract):
    north_direction: Literal["up", "right", "down", "left", "unknown"]
    landmarks: list[Landmark] = Field(max_length=12)
    relations: list[Relation] = Field(max_length=20)
    unresolved: list[Text] = Field(max_length=12)

    @model_validator(mode="after")
    def references(self):
        ids = [p.id for p in self.landmarks]
        if len(ids) != len(set(ids)):
            raise ValueError("Landmark IDs must be unique")
        for relation in self.relations:
            if relation.from_id not in ids or relation.to_id not in ids or relation.from_id == relation.to_id:
                raise ValueError("Relations need two distinct known landmarks")
            if self.north_direction == "unknown" and relation.kind.endswith("_of"):
                raise ValueError("Compass relationships need explicit orientation")
        return self


class Candidate(Contract):
    id: Identifier
    label: str = Field(min_length=1, max_length=300)
    aliases: list[str] = Field(max_length=30)
    type: str = Field(max_length=120)
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    source_url: str
    position_kind: Literal["node", "bounding_box_center", "synthetic"]


class Proposal(Contract):
    sketch_id: Identifier
    candidate_id: Identifier | None
    evidence: Text


class Proposals(Contract):
    matches: list[Proposal] = Field(max_length=12)


class Decision(Contract):
    sketch_id: Identifier
    candidate_id: Identifier | None
    note: Text


class Confirmations(Contract):
    revision: int = Field(ge=0)
    decisions: list[Decision] = Field(min_length=1, max_length=12)


class GraphEdit(Contract):
    revision: int = Field(ge=0)
    graph: SketchGraph
    note: Text
