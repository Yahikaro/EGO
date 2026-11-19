"""API contract guard: the served OpenAPI must match docs/contracts/m1-openapi.json.

A failure means the HTTP contract changed. If the change is intentional and
backward compatible, run scripts/export_openapi.py and commit the new snapshot
together with the M1_API.md update. Removing routes/fields breaks clients.
"""
import json
from pathlib import Path

from scripts.export_openapi import SNAPSHOT, contract


def _operations(schema):
    return {(path, method) for path, item in schema['paths'].items() for method in item}


def test_served_api_matches_committed_contract_snapshot():
    committed = json.loads(SNAPSHOT.read_text(encoding='utf-8'))
    current = json.loads(json.dumps(contract(), sort_keys=True))
    removed = _operations(committed) - _operations(current)
    added = _operations(current) - _operations(committed)
    assert not removed, f'routes removed (breaking): {sorted(removed)}'
    assert not added, f'routes added without updating the snapshot: {sorted(added)}'
    assert current == committed, 'request/response schemas changed; re-export after review'


def test_m1_routes_and_client_fields_stay_available():
    schema = contract()
    for operation in [('/health', 'get'), ('/v1/system/status', 'get'), ('/v1/chats', 'get'), ('/v1/chats', 'post'),
                      ('/v1/chats/{chat_id}/messages', 'get'), ('/v1/generations', 'post'),
                      ('/v1/generations/{generation_id}', 'get'), ('/v1/generations/{generation_id}/events', 'get'),
                      ('/v1/generations/{generation_id}/stop', 'post'), ('/v1/operations/{operation_id}', 'get'),
                      ('/v1/events', 'get'), ('/v1/system/shutdown', 'post')]:
        assert operation in _operations(schema), operation
    fields = schema['components']['schemas']['GenerationCommand']['properties']
    assert {'operation_id', 'chat_id', 'content', 'expected_version', 'character_name', 'character_description'} <= set(fields)
    paging = {p['name'] for p in schema['paths']['/v1/chats/{chat_id}/messages']['get']['parameters']}
    assert {'before', 'limit'} <= paging
