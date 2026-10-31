import importlib.util
from pathlib import Path
import sys
import pytest
from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parents[1]/'app/skills/generic-chart/scripts'
sys.path.insert(0,str(BASE))
import group_line
import line_chart


@pytest.mark.parametrize('curve',['step-after','step-before'])
def test_time_steps_preserve_irregular_dates_values_and_tooltips(curve):
    payload={'type':'line','xField':'date','yField':'rate','xScale':'time','missingValuePolicy':'gap',
             'series':[{'field':'rate','label':'Rate','axis':'right'}],
             'visualStyle':{'line':{'curve':curve,'showPoints':True}},
             'data':[{'date':'2022-01-01','rate':0.5},{'date':'2022-02-01','rate':5.5},{'date':'2023-01-01','rate':4.0}]}
    normalized=line_chart._normalize_line_payload(payload,None,{})
    assert normalized['data']==payload['data']
    assert normalized['xScale']=='time'
    with sync_playwright() as p:
        browser=p.chromium.launch()
        try:
            for width in (390,1120):
                page=browser.new_page(viewport={'width':width,'height':700});errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.set_content(group_line.render_document(normalized,None,{}))
                path=page.locator('.line-path').get_attribute('d')
                assert path.count('H')==2 and path.count('V')==2
                values=page.evaluate('''()=>{const zones=[...document.querySelectorAll('.hover-zone')];return zones.map(z=>({x:Number(z.getAttribute('x')),width:Number(z.getAttribute('width'))}));}''')
                assert values[0]['width'] < values[2]['width']/3
                assert not errors
                page.close()
        finally:browser.close()


def test_gap_policy_never_forward_fills_percent_observations():
    payload={'type':'group-line','xField':'date','series':[{'field':'rate','axis':'right'}], 'rightAxis':{'suffix':'%'},
             'missingValuePolicy':'gap','data':[{'date':'2022-01-01','rate':4},{'date':'2022-02-01','rate':None},{'date':'2023-01-01','rate':5}]}
    config=group_line._normalize_config(payload,None,{})
    assert config['data'][1]['rate'] is None
