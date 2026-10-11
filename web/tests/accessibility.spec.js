const {test, expect} = require('@playwright/test');
const AxeBuilder = require('@axe-core/playwright').default;
const payload = {focus: 'a', nodes: [
  {id: 'a', name: 'Alpha', path: 'src/a.py', symbol: 'Alpha', confidence: 'sound', reason: 'test reason', verify: 'pytest a'},
  {id: 'b', name: 'Beta', path: 'src/b.py', symbol: 'Beta', confidence: 'unknown', reason: 'dynamic call', verify: 'pytest b'}
], edges: [{from: 'a', to: 'b', relation: 'CALLS'}]};

for (const viewer of ['card', 'graph']) {
  test(`${viewer}: keyboard input, error recovery and accessibility`, async ({page}) => {
    await page.goto(`/${viewer}.html`);
    const input = page.getByLabel(viewer === 'card' ? 'Map JSON' : 'Graph JSON', {exact: true});
    await input.fill('bad JSON');
    await page.getByRole('button', {name: 'Render', exact: true}).focus();
    await page.keyboard.press('Enter');
    await expect(input).toHaveAttribute('aria-invalid', 'true');
    await expect(input).toBeFocused();
    await input.fill(JSON.stringify(payload));
    await page.getByRole('button', {name: 'Render', exact: true}).focus();
    await page.keyboard.press('Enter');
    await expect(input).not.toHaveAttribute('aria-invalid', 'true');
    if (viewer === 'graph') {
      const select = page.getByLabel('Inspect node', {exact: true});
      await select.focus();
      await page.keyboard.press('b');
      await page.keyboard.press('Enter');
      await expect(page.locator('#detail')).toContainText('Beta');
      await expect(page.locator('#detail')).toContainText('Alpha → CALLS → Beta');
      await page.getByRole('button', {name: 'Zoom in', exact: true}).press('Enter');
      await expect(page.locator('#g > g')).toHaveAttribute('transform', /scale\(1.3\)/);
    } else {
      const toggle = page.getByRole('button', {name: 'View JSON'});
      await toggle.focus();
      await page.keyboard.press('Space');
      await expect(toggle).toHaveAttribute('aria-expanded', 'true');
      await page.getByRole('button', {name: 'Answer b'}).focus();
      await page.keyboard.press('Enter');
      await expect(page.getByRole('status')).toContainText('pytest b');
    }
    expect((await new AxeBuilder({page}).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({path: test.info().outputPath(`${viewer}.png`), fullPage: true});
  });
}

test('graph: text inspection still works without diagram library', async ({page}) => {
  await page.route('**/vendor/d3.min.js', route => route.abort());
  await page.goto('/graph.html');
  await page.getByLabel('Graph JSON', {exact: true}).fill(JSON.stringify(payload));
  await page.getByRole('button', {name: 'Render', exact: true}).press('Enter');
  await expect(page.getByRole('status')).toContainText('Diagram unavailable');
  await page.getByLabel('Inspect node', {exact: true}).selectOption('b');
  await expect(page.locator('#detail')).toContainText('Alpha → CALLS → Beta');
});
