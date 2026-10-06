// 只读取目标主播的身份和固定网页房间号，不导出整个页面状态或账号凭证。
(target) => {
    let identity, failure;
    const number = value => /^[1-9][0-9]{0,19}$/.test(String(value || ''));
    const remember = user => {
        if (!user || (user.sec_uid || user.secUid) !== target || !number(user.uid)) return;
        let room = user.room_data || user.roomData || {};
        try { if (typeof room === 'string') room = JSON.parse(room); } catch (_) { room = {}; }
        const owner = room?.owner;
        const ownerUid = owner?.id_str || owner?.idStr || owner?.uid || owner?.id;
        const sameOwner = !ownerUid || String(ownerUid) === String(user.uid);
        const rid = user.web_rid || user.webRid || (sameOwner && (room?.web_rid || owner?.web_rid || owner?.webRid));
        identity = {uid: String(user.uid), sec_uid: target,
            web_rid: number(rid) ? String(rid) : identity?.web_rid || ''};
    };
    const wanted = url => {
        try {
            const parsed = new URL(url, location.href);
            return parsed.origin === location.origin && parsed.pathname === '/aweme/v1/web/user/profile/other/' &&
                parsed.searchParams.get('sec_user_id') === target;
        } catch (_) { return false; }
    };
    const capture = data => { if (data?.status_code === 0) remember(data.user); };
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
    const visit = (data, depth = 0) => {
        if (!data || typeof data !== 'object' || depth > 20) return;
        remember(data);
        Object.values(data).forEach(value => visit(value, depth + 1));
    };
    const rendered = () => {
        for (const script of document.querySelectorAll('script[type="application/json"], script#RENDER_DATA')) {
            try { visit(JSON.parse(script.textContent)); }
            catch (_) { try { visit(JSON.parse(decodeURIComponent(script.textContent))); } catch (_) {} }
        }
        // 官网 React 首屏资料使用 camelCase，不一定再次发送 profile/other 请求。
        const fromProps = (props, depth = 0) => {
            if (!props || depth > 5) return;
            remember(props);
            for (const key of ['user', 'userInfo', 'currentUserInfo', 'userDetail']) remember(props[key]);
            for (const child of Array.isArray(props.children) ? props.children : [props.children])
                fromProps(child?.props, depth + 1);
        };
        for (const element of document.querySelectorAll('[data-e2e^="user-info"], [data-e2e="user-detail"], [data-e2e="user-header"]')) {
            const propsKey = Object.keys(element).find(key => key.startsWith('__reactProps$'));
            fromProps(element[propsKey]);
            const fiberKey = Object.keys(element).find(key => key.startsWith('__reactFiber$'));
            let fiber = element[fiberKey];
            for (let depth = 0; fiber && depth < 8; depth++, fiber = fiber.return) fromProps(fiber.memoizedProps);
        }
    };
    const peek = () => {
        rendered();
        return identity || (failure ? {error: failure} : null);
    };
    const read = async () => {
        const deadline = Date.now() + 15000;
        while (Date.now() < deadline) {
            if (window.__ddmProfileCancelled) return;
            rendered();
            if (identity) return;
            // 使用已经装载的官网客户端生成设备参数、签名和验证流程。
            const chunks = window.webpackChunkdouyin_web;
            let require;
            if (chunks) chunks.push([['ddm-profile-' + Date.now()], {}, r => { require = r; }]);
            if (require?.m) {
                const modules = Object.entries(require.m);
                const common = modules.find(([, fn]) => fn.toString().includes('CHANNEL_PC_WEB:function') &&
                    fn.toString().includes('COMMON_SEARCH_PARAMS:function'));
                const client = modules.find(([, fn]) => fn.toString().includes('skipCheckCode') &&
                    fn.toString().includes('securitySdkInitWeb') && fn.toString().includes('withCredentials'));
                if (common && client) {
                    try {
                        capture(await require(client[0]).U2('/aweme/v1/web/user/profile/other/',
                            {...require(common[0]).COMMON_SEARCH_PARAMS, sec_user_id: target}, {timeout: 12000}));
                    } catch (_) { /* 验证失败通过有限等待后的状态提示呈现。 */ }
                    if (!identity) failure = 'profile_unavailable';
                    return;
                }
            }
            await new Promise(resolve => setTimeout(resolve, 200));
        }
        if (!identity) failure = 'profile_unavailable';
    };
    window.__ddmDouyinProfile = {peek, read};
}
