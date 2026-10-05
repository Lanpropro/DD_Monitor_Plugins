// 只收集官网关注面板自己发出的响应；不发 API 请求，也不记录 Cookie 或签名 URL。
(() => {
    if (window.__ddmDouyinFollows) return;
    const pages = new Map();
    const wanted = url => {
        try {
            const parsed = new URL(url, location.href);
            return parsed.origin === location.origin && parsed.pathname === '/aweme/v1/web/user/following/list/';
        } catch (_) { return false; }
    };
    const cursor = params => JSON.stringify(['offset', 'min_time', 'max_time'].map(k => String(params.get(k) || 0)));
    const roomInfo = value => {
        try {
            const room = typeof value === 'string' ? JSON.parse(value) : value;
            if (!room) return {};
            return {web_rid: room.web_rid, title: room.title, status: room.status,
                owner: {web_rid: room.owner?.web_rid}};
        } catch (_) { return {}; }
    };
    const capture = (url, data) => {
        try {
            const parsed = new URL(url, location.href);
            if (parsed.origin !== location.origin || parsed.pathname !== '/aweme/v1/web/user/following/list/') return;
            const uid = parsed.searchParams.get('user_id');
            if (!uid || !data || typeof data !== 'object') return;
            const payload = {status_code: data.status_code, has_more: data.has_more,
                offset: data.offset, min_time: data.min_time, max_time: data.max_time};
            if (Array.isArray(data.followings)) payload.followings = data.followings.map(user => ({
                uid: user.uid, sec_uid: user.sec_uid, nickname: user.nickname, remark_name: user.remark_name,
                web_rid: user.web_rid, avatar_medium: user.avatar_medium, avatar_thumb: user.avatar_thumb,
                room_data: roomInfo(user.room_data)
            }));
            pages.set(uid + ':' + cursor(parsed.searchParams), payload);
        } catch (_) { /* 官网非 JSON 的错误页不作为空关注列表。 */ }
    };
    const open = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url, ...args) {
        if (wanted(url)) this.addEventListener('load', () => {
            try {
                const data = this.responseType === 'json' ? this.response : JSON.parse(this.responseText);
                capture(url, data);
            } catch (_) {}
        }, {once: true});
        return open.call(this, method, url, ...args);
    };
    const fetch = window.fetch;
    window.fetch = function(input, ...args) {
        return fetch.call(this, input, ...args).then(response => {
            const url = input instanceof Request ? input.url : String(input);
            if (wanted(url)) {
                response.clone().json().then(data => capture(url, data)).catch(() => {});
            }
            return response;
        });
    };
    const visible = element => element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden';
    const panel = () => [...document.querySelectorAll('[data-e2e="user-fans-container"]')].find(visible);
    const openPanel = () => {
        // 只点击包含数字的关注计数，不能点击会改变关注关系的“关注”按钮。
        const counter = [...document.querySelectorAll('button,a,span,div')].find(element => {
            const text = element.textContent.trim();
            return visible(element) && /^(?:关注\s*[\d.,万亿kKmM]+|[\d.,万亿kKmM]+\s*关注|Following\s*[\d.,kKmM]+)$/.test(text);
        });
        if (counter) counter.click();
        return !!counter;
    };
    const read = async params => {
        const prefix = params.get('user_id') + ':';
        const index = Number(params.get('offset') || 0);
        const deadline = Date.now() + 25000;
        let clicked = false;
        while (Date.now() < deadline) {
            if (window.__ddmFollowCancelled) throw new Error('cancelled');
            const received = [...pages].filter(([key]) => key.startsWith(prefix));
            if (received[index]) {
                // 本地页号只用于读取已经观察到的页面；官网自行决定请求游标。
                return {...received[index][1], offset: index + 1, min_time: 0, max_time: 0};
            }
            const container = panel();
            if (!container && !clicked) clicked = openPanel();
            if (container) {
                const scroll = [container, ...container.querySelectorAll('*')].find(element =>
                    element.scrollHeight > element.clientHeight && /auto|scroll/.test(getComputedStyle(element).overflowY));
                if (scroll) {
                    scroll.scrollTop = scroll.scrollHeight;
                    scroll.dispatchEvent(new Event('scroll', {bubbles: true}));
                }
            }
            await new Promise(resolve => setTimeout(resolve, 150));
        }
        throw new Error(clicked || panel() ? 'follow_page_timeout' : 'follow_panel_not_found');
    };
    window.__ddmDouyinFollows = {read};
})();
