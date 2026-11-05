// Inspect and focus the official input only; text and Enter come from native Qt events.
(task, action) => {
    if (window.__ddmChatCancelled) return {error: 'cancelled_before_send'};
    if (location.origin !== 'https://live.douyin.com')
        return location.href === 'about:blank' ? {ready: false, reason: 'page'} : {error: 'wrong_room_page'};
    if (location.pathname.replace(/\/$/, '') !== '/' + task.webRid) return {error: 'wrong_room_page'};
    const room = window.__STORE__?.roomStore?.roomInfo?.roomId;
    const uid = window.__STORE__?.userStore?.userInfo?.id_str;
    if (!/^[1-9]\d{0,19}$/.test(String(room)) || !/^[1-9]\d{0,19}$/.test(String(uid)))
        return {ready: false, reason: 'context'};
    if (String(room) !== task.roomId) return {error: 'wrong_room_session'};
    if (String(uid) !== task.senderUid) return {error: 'account_mismatch'};
    const visible = element => {
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 && getComputedStyle(element).visibility !== 'hidden';
    };
    const editors = [...document.querySelectorAll('#chatInput [contenteditable="true"],#chatInput textarea')].filter(visible);
    if (!editors.length) return {ready: false, reason: 'editor'};
    if (editors.length !== 1) return {error: 'ambiguous_editor'};
    const editor = editors[0];
    if (editor.disabled || editor.getAttribute('aria-disabled') === 'true') return {ready: false, reason: 'editor'};
    const text = editor.tagName === 'TEXTAREA' ? editor.value : editor.textContent;
    if (action === 'verify') {
        if (window.__ddmNativeInput !== editor || document.activeElement !== editor) return {error: 'input_focus_lost'};
        if (!text) return {ready: false, reason: 'input'};
        if (text !== task.text) return {error: 'input_mismatch'};
        const button = editor.closest('#chatInput')?.querySelector('.webcast-chatroom___send-btn');
        if (!button || button.disabled || button.getAttribute('aria-disabled') === 'true') return {ready: false, reason: 'input'};
        return {ready: true};
    }
    if (text) return {error: 'official_draft_exists'};
    if (action === 'focus') {
        editor.focus();
        window.__ddmNativeInput = editor;
        if (document.activeElement !== editor) return {error: 'input_focus_lost'};
    }
    return {ready: true};
}
