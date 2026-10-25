const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
function page(file) {
    const nodes = new Map();
    const alerts = [];
    const context = vm.createContext({
        window: {addEventListener() {}}, alert: msg => alerts.push(msg),
        console, FormData, confirm: () => true,
        document: {
            getElementById(id) {
                if (!nodes.has(id)) nodes.set(id, {value: '', style: {}, classList: {add() {}}, appendChild() {}});
                return nodes.get(id);
            },
            createElement: () => ({}),
        },
    });
    const source = readFileSync(file, 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
    vm.runInContext(source, context);
    return {context, nodes, alerts, run: code => vm.runInContext(code, context)};
}
(async () => {
    const p = page('static/index.html');
    p.run("addItem('同名', 100, false, 100, 'A', 0, '', 1); addItem('同名', 200, true, 300, 'B', 0, '', 2)");
    assert.equal(p.run('Object.keys(cart).length'), 2);
    let finish, requests = 0, body;
    p.context.fetch = async (url, options) => {
        requests++;
        body = JSON.parse(options.body);
        return await new Promise(resolve => { finish = resolve; });
    };
    const first = p.run('checkout()');
    await p.run('checkout()');
    p.run("addItem('別商品', 500, false, 500, '', 0, '', 3); clearCart()");
    assert.equal(requests, 1);
    assert.equal(p.run('Object.keys(cart).length'), 2);
    assert.equal(body.total_amount, 300);
    assert.equal(Object.keys(JSON.parse(body.items_json)).length, 2);
    finish({ok: true});
    await first;
    assert.equal(p.run('Object.keys(cart).length'), 0);
    p.run("addItem('商品', 100, false, 100, '', 0, '', 1)");
    p.context.fetch = async () => { throw Error('offline'); };
    await p.run('checkout()');
    assert.equal(p.run('checkoutUncertain'), true);
    assert.equal(p.run('Object.keys(cart).length'), 1);
    const a = page('static/dashboard.html');
    a.run('dirtyItems.add(1); isDirty = true');
    a.context.document.getElementById('cat-1').value = 'アクセサリー';
    let sent;
    a.context.fetch = async (url, options) => { sent = options.body; return {ok: false}; };
    await a.run('saveAllChanges()');
    assert.equal(sent.get('category'), 'アクセサリー');
    assert.equal(a.run('isDirty'), true);
    assert.equal(a.run('dirtyItems.size'), 1);
    assert(!a.alerts.includes('すべての変更を保存しました！'));
    function registrationPage() {
        const p = page('static/index.html');
        for (const [id, value] of Object.entries({newName: '作品', newPrice: '100', newMaterial: '0'})) {
            p.context.document.getElementById(id).value = value;
        }
        p.context.document.getElementById('newFile').files = [new Blob(['image'])];
        return p;
    }
    const r = registrationPage();
    let posts = 0, resolvePost;
    r.context.fetch = async (url, options) => {
        if (!options) throw Error('一覧取得失敗');
        posts++;
        return new Promise(resolve => { resolvePost = resolve; });
    };
    r.context.document.getElementById('newPrice').value = '';
    await r.run('submitNewItem()');
    assert.match(r.alerts.at(-1), /定価を入力/);
    r.context.document.getElementById('newPrice').value = '100';
    r.context.document.getElementById('newMaterial').value = '';
    await r.run('submitNewItem()');
    assert.match(r.alerts.at(-1), /材料費を入力/);
    assert.equal(posts, 0);
    r.context.document.getElementById('newMaterial').value = '0';
    const registration = r.run('submitNewItem()');
    await r.run('submitNewItem()');
    assert.equal(posts, 1);
    assert.equal(r.context.document.getElementById('registerItemButton').disabled, true);
    resolvePost({ok: true});
    await registration;
    assert.equal(r.alerts.at(-1), '登録しました');
    assert.equal(r.context.document.getElementById('registerItemButton').disabled, true);
    for (const response of [null, {ok: false, status: 500}, {ok: false, status: 422}]) {
        const p = registrationPage();
        let calls = 0;
        p.context.fetch = async () => { calls++; if (!response) throw Error('offline'); return response; };
        await p.run('submitNewItem()');
        await p.run('submitNewItem()');
        const uncertain = !response || response.status === 500;
        assert.equal(calls, uncertain ? 1 : 2);
        assert.equal(p.run('itemRegistrationUncertain'), uncertain);
        assert.equal(p.context.document.getElementById('newName').value, '作品');
    }
    console.log('PASS: 商品登録の必須入力、0円、連打防止、保存結果不明時の再送停止、422再試行、保存成功後の一覧取得失敗');
    console.log('PASS: 同名商品、合計、連打防止、会計中の変更防止、通信不明時の再送停止、カテゴリ保存、保存失敗時の入力保持');
})().catch(error => { console.error(error); process.exitCode = 1; });
