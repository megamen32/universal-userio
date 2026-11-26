const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const assert = require('node:assert/strict');

(async () => {
  const server = spawn(process.env.PYTHON || 'python3', ['tests/e2e/fixture_server.py'], {env: {...process.env, PYTHONPATH:'src'}, stdio:['ignore','pipe','inherit']});
  const fixture = JSON.parse(await new Promise(resolve => readline.createInterface({input:server.stdout}).once('line', resolve)));
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    const old = `#/chat/${fixture.chat}?m=${fixture.message}`;
    await page.goto(`http://127.0.0.1:${fixture.port}/?utm_source=ci${old}`);
    await page.waitForURL(/\/login/);
    await page.locator('[name=username]').fill('fixture-user');
    await page.locator('[name=password]').fill('fixture-password');
    await page.locator('form[action="/auth/session"] button').click();
    await page.waitForURL(/\/app\/chat\//);
    assert.equal(new URL(page.url()).searchParams.get('m'), fixture.message);
    await page.locator(`[data-read-message="${fixture.message}"]`).waitFor({state:'visible'});
    assert.match(await page.locator(`[data-read-message="${fixture.message}"]`).innerText(), /DEEPLINK FIXTURE MESSAGE/);
    const direct = `http://127.0.0.1:${fixture.port}/app/chat/${fixture.chat}?m=${fixture.message}`;
    await page.goto(direct);
    await page.locator(`[data-read-message="${fixture.message}"]`).waitFor({state:'visible'});
    assert.equal(page.url(), direct);
    const fresh = await browser.newPage();
    await fresh.goto(direct);
    await fresh.waitForURL(/\/login/);
    await fresh.locator('[name=username]').fill('fixture-user');
    await fresh.locator('[name=password]').fill('fixture-password');
    await fresh.locator('form[action="/auth/session"] button').click();
    await fresh.waitForURL(direct);
    await fresh.locator(`[data-read-message="${fixture.message}"]`).waitFor({state:'visible'});
    const signupContext = await browser.newContext();
    const signup = await signupContext.newPage();
    await signup.goto(direct);
    await signup.locator('a[href^="/signup?"]').first().click();
    await signup.locator('[name=username]').fill('new-fixture-user');
    await signup.locator('[name=password]').fill('fixture-password');
    await signup.locator('[name=password_confirm]').fill('fixture-password');
    await signup.locator('form[action="/auth/signup"] button').click();
    await signup.waitForURL(direct);
    await signupContext.close();
    for (const bad of ['//evil.example/path', 'https://evil.example/', '/\\evil.example', '/\r\nLocation: https://evil.example']) {
      const context = await browser.newContext();
      const probe = await context.newPage();
      await probe.goto(`http://127.0.0.1:${fixture.port}/login?next=${encodeURIComponent(bad)}`);
      await probe.locator('[name=username]').fill('fixture-user');
      await probe.locator('[name=password]').fill('fixture-password');
      await probe.locator('form[action="/auth/session"] button').click();
      await probe.waitForURL(`http://127.0.0.1:${fixture.port}/`);
      await context.close();
    }
    console.log('PASS legacy link, direct and authenticated links, rejected external next');
  } finally { await browser.close(); server.kill('SIGTERM'); }
})().catch(error => { console.error(error); process.exitCode = 1 });
