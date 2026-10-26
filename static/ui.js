// In-page dialogs work in mobile browsers that suppress native alert/confirm.
function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
}
function safeImageUrl(value) {
    try { const u = new URL(value); return u.protocol === 'https:' ? u.href : ''; }
    catch (_) { return ''; }
}
let dialogQueue = Promise.resolve();
function uiDialog(message, confirmation) {
    const task = dialogQueue.then(() => new Promise(resolve => {
        const previousFocus = document.activeElement;
        const panel = document.createElement('dialog');
        panel.setAttribute('aria-label', confirmation ? '確認' : 'お知らせ');
        panel.style.cssText = 'box-sizing:border-box;width:calc(100% - 24px);max-width:480px;max-height:85vh;overflow:auto;border:2px solid #1565c0;border-radius:12px;padding:20px;';
        const text = document.createElement('p');
        text.textContent = message;
        text.style.whiteSpace = 'pre-wrap';
        panel.appendChild(text);
        const finish = value => { panel.close(); panel.remove(); previousFocus?.focus?.(); resolve(value); };
        for (const [label, value] of (confirmation ? [['続ける', true], ['戻る', false]] : [['確認しました', true]])) {
            const button = document.createElement('button');
            button.textContent = label;
            button.style.cssText = 'font-size:16px;padding:14px;margin:6px;min-width:110px;';
            button.onclick = () => finish(value);
            panel.appendChild(button);
        }
        panel.addEventListener('cancel', e => { e.preventDefault(); finish(false); });
        document.body.appendChild(panel);
        panel.showModal();
    }));
    dialogQueue = task.catch(() => {});
    return task;
}
function uiNotice(message) { return uiDialog(message, false); }
function uiConfirm(message) { return uiDialog(message, true); }
