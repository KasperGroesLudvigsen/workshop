from __future__ import annotations

import pytest

from screener.config import BoligaSettings
from screener.fetch.boliga import (
    BoligaBlockedError,
    BoligaClient,
    BoligaTruncatedResultsError,
    TransportResponse,
    discover_property_types,
)


def _settings() -> BoligaSettings:
    return BoligaSettings(
        base_url="https://api.boliga.dk/api/v2/search/results",
        requests_per_second=1000.0,  # keep tests fast
        max_results_per_query=300,
        user_agent="test-agent",
    )


def make_transport(pages_by_zip: dict[tuple[int, int], list[list[dict]]], total_by_zip: dict[tuple[int, int], int]):
    def transport(url, params, headers):
        zip_from, zip_to = params["zipcodeFrom"], params["zipcodeTo"]
        page = params["page"]
        pages = pages_by_zip[(zip_from, zip_to)]
        body = {
            "meta": {"totalCount": total_by_zip[(zip_from, zip_to)]},
            "results": pages[page - 1] if page - 1 < len(pages) else [],
        }
        return TransportResponse(status_code=200, text="{}", json_body=body)

    return transport


def test_fetch_shard_pages_and_matches_total():
    listings_p1 = [{"id": 1}, {"id": 2}]
    listings_p2 = [{"id": 3}]
    transport = make_transport(
        pages_by_zip={(4200, 4200): [listings_p1, listings_p2]},
        total_by_zip={(4200, 4200): 3},
    )
    client = BoligaClient(_settings(), transport=transport)
    result = list(client.fetch_shard(zip_from=4200, zip_to=4200, property_type=4, page_size=2))
    assert [r["id"] for r in result] == [1, 2, 3]


def test_fetch_shard_raises_on_truncation():
    listings_p1 = [{"id": 1}, {"id": 2}]
    transport = make_transport(
        pages_by_zip={(4200, 4200): [listings_p1]},  # only one page even though total says 3
        total_by_zip={(4200, 4200): 3},
    )
    client = BoligaClient(_settings(), transport=transport)
    with pytest.raises(BoligaTruncatedResultsError):
        list(client.fetch_shard(zip_from=4200, zip_to=4200, property_type=4, page_size=2))


def test_fetch_shard_bisects_when_over_cap():
    total_by_zip = {
        (4200, 4210): 500,  # over cap of 300 -> must bisect
        (4200, 4205): 2,
        (4206, 4210): 1,
    }
    pages_by_zip = {
        (4200, 4205): [[{"id": 1}, {"id": 2}]],
        (4206, 4210): [[{"id": 3}]],
    }

    def transport(url, params, headers):
        zip_from, zip_to = params["zipcodeFrom"], params["zipcodeTo"]
        page = params["page"]
        total = total_by_zip[(zip_from, zip_to)]
        pages = pages_by_zip.get((zip_from, zip_to), [])
        results = pages[page - 1] if page - 1 < len(pages) else []
        return TransportResponse(status_code=200, text="{}", json_body={"meta": {"totalCount": total}, "results": results})

    settings = _settings()
    client = BoligaClient(settings, transport=transport)
    result = list(client.fetch_shard(zip_from=4200, zip_to=4210, property_type=4, page_size=10))
    assert sorted(r["id"] for r in result) == [1, 2, 3]


def test_single_zip_over_cap_raises_without_bisecting_further():
    transport = make_transport(
        pages_by_zip={(4200, 4200): [[{"id": i} for i in range(400)]]},
        total_by_zip={(4200, 4200): 400,},
    )
    client = BoligaClient(_settings(), transport=transport)
    with pytest.raises(BoligaTruncatedResultsError):
        list(client.fetch_shard(zip_from=4200, zip_to=4200, property_type=4, page_size=400))


def test_403_raises_loudly_not_empty_list():
    def transport(url, params, headers):
        return TransportResponse(status_code=403, text="blocked", json_body=None)

    client = BoligaClient(_settings(), transport=transport)
    with pytest.raises(BoligaBlockedError):
        list(client.fetch_shard(zip_from=4200, zip_to=4200, property_type=4))


def test_discover_property_types_reads_labels_off_response():
    def transport(url, params, headers):
        body = {
            "meta": {"totalCount": 3},
            "results": [
                {"id": 1, "propertyType": 4, "propertyTypeName": "Fritidshus"},
                {"id": 2, "propertyType": 1, "propertyTypeName": "Villa"},
                {"id": 3, "propertyType": 4, "propertyTypeName": "Fritidshus"},
            ],
        }
        return TransportResponse(status_code=200, text="{}", json_body=body)

    client = BoligaClient(_settings(), transport=transport)
    codes = discover_property_types(client, sample_zip=4200)
    assert codes == {4: {"Fritidshus"}, 1: {"Villa"}}
