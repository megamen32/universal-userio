#!/usr/bin/env python3
"""Check documentation structure, local links, examples and reproducibility."""
from __future__ import annotations
import json, re, subprocess, sys, tempfile
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

class Document(HTMLParser):
    def __init__(self):
        super().__init__(); self.ids=[]; self.anchors=[]; self.remote_assets=[]
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if 'id' in attrs: self.ids.append(attrs['id'])
        if tag=='a' and attrs.get('href','').startswith('#'):
            self.anchors.append(attrs['href'][1:])
        if tag=='script' and attrs.get('src'): self.remote_assets.append(attrs['src'])
        if tag=='link' and attrs.get('rel')=='stylesheet': self.remote_assets.append(attrs.get('href'))

def check()->None:
    config=json.loads((ROOT/'docs/guide/navigation.json').read_text())
    slugs=[p['slug'] for p in config['pages']]
    assert len(slugs)==len(set(slugs)), 'Duplicate page slug'
    assert all(re.fullmatch(r'[a-z0-9-]+',s) for s in slugs), 'Unsafe slug'
    json_examples=0
    for path in [ROOT/'README.md',*sorted((ROOT/'docs').glob('*.md'))]:
        for href in re.findall(r'\]\(([^)]+)\)',path.read_text()):
            if href.startswith(('https://','http://','#','mailto:')):continue
            target=(path.parent/href.split('#')[0]).resolve()
            assert target.is_relative_to(ROOT) and target.exists(), f'Broken reference link: {href} in {path}'
            assert not {'.tmp','.venv','node_modules','VIP'} & set(target.relative_to(ROOT).parts), f'Unpublished dependency link: {href}'
    for page in config['pages']:
        path=ROOT/f'docs/guide/{page["slug"]}.md';body=path.read_text()
        assert len(re.findall(r'^# ',body,re.M))==1, f'Expected one title: {path}'
        for href in re.findall(r'\]\(([^)]+)\)',body):
            if href.startswith(('https://','http://','#')):continue
            target=(path.parent/href.split('#')[0]).resolve()
            assert target.is_relative_to(ROOT), f'Path outside package: {href}'
            assert target.exists(), f'Broken Markdown link: {href} in {path}'
        for code in re.findall(r'```json\s*\n(.*?)\n```',body,re.S):
            value=json.loads(code);assert isinstance(value,dict);json_examples+=1
        assert all(key in config['sources'] for key in page['sources'])
    assert (ROOT/'docs/index.html').read_bytes()==(ROOT/'src/universal_userio/static/docs.html').read_bytes(), 'Runtime docs stale'
    site=(ROOT/'docs/index.html').read_text()
    parsed=Document();parsed.feed(site)
    assert len(parsed.ids)==len(set(parsed.ids)), 'Duplicate HTML ids'
    assert set(parsed.anchors).issubset(set(parsed.ids)), 'Broken site anchor'
    assert not parsed.remote_assets, 'External runtime dependency'
    data=json.loads(re.search(r'<script id="pages-data" type="application/json">(.*?)</script>',site,re.S)[1])
    assert {p['slug'] for p in data}==set(slugs)
    assert all(p['text'] and p['markdown'] for p in data)
    temp_root=ROOT/'.tmp/docs-check';temp_root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='userio-docs-check-',dir=temp_root) as temp:
        output=Path(temp)/'index.html'
        subprocess.run([sys.executable,str(ROOT/'tools/userio_docs/build.py'),'--output',str(output)],check=True,capture_output=True)
        assert output.read_bytes()==(ROOT/'docs/index.html').read_bytes(), 'Rebuild docs/index.html before committing'
    print(json.dumps({'pages':len(slugs),'json_examples':json_examples,
          'html_ids':len(parsed.ids),'internal_site_links':len(parsed.anchors),
          'external_runtime_assets':len(parsed.remote_assets),'reproducible':True},ensure_ascii=False,indent=2))

if __name__=='__main__':check()
