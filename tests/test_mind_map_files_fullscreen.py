import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import pytest
from playwright.sync_api import sync_playwright

SCRIPT=Path(__file__).resolve().parents[1]/'app/skills/generic-chart/scripts/mind_map.py'
spec=importlib.util.spec_from_file_location('file_mind_map',SCRIPT)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def test_large_file_mapping_includes_every_record_and_preserves_source(tmp_path):
    rows=[{'category':'A' if i%2 else 'B','name':f'Item {i}','value':None if i==0 else i} for i in range(1500)]
    source=tmp_path/'events.json';source.write_text(json.dumps({'events':rows}));original=source.read_bytes()
    payload={'title':'All records','dataSource':{'path':'events.json','recordsPath':['events'],'groupBy':['category'],
             'labelFields':['name',{'field':'value','label':'Value'}]}}
    result=m.resolve_file_content(payload,tmp_path)
    assert result['_fileSource']['recordCount']==1500
    assert result['data']['content'].count('Item ')==1500
    assert 'Item 1499' in result['data']['content'] and 'Value: —' in result['data']['content']
    assert source.read_bytes()==original and 'dataSource' in payload
    assert len(result['_fileSource']['sha256'])==64
    payload['dataSource']['labelFields']=['missing']
    with pytest.raises(ValueError,match='field not found'):m.resolve_file_content(payload,tmp_path)


def test_tree_and_markdown_files_have_no_model_transcription_or_script_injection(tmp_path):
    source=tmp_path/'tree.json';source.write_text(json.dumps({'name':'Root','children':[{'name':'</script><b>Literal</b>\n# not a root'}]}))
    result=m.resolve_file_content({'dataSource':{'path':str(source),'treePath':[]}},tmp_path)
    assert result['_fileSource']['recordCount']==2 and result['data']['content'].count('\n')==1
    assert '&lt;/script&gt;' in result['data']['content']
    markdown=tmp_path/'map.md';markdown.write_text('# Real topic\n## Branch\n- </script>')
    output=tmp_path/'chart.html'
    run=subprocess.run([sys.executable,str(SCRIPT),'--content-file',str(markdown),'--output',str(output),'--theme','dark'],capture_output=True,text=True)
    assert run.returncode==0,run.stderr
    assert '<\\/script>' in output.read_text()
    assert json.loads(run.stdout)['fileSource']['kind']=='markdown-file'
    with pytest.raises(ValueError,match='not both'):m.resolve_file_content({'data':{'content':'inline'}},tmp_path,str(markdown))


def test_fullscreen_button_visible_in_embed_and_exit_restores_canvas():
    html=m.build_html({'data':{'content':'# Topic\n## Group\n- Value'},'theme':'dark'})
    # Stable SVG stands in for the external Markmap loader; exercise actual fullscreen and fit code.
    loader="""const p=document.getElementById('mm');p.innerHTML='<svg class="markmap" width="100%" height="100%" viewBox="0 0 900 500"><g><rect x="0" y="0" width="600" height="300"/></g></svg>';"""
    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        try:
            for width in (1100,390):
                page=browser.new_page(viewport={'width':width,'height':800})
                page.route('**/*markmap-autoloader*',lambda route:route.fulfill(body=loader,content_type='application/javascript'))
                page.set_content('<iframe allowfullscreen style="width:100%;height:520px;border:0"></iframe>')
                page.locator('iframe').evaluate('(frame,html)=>frame.srcdoc=html',html)
                child=page.frames[1];child.wait_for_selector('svg.markmap')
                button=child.locator('#fullscreenBtn')
                assert button.is_visible()
                before=child.locator('#container').bounding_box()['height']
                button.click()
                child.wait_for_function('document.fullscreenElement !== null')
                assert child.locator('#exitFullscreenBtn').is_visible() and child.locator('#mindMapZoom').is_visible()
                assert child.locator('body').evaluate('(b)=>getComputedStyle(b).backgroundColor')=='rgb(16, 16, 16)'
                child.locator('#exitFullscreenBtn').click()
                child.wait_for_function('document.fullscreenElement === null')
                assert button.is_visible()
                assert abs(child.locator('#container').bounding_box()['height']-before)<2
                page.close()
        finally:browser.close()
