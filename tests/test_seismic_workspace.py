from pathlib import Path
from html.parser import HTMLParser
import json

import pandas as pd
import pytest

from utils.seismic_workspace import prepare_events, build_workspace_payload, workspace_html


def catalog():
    return pd.DataFrame({
        'bujur': [107.0, 108.0], 'lintang': [-7.0, -8.0],
        'kedalaman': [20.0, 850.0], 'magnitudo': [4.5, None],
        'waktu': ['2026-01-01 12:00:00', None],
        'provinsi': ['Jawa Barat', '</script><script>alert(1)</script>'],
    })


def test_events_keep_metadata_and_stable_identity_without_mutating_catalog():
    frame = catalog()
    before = frame.copy(deep=True)
    result = prepare_events(frame)
    pd.testing.assert_frame_equal(frame, before)
    assert result[0]['id'] == prepare_events(frame.iloc[::-1])[1]['id']
    assert result[0]['time'] == '2026-01-01 12:00:00'
    assert result[1]['depth'] == 850
    assert result[1]['magnitude'] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('field,value', [
    ('bujur', 181), ('lintang', -91), ('kedalaman', -1),
    ('bujur', float('inf')), ('kedalaman', float('nan')),
])
def test_unplottable_events_are_excluded(field, value):
    frame = catalog()
    frame.loc[0, field] = value
    assert len(prepare_events(frame)) == 1


def test_minimal_and_empty_catalog():
    assert prepare_events(pd.DataFrame()) == []
    assert prepare_events(pd.DataFrame({'bujur': [107]})) == []
    result = prepare_events(pd.DataFrame({'bujur': [107], 'lintang': [-7], 'kedalaman': [20]}))
    assert len(result) == 1
    assert result[0]['time'] == 'Tidak tersedia'


def test_payload_keeps_geographic_reference_layers_and_empty_data():
    layers = {'features': [{'geometry': {'type': 'LineString', 'coordinates': [[106,-7],[108,-8]]}, 'properties': {'Name': 'Sunda'}}]}
    result = build_workspace_payload(catalog(), grids=[], trench_geojson=layers)
    assert result['layers']['trenches'][0]['path'][0] == [106, -7, 0]
    assert result['dropped'] == 0
    assert result['bbox'][0] < 107 < result['bbox'][2]
    assert build_workspace_payload(pd.DataFrame(), grids=[])['events'] == []


def test_html_is_offline_and_payload_cannot_escape_script():
    html = workspace_html(build_workspace_payload(catalog(), grids=[]))
    assert '</script><script>alert(1)</script>' not in html
    assert '\\u003c/script\\u003e' in html
    class ScriptSources(HTMLParser):
        sources = []
        def handle_starttag(self, tag, attrs):
            if tag == 'script' and 'src' in dict(attrs):
                self.sources.append(dict(attrs)['src'])
    parser = ScriptSources()
    parser.feed(html)
    assert not parser.sources
    assert 'id="view3d"' in html and 'id="section"' in html
    assert '__PAYLOAD__' not in html
