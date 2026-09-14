import io

import pytest


def test_parses_n8n_search_and_validation_headers():
    from app.modules.leads.importer import parse_leads_csv

    csv_data = """Place ID,Nombre,Telefono,Direccion,Web,Rating,Resenias,Giro,Zona,WebStatus,WebDetalle,GooglePresence,MetaAds,Perfil,Score,Contactado,Fecha,Mensaje frío,Mensaje generado,Mensaje frío error
ChIJ123,Clínica Roma,5512345678,"Roma Norte, CDMX",https://clinicaroma.mx,4.8,120,clínicas,Roma Norte,valida,,primeras_posiciones,true,oro,9,false,2026-09-10T12:00:00+00:00,"Hola Clínica Roma",2026-09-10T12:05:00+00:00,
"""

    rows = parse_leads_csv(io.BytesIO(csv_data.encode("utf-8")))

    assert len(rows) == 1
    row = rows[0]
    assert row.place_id == "ChIJ123"
    assert row.name == "Clínica Roma"
    assert row.phone == "5512345678"
    assert row.reviews_total == 120
    assert row.web_status == "valida"
    assert row.google_presence == "primeras_posiciones"
    assert row.meta_ads_active is True
    assert row.profile == "oro"
    assert row.score == 9
    assert row.contacted is False
    assert row.outreach_message == "Hola Clínica Roma"


def test_parses_search_only_export_and_creates_stable_fallback_key():
    from app.modules.leads.importer import parse_leads_csv

    first = parse_leads_csv(
        io.BytesIO(
            b"Nombre,Telefono,Direccion,Giro\nTaller Norte,8188880000,Monterrey, talleres\n"
        )
    )[0]
    second = parse_leads_csv(
        io.BytesIO(
            b"Nombre,Telefono,Direccion,Giro\nTaller Norte,8188880000,Monterrey, talleres\n"
        )
    )[0]

    assert first.place_id.startswith("csv:")
    assert first.place_id == second.place_id
    assert first.validation_status == "pending"


def test_rejects_rows_without_business_name_with_row_number():
    from app.modules.leads.importer import LeadImportError, parse_leads_csv

    with pytest.raises(LeadImportError, match="fila 2.*Nombre"):
        parse_leads_csv(io.BytesIO(b"Place ID,Nombre\nabc,\n"))


def test_maps_validation_fields_and_preserves_existing_values_when_csv_is_blank():
    from types import SimpleNamespace

    from app.modules.leads.importer import ImportedLeadRow, apply_imported_row

    business = SimpleNamespace(
        name="Nombre anterior",
        phone="5511111111",
        address="Dirección anterior",
        website="https://existente.mx",
        rating=4.0,
        reviews_total=10,
        latitude=None,
        longitude=None,
    )
    lead = SimpleNamespace(
        profile="unclassified",
        validation_status="pending",
        score=None,
        score_reason=None,
        giro=None,
        zona=None,
        web_status=None,
        web_check_detail=None,
        google_presence=None,
        meta_ads_active=None,
        contacted=False,
        source_searched_at=None,
        outreach_message=None,
        outreach_message_generated_at=None,
        outreach_message_error=None,
        raw_import_data=None,
    )
    row = ImportedLeadRow(
        place_id="ChIJ123",
        name="Clínica actualizada",
        phone=None,
        address=None,
        profile="oro",
        validation_status="complete",
        score=9,
        web_status="invalida",
        outreach_message="Mensaje listo",
        raw={"Nombre": "Clínica actualizada"},
    )

    apply_imported_row(business, lead, row)

    assert business.name == "Clínica actualizada"
    assert business.phone == "5511111111"
    assert business.website == "https://existente.mx"
    assert lead.profile == "oro"
    assert lead.validation_status == "complete"
    assert lead.score == 9
    assert lead.web_status == "invalida"
    assert lead.outreach_message == "Mensaje listo"
    assert lead.raw_import_data == {"Nombre": "Clínica actualizada"}
