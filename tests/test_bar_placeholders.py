import importlib.util
from pathlib import Path
import copy
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'app/skills/generic-chart/scripts/grouped_bar.py'
spec = importlib.util.spec_from_file_location('placeholder_bar', SCRIPT)
chart = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chart)


def payload(reference=2.84):
    return {'title': 'Actual vs estimate', 'xField': 'period',
            'series': [{'field': 'actual', 'label': 'Actual'}, {'field': 'estimate', 'label': 'Estimate'}],
            'data': [{'period': 'Q1', 'actual': 2.5, 'estimate': 2.4}, {'period': 'Q2', 'actual': None, 'estimate': reference}],
            'barPlaceholders': [{'series': 'Actual', 'x': 'Q2', 'referenceSeries': 'Estimate', 'label': 'Not reported'}]}


@pytest.mark.parametrize('value', [2.84, -2.84, 0])
def test_placeholder_preserves_source_and_resolves_only_same_row(value):
    source = payload(value)
    original = copy.deepcopy(source)
    config = chart.normalize_xy_series(source, 'grouped-bar', 'Test', 'bar')
    assert source == original
    assert config['data'][1]['actual'] is None
    assert config['barPlaceholders'][0]['index'] == 1
    assert config['barPlaceholders'][0]['referenceSeries'] == 'estimate'
    html = chart.render_svg_chart(config, 'bar')
    assert 'bar-placeholder' in html and 'patternUnits' in html


@pytest.mark.parametrize('kind', ['present', 'missing-ref', 'infinite', 'ambiguous', 'duplicate', 'bad-index', 'same-series'])
def test_invalid_placeholder_cannot_fabricate_or_overwrite_values(kind):
    p = payload()
    if kind == 'present': p['data'][1]['actual'] = 0
    if kind == 'missing-ref': p['data'][1]['estimate'] = None
    if kind == 'infinite': p['data'][1]['estimate'] = float('inf')
    if kind == 'ambiguous': p['data'].append(dict(p['data'][1]))
    if kind == 'duplicate': p['barPlaceholders'] *= 2
    if kind == 'bad-index': p['barPlaceholders'][0] = {'series': 'actual', 'index': True, 'referenceSeries': 'estimate'}
    if kind == 'same-series': p['barPlaceholders'][0]['referenceSeries'] = 'actual'
    with pytest.raises(ValueError): chart.normalize_xy_series(p, 'grouped-bar', 'Test', 'bar')


def test_hatch_geometry_tooltip_and_theme_in_browser():
    from playwright.sync_api import sync_playwright
    p = payload()
    p['data'] += [{'period': 'Q3', 'actual': None, 'estimate': -1.5}, {'period': 'Q4', 'actual': None, 'estimate': None}]
    p['barPlaceholders'].append({'series': 'actual', 'x': 'Q3', 'referenceSeries': 'estimate', 'label': 'Not reported'})
    p['data'][0]['__markColors'] = {'actual': '#ff0000'}
    config = chart.normalize_xy_series(p, 'grouped-bar', 'Test', 'bar')
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for theme in ('light', 'dark'):
                config['themeMode'] = theme
                page = browser.new_page(viewport={'width': 1000, 'height': 600})
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.set_content(chart.render_svg_chart(config, 'grouped-bar'))
                for width in (1000, 390):
                    page.set_viewport_size({'width': width, 'height': 600})
                    page.wait_for_timeout(80)
                    assert page.locator('.bar-placeholder').count() == 2
                    for row in (1, 2):
                        actual = page.locator(f'.bar[data-row-index="{row}"][data-series="actual"]')
                        estimate = page.locator(f'.bar[data-row-index="{row}"][data-series="estimate"]')
                        assert actual.get_attribute('height') == estimate.get_attribute('height')
                        assert actual.get_attribute('y') == estimate.get_attribute('y')
                        assert actual.get_attribute('fill').startswith('url(#bar-hatch-')
                        assert 'Not reported' in actual.get_attribute('aria-label')
                    assert page.locator('.bar[data-row-index="0"][data-series="actual"]').get_attribute('fill') == '#ff0000'
                    assert page.locator('.bar[data-row-index="3"]').count() == 0
                    page.locator('.hover-zone').nth(1).hover()
                    assert 'Not reported' in page.locator('.grouped-bar-tooltip-portal.active').inner_text()
                    assert page.evaluate('config.data[1].actual') is None
                    assert 'Not reported' in page.locator('.chart-legend').inner_text()
                    assert not errors
                page.close()
        finally:
            browser.close()
