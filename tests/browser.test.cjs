// Isolated browser rendering test. Every request is intercepted; no live services.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
    const browser = await chromium.launch({channel:'msedge', headless:true});
    try {
        const context = await browser.newContext({viewport:{width:390,height:844}, isMobile:true, hasTouch:true});
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', e => errors.push(e.message));
        page.on('dialog', d => { errors.push('Unexpected native dialog: ' + d.message()); d.dismiss(); });
        let sales = [], itemRequests = 0;
        let items = [{id:1,name:'<img src=x onerror="window.injected=true">作品',price:90,category:'キーホルダー',creator_id:'A123',material_fee:10,remarks:'',image_url:'',sale_price:null,is_sale:false}];
        await page.route('**/*', async route => {
            const url = new URL(route.request().url());
            const p = url.pathname;
            if (p === '/' || p === '/admin' || p === '/static/ui.js') {
                const file = p === '/' ? 'static/index.html' : p === '/admin' ? 'static/dashboard.html' : 'static/ui.js';
                return route.fulfill({contentType:p.endsWith('.js')?'application/javascript':'text/html',body:fs.readFileSync(file,'utf8')});
            }
            if (p === '/items') {
                itemRequests++;
                const publicItems = items.map(({creator_id, material_fee, remarks, ...item}) => item);
                return route.fulfill({json:route.request().method() === 'GET' ? publicItems : {item_id:2}});
            }
            if (p === '/admin/items') return route.fulfill({json:items});
            if (p === '/sales') {
                const data = route.request().postDataJSON();
                sales.push(data);
                return route.fulfill({json:{sale_id:42}});
            }
            if (p === '/members') return route.fulfill({json:[]});
            if (p === '/reports/dashboard') return route.fulfill({json:{overall:{revenue:190,count:2},period:{revenue:90,count:1,sales:[{id:42,created_at:'2026-10-26T00:00:00',total_amount:90,items_json:sales[0]?.items_json || '{}',voided:false,corrections:[]}]},label:'2026-10-26'}});
            if (p.startsWith('/export/')) return route.fulfill({contentType:'text/csv',body:'test'});
            return route.fulfill({status:404,body:''});
        });
        await page.goto('http://127.0.0.1:8765/');
        await page.locator('.item-card').click();
        await page.locator('#productSearch').fill('作品');
        assert.equal(await page.locator('#searchGrid .item-card').count(), 1);
        assert(!(await page.locator('#tabsContainer').isVisible()));
        await page.locator('#productSearch').fill('存在しない');
        assert.equal(await page.locator('#searchGrid .item-card').count(), 0);
        await page.getByRole('button',{name:'検索を終了',exact:true}).click();
        assert(await page.locator('#tabsContainer').isVisible());
        assert.equal(await page.locator('#totalDisplay').innerText(), '90');
        assert.equal(await page.evaluate(() => window.injected), undefined);
        await page.locator('#checkoutButton').click();
        await page.locator('dialog[open]').waitFor();
        assert.match(await page.locator('dialog').innerText(), /完了.*取引番号：42/s);
        assert.equal(sales.length, 1);
        assert(sales[0].request_id);
        assert(!JSON.stringify(sales[0]).includes('creator_id'));
        const box = await page.locator('dialog').boundingBox();
        assert(box.x >= 0 && box.x + box.width <= 390);
        const screenshot = path.join(process.env.TEMP || '.', 'fes-checkout-mobile.png');
        await page.screenshot({path:screenshot});
        await page.getByRole('button',{name:'確認しました',exact:true}).click();
        await page.getByRole('button',{name:'⚙️ 商品登録',exact:true}).click();
        await page.locator('#newName').fill('入力を保持');
        await page.locator('#registerItemButton').click();
        await page.locator('dialog[open]').waitFor();
        assert.match(await page.locator('dialog').innerText(), /学籍番号/);
        await page.getByRole('button',{name:'確認しました',exact:true}).click();
        await page.locator('#newPrice').fill('100');
        await page.locator('#newMaterial').fill('80');
        await page.locator('#newCreator').fill('A123');
        await page.locator('#newFile').setInputFiles({name:'test.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==','base64')});
        await page.locator('#registerItemButton').click();
        assert(await page.locator('#costConfirmation').isVisible());
        assert.match(await page.locator('#costConfirmation').innerText(), /還元見込額.*70円/s);
        await page.getByRole('button',{name:'修正する',exact:true}).click();
        items = [...items, {...items[0],id:2,name:'別端末の新商品'}];
        const request = page.waitForResponse(r => r.url().endsWith('/items'));
        await page.getByRole('button',{name:'商品一覧を更新',exact:true}).click();
        await request;
        await page.locator('#productSearch').fill('別端末');
        assert.equal(await page.locator('#searchGrid .item-card').count(), 1);
        await page.getByRole('button',{name:'検索を終了',exact:true}).click();
        assert.equal(await page.locator('#newName').inputValue(), '入力を保持');
        assert.equal(await page.locator('#newFile').evaluate(el => el.files.length), 1);
        await page.goto('http://127.0.0.1:8765/admin');
        await page.waitForFunction(() => document.getElementById('reportStatus').textContent.includes('最終取得'));
        assert.equal(await page.locator('#c-1').inputValue(), 'A123');
        await page.getByRole('button',{name:'🧾 取引履歴',exact:true}).click();
        assert.match(await page.locator('#salesTable').innerText(), /42/);
        assert.equal(await page.evaluate(() => window.injected), undefined);
        await page.locator('#periodStart').fill('2026-10-26');
        await page.locator('#periodEnd').fill('2026-10-26');
        const dateRequest = page.waitForRequest(r => r.url().includes('start=2026-10-26'));
        await page.getByRole('button',{name:'この期間を表示',exact:true}).click();
        await dateRequest;
        await page.waitForFunction(() => !reportLoading);
        assert.equal(await page.locator('#totalRevenue').innerText(), '190');
        assert.equal(await page.locator('#periodRevenue').innerText(), '90');
        const fullRequest = page.waitForRequest(r => r.url().includes('/export/overall'));
        await page.evaluate(() => downloadCSV('/export/overall','all.csv'));
        assert.equal(new URL((await fullRequest).url()).search, '');
        const periodRequest = page.waitForRequest(r => r.url().includes('/export/period'));
        await page.evaluate(() => downloadCSV('/export/period','period.csv',true));
        assert.equal(new URL((await periodRequest).url()).searchParams.get('start'), '2026-10-26');
        await page.screenshot({path:path.join(process.env.TEMP || '.', 'fes-admin-mobile.png')});
        assert.equal(errors.length, 0, errors.join('\n'));
        console.log('PASS: mobile receipt dialog, required inputs, cost confirmation, form/photo retained on refresh, XSS escaping, admin history and date filters');
        console.log('Screenshot: ' + screenshot);
    } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
