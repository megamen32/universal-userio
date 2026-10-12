#!/usr/bin/env python3
"""Build the self-contained User.io documentation site from versioned Markdown."""
from __future__ import annotations
import argparse, html, json, re
from pathlib import Path
from markdown_it import MarkdownIt

ROOT=Path(__file__).resolve().parents[2]
ICON={
 'search':'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
 'menu':'<path d="M4 6h16M4 12h16M4 18h16"/>',
 'theme':'<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
 'arrow':'<path d="M5 12h14m-5-5 5 5-5 5"/>',
 'book':'<path d="M12 20c-2-2-5-2-9-2V4c4 0 7 0 9 2 2-2 5-2 9-2v14c-4 0-7 0-9 2Zm0 0V6"/>'}
def icon(name:str)->str:
 return f'<svg class="icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{ICON[name]}</svg>'
def main()->None:
 parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);args=parser.parse_args()
 cfg=json.loads((ROOT/'docs/guide/navigation.json').read_text())
 md=MarkdownIt('commonmark',{'html':True}).enable('table')
 slugs={p['slug'] for p in cfg['pages']};sections=[];nav=[];group=None;search=[]
 for i,p in enumerate(cfg['pages']):
  slug=p['slug'];raw=(ROOT/f'docs/guide/{slug}.md').read_text();body='\n'.join(raw.splitlines()[2:])
  body=re.sub(r'\]\(([-a-z0-9]+)\.md\)',lambda m:'](#'+m[1]+')' if m[1] in slugs else m[0],body)
  rendered=md.render(body)
  counter=[0]
  def heading(m):
   counter[0]+=1;return f'<h2 id="{slug}--section-{counter[0]}">{m[1]}</h2>'
  rendered=re.sub(r'<h2>(.*?)</h2>',heading,rendered,flags=re.S)
  rendered=re.sub(r'<pre><code(?: class="language-([^"]+)")?>(.*?)</code></pre>',lambda m:'<div class="code-wrap"><div class="code-head"><span>'+html.escape(m[1] or 'text')+'</span><button class="code-copy" type="button" aria-label="Скопировать пример">Копировать</button></div><pre><code>'+m[2]+'</code></pre></div>',rendered,flags=re.S)
  rendered=rendered.replace('<table>','<div class="table-wrap"><table>').replace('</table>','</table></div>')
  rendered=re.sub(r'<p>(<strong>User\.io полностью бесплатен.*?</p>)',r'<p class="free-callout">\1',rendered)
  home=''
  if slug=='overview':
   home='''<div class="flow"><div class="flow-head"><span><i class="flow-mark"></i>Ваши аккаунты → ваш помощник</span><span>MCP + API</span></div><div class="flow-body"><div class="provider-stack"><span>Telegram</span><span>WhatsApp</span><span>Gmail</span><span>Яндекс</span><span>MAX</span><span>VK · SMS</span></div><span class="flow-arrow">→</span><div class="flow-center">User.io</div><span class="flow-arrow second">→</span><div class="ai-stack">Ваш ИИ<small>ЧИТАТЬ · НАХОДИТЬ · ОТВЕЧАТЬ</small></div></div><p class="flow-caption">Разные каналы. Общая переписка для работы с ИИ.</p></div><div class="start-grid"><a class="start-card" href="#quickstart"><span class="num">01</span><strong>Создать аккаунт <span class="arr">↗</span></strong><p>Имя пользователя и пароль. Бесплатно.</p></a><a class="start-card" href="#channels"><span class="num">02</span><strong>Подключить чаты <span class="arr">↗</span></strong><p>Начните с почты или Telegram.</p></a><a class="start-card" href="#connect-ai"><span class="num">03</span><strong>Выбрать свой ИИ <span class="arr">↗</span></strong><p>Один адрес подключения через MCP.</p></a></div>'''
  prev=cfg['pages'][i-1] if i else None;nxt=cfg['pages'][i+1] if i+1<len(cfg['pages']) else None
  pager='<nav class="pager" aria-label="Соседние разделы">'
  if prev:pager+=f'<a href="#{prev["slug"]}"><span class="over">← НАЗАД</span><span class="name">{html.escape(prev["title"])}</span></a>'
  if nxt:pager+=f'<a href="#{nxt["slug"]}"><span class="over">ДАЛЬШЕ →</span><span class="name">{html.escape(nxt["title"])}</span></a>'
  pager+='</nav>'
  sections.append(f'<section class="page {"active" if not i else ""}" id="{slug}" aria-labelledby="title-{slug}"><div class="edition"><span class="tag">USER.IO / DOCS</span><span>{html.escape(cfg["edition"])}</span></div><h1 id="title-{slug}">{html.escape(p["title"])}</h1><p class="lede">{html.escape(p["summary"])}</p>{home}<div class="prose">{rendered}</div>{pager}</section>')
  if group!=p['group']:
   if group:nav.append('</div>')
   group=p['group'];nav.append(f'<div class="nav-group"><p class="nav-label">{html.escape(group)}</p>')
  color={'telegram':'tg','gmail':'gmail','yandex':'yandex','whatsapp':'wa','max':'max','vk':'vk'}.get(slug)
  dot=f'<span class="nav-dot nav-{color}" aria-hidden="true"></span>' if color else ''
  nav.append(f'<a class="nav-link" href="#{slug}" {"aria-current=page" if not i else ""}>{dot}{html.escape(p["title"])}</a>')
  plain=html.unescape(re.sub('<[^>]+>',' ',rendered));plain=re.sub(r'\s+',' ',plain).strip()
  search.append({**p,'text':plain,'markdown':raw})
 nav.append('</div>')
 css=(ROOT/'tools/userio_docs/site.css').read_text();js=(ROOT/'tools/userio_docs/site.js').read_text()
 data=json.dumps(search,ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
 result=f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="description" content="Документация User.io: бесплатная регистрация, подключение Telegram, Gmail, Яндекс.Почты, WhatsApp, MAX и VK, работа с ИИ через MCP, секретарь и API."><meta name="theme-color" content="#f6f7f2"><title>User.io Docs · Ваши чаты. Ваш ИИ.</title><script>document.documentElement.classList.add('js');</script><style>{css}</style></head>
<body><a href="#main" class="skip">Перейти к содержанию</a><header class="top"><button id="menu-toggle" class="btn icon-btn mobile-toggle" type="button" aria-label="Открыть меню" aria-expanded="false" aria-controls="sidebar">{icon('menu')}</button><a href="#overview" class="brand" aria-label="User.io: документация"><span class="mark">u/</span><span>User.io</span></a><span class="brand-divider"></span><span class="brand-sub">Документация</span><span class="top-fill"></span><a class="free" href="#account"><i></i>Бесплатно. Только логин и пароль.</a><a class="btn top-github" href="https://github.com/megamen32/universal-userio" target="_blank" rel="noopener noreferrer">GitHub ↗</a><a class="top-login" style="font-size:12px" href="https://msg.bezrabotnyi.com/login">Личный кабинет</a><button id="search-mobile" class="btn icon-btn mobile-toggle" type="button" aria-label="Поиск по документации">{icon('search')}</button><button id="theme-toggle" class="btn icon-btn" type="button" aria-label="Включить тёмную тему">{icon('theme')}</button><a class="btn btn-primary" href="https://msg.bezrabotnyi.com/signup">Начать бесплатно {icon('arrow')}</a></header>
<div id="backdrop" class="backdrop"></div><div class="layout"><aside class="sidebar" id="sidebar" aria-label="Разделы документации"><button id="search-trigger" class="search-trigger" type="button">{icon('search')} Поиск по тексту <kbd>⌘ K</kbd></button><a class="btn btn-primary mobile-signup" href="https://msg.bezrabotnyi.com/signup">Создать аккаунт бесплатно</a><nav>{''.join(nav)}</nav><a class="sidebar-foot" href="https://msg.bezrabotnyi.com/">← На сайт User.io</a></aside><div class="content-layout"><main class="main" id="main"><div class="breadcrumb"><span id="breadcrumb-group">Начало</span><span>/</span><span id="breadcrumb-title">Ваши чаты. Ваш ИИ.</span></div><button class="copy-page" type="button" id="copy-markdown">Копировать Markdown</button>{''.join(sections)}<footer class="doc-footer"><span>User.io · Экосистема «Безработный»</span><a href="#sources">Версии и источники ↗</a></footer></main><aside class="toc" aria-label="На этой странице"><p class="toc-title">На этой странице</p><nav id="toc-list"></nav><div class="toc-links"><a href="#troubleshooting">Нужна помощь? →</a><a href="https://github.com/megamen32/universal-userio/issues" target="_blank" rel="noopener noreferrer">Сообщить об ошибке ↗</a></div></aside></div></div>
<dialog id="search-dialog" aria-label="Поиск по документации"><div class="search-box">{icon('search')}<input id="search-input" type="search" placeholder="Например, Telegram или восстановление пароля" aria-label="Поисковый запрос" aria-controls="search-results" role="combobox" aria-expanded="true" autocomplete="off"><button id="close-search" type="button" aria-label="Закрыть поиск">ESC</button></div><div id="search-results" class="results" role="listbox" aria-label="Результаты поиска"></div><div class="search-help"><span>↑ ↓ выбрать · Enter открыть</span><span>{len(search)} раздел · поиск по всему тексту</span></div></dialog><div id="toast" class="toast" role="status" aria-live="polite"></div><script id="pages-data" type="application/json">{data}</script><script>{js}</script></body></html>'''
 outputs=[args.output] if args.output else [ROOT/'docs/index.html',ROOT/'src/universal_userio/static/docs.html']
 for output in outputs:
  output.parent.mkdir(parents=True,exist_ok=True);output.write_text(result,encoding='utf-8')
  print(f'Built {len(search)} pages → {output} ({len(result.encode()):,} bytes)')
if __name__=='__main__':main()
