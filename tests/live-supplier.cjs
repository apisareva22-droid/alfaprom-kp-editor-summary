// Explicit live check against the supplier and deployed cloud function.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const site = 'https://apisareva22-droid.github.io/alfaprom-kp-editor-summary/';
const supplier = 'https://hmru.ru/catalog/markirovochnoe_i_etiketirovochnoe_oborudovanie/etiketirovshchiki/avtomaticheskiy_etiketirovshchik_dlya_paketov_hlmt_280p_vakuumnyy_zakhvat_termotransfernaya_pechat/';

(async () => {
  const browser = await chromium.launch({channel:'chrome',headless:true});
  try {
    const page = await browser.newPage();
    page.on('pageerror', error => console.error(error.message));
    page.on('requestfailed', request => console.error(new URL(request.url()).hostname, request.failure()?.errorText));
    if (!process.env.TEST_URL) {
      await page.route(site, route => route.fulfill({
        path:path.join(__dirname,'..','index.html'), contentType:'text/html; charset=utf-8'
      }));
    }
    await page.goto(process.env.TEST_URL || site);
    const card = page.locator('.product-editor-card').first();
    await card.getByRole('button',{name:'Добавить оборудование по ссылке'}).click();
    await card.getByRole('textbox',{name:'Ссылка на оборудование'}).fill(supplier);
    await card.getByRole('button',{name:'Добавить',exact:true}).click();
    const status = card.getByRole('status').filter({hasText:'Импорт завершён'});
    await status.waitFor({timeout:180000});
    assert.doesNotMatch(await status.textContent(), /Пропущено/);
    assert.match(await card.locator('[data-field="productName"]').inputValue(), /HLMT-280P/);
    const price = await card.locator('[data-field="price"]').inputValue();
    assert.ok(Number(price)>0);
    const specs = await card.locator('[data-field="specsText"]').inputValue();
    assert.match(specs,/HUALIAN/i);
    assert.match(await card.locator('.rich-editor').textContent(),/HLMT-280P/);
    const photos = await page.evaluate(()=>state.products[0].photoIds.length);
    assert.ok(photos > 1);
    await page.waitForFunction(n=>document.querySelectorAll('#previewPages .product-photo-frame img').length === n,photos);
    if (process.env.IMPORT_SCREENSHOT) await card.locator('.product-editor-head').screenshot({path:process.env.IMPORT_SCREENSHOT});
    console.log({price, photos, characteristics:specs.split('\n').length});
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1});

