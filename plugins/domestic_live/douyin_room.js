// 只读取当前目标房间的主播身份和添加卡片资料；官网负责验证流程。
(target) => {
    let identity;
    const number = value => /^[1-9][0-9]{0,19}$/.test(String(value || ''));
    const current = () => location.hostname === 'live.douyin.com' &&
        location.pathname.replace(/\/$/, '') === '/' + target;
    const image = value => ({url_list: value?.url_list || value?.urlList || []});
    const remember = info => {
        const room = info?.room || info;
        const owner = room?.owner || info?.anchor;
        const uid = owner?.id_str || owner?.id || owner?.uid;
        const rid = owner?.web_rid || room?.web_rid;
        if (!current() || String(rid) !== target || !number(uid) ||
            !number(room?.id_str) || !Number.isInteger(room?.status)) return;
        identity = {uid: String(uid), web_rid: target, nickname: owner.nickname || '',
            avatar: image(owner.avatar_thumb), title: room.title || '',
            status: room.status, cover: image(room.cover)};
    };
    const visit = (data, depth = 0) => {
        if (!data || typeof data !== 'object' || depth > 20) return;
        remember(data);
        Object.values(data).forEach(value => visit(value, depth + 1));
    };
    const wanted = url => {
        try {
            const parsed = new URL(url, location.href);
            return current() && parsed.origin === location.origin &&
                parsed.pathname === '/webcast/room/web/enter/' &&
                parsed.searchParams.get('web_rid') === target;
        } catch (_) { return false; }
    };
    const capture = data => {
        if (data?.status_code !== 0) return;
        const block = data.data;
        for (const room of block?.data || []) remember({room, anchor: block.user});
    };
    const open = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url, ...args) {
        if (wanted(url)) this.addEventListener('load', () => {
            try { capture(this.responseType === 'json' ? this.response : JSON.parse(this.responseText)); }
            catch (_) {}
        }, {once: true});
        return open.call(this, method, url, ...args);
    };
    const fetch = window.fetch;
    window.fetch = function(input, ...args) {
        const result = fetch.call(this, input, ...args);
        if (wanted(typeof input === 'string' ? input : input?.url))
            result.then(r => r.clone().json()).then(capture).catch(() => {});
        return result;
    };
    const read = () => {
        if (!current()) return null;
        for (const chunk of self.__pace_f || []) {
            try { visit(JSON.parse(chunk[1].replace(/^\w+:/, ''))); } catch (_) {}
        }
        for (const script of document.querySelectorAll('script[type="application/json"], script#RENDER_DATA')) {
            try { visit(JSON.parse(script.textContent)); }
            catch (_) { try { visit(JSON.parse(decodeURIComponent(script.textContent))); } catch (_) {} }
        }
        return identity || null;
    };
    window.__ddmDouyinProfile = {peek: read, read};
}
