"""Check the live documentation on ONE explicitly owned existing CDP target.

No browser launch, new page, authentication, provider action or browser close.
Caller must hold its normal resource lease and stop at that lease's deadline.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cdp-url', required=True)
    parser.add_argument('--target-id', required=True)
    parser.add_argument('--output-dir', type=Path, default=Path('.tmp/docs-browser'))
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright
    args.output_dir.mkdir(parents=True, exist_ok=True)
    checks, errors = [], []
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(args.cdp_url, timeout=10000)
        page = None
        for context in browser.contexts:
            for candidate in context.pages:
                session = context.new_cdp_session(candidate)
                info = session.send('Target.getTargetInfo')['targetInfo']
                session.detach()
                if info['targetId'] == args.target_id:
                    page = candidate
                    break
            if page:
                break
        if page is None:
            raise RuntimeError('Owned target absent; refusing a replacement page')
        if urlsplit(page.url).path not in {'/docs', '/docs/', '/docs/index.html'}:
            raise RuntimeError('Owned target is not on the published documentation')
        original_viewport = page.viewport_size or page.evaluate("({width:innerWidth,height:innerHeight})")
        page.set_default_timeout(4000)
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.locator('.nav-link[href="#phone"]').click()
            assert page.locator('.page.active').get_attribute('id') == 'phone'
            page.screenshot(path=str(args.output_dir/'desktop.png'))
            page.locator('#search-trigger').click()
            page.locator('#search-input').fill('SMS')
            assert page.locator('.result').count() > 0
            page.keyboard.press('Escape')
            page.locator('#theme-toggle').click()
            assert page.locator('html').get_attribute('data-theme') == 'dark'
            checks += ['phone article', 'search', 'theme']
            page.set_viewport_size({'width':390,'height':844})
            page.locator('#menu-toggle').click()
            page.locator('.nav-link[href="#http-api"]').click()
            assert page.locator('.page.active').get_attribute('id') == 'http-api'
            assert not page.evaluate('document.documentElement.scrollWidth>window.innerWidth')
            page.screenshot(path=str(args.output_dir/'mobile.png'))
            checks += ['mobile menu', 'no horizontal overflow']
            assert not errors, errors
        finally:
            page.set_viewport_size(original_viewport)
        # Exit Playwright disconnects its transport; never close shared browser.
    (args.output_dir/'result.json').write_text(json.dumps(
        {'target_id':args.target_id,'checks':checks,'page_errors':errors},indent=2))
    print(json.dumps({'checks':checks,'page_errors':errors}))


if __name__ == '__main__':
    main()
