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
    console.log('PASS: 同名商品、合計、連打防止、会計中の変更防止、通信不明時の再送停止、カテゴリ保存、保存失敗時の入力保持');
})().catch(error => { console.error(error); process.exitCode = 1; });
