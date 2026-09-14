from app.integrations.places import PlaceCandidate
from app.modules.prospection.schemas import ProspectionRunCreate
from app.modules.prospection.service import ProspectionService


def test_circle_contract_requires_radius():
    run = ProspectionRunCreate(
        giro="dentistas",
        location_label="Roma Norte",
        center_latitude=19.4155,
        center_longitude=-99.1635,
        radius_km=5,
    )
    assert run.radius_km == 5


def test_polygon_contract_is_normalized_and_filtered():
    service = object.__new__(ProspectionService)
    geometry = {"type": "Polygon", "coordinates": [[[-1, -1], [1, -1], [1, 1], [-1, 1], [-1, -1]]]}
    run = ProspectionRunCreate(
        giro="restaurantes",
        location_label="Centro",
        center_latitude=0,
        center_longitude=0,
        polygon_geometry=geometry,
    )
    wkt = service._geometry_to_wkt(run.polygon_geometry)  # type: ignore[arg-type]
    inside = PlaceCandidate(place_id="inside", name="Dentro", latitude=0, longitude=0)
    outside = PlaceCandidate(place_id="outside", name="Fuera", latitude=2, longitude=0)
    assert wkt.startswith("POLYGON((")
    assert service._is_point_in_polygon(inside, wkt)
    assert not service._is_point_in_polygon(outside, wkt)
