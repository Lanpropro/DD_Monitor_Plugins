// 读取官网已经加载的关注面板；不发 API 请求，也不记录 Cookie 或签名 URL。
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
    const listProps = container => {
        // React DOM 上的当前 props 包含列表组件；只读取已知列表字段，不遍历网站状态。
        const propsKey = Object.keys(container).find(key => key.startsWith('__reactProps$'));
        const fromProps = props => {
            if (Array.isArray(props?.userList)) return props;
            const children = Array.isArray(props?.children) ? props.children : [props?.children];
            return children.find(child => Array.isArray(child?.props?.userList))?.props;
        };
        const current = fromProps(container[propsKey]);
        if (current) return current;
        const fiberKey = Object.keys(container).find(key => key.startsWith('__reactFiber$'));
        let fiber = container[fiberKey];
        for (let depth = 0; fiber && depth < 8; depth++, fiber = fiber.return) {
            const props = fromProps(fiber.memoizedProps);
            if (props) return props;
        }
    };
    const renderedUser = user => ({
        uid: user.uid, sec_uid: user.secUid, nickname: user.nickname, remark_name: user.remarkName,
        web_rid: user.webRid, avatar_medium: {url_list: user.avatarUri ? [user.avatarUri] : []},
        room_data: roomInfo(user.roomData)
    });
    const openPanel = () => {
        // 只点击包含数字的关注计数，不能点击会改变关注关系的“关注”按钮。
        const counter = [...document.querySelectorAll('button,p,span,div')].find(element => {
            const text = element.textContent.trim();
            // 侧栏的“关注 1”是信息流导航，不是个人页的关注列表入口。
            return !element.closest('a,nav,[role="navigation"]') && typeof element.onclick === 'function' &&
                visible(element) && /^(?:关注\s*[\d.,万亿kKmM]+|[\d.,万亿kKmM]+\s*关注|Following\s*[\d.,kKmM]+)$/.test(text);
        });
        if (counter) counter.click();
        return !!counter;
    };
    const renderedCounts = new Map();
    const read = async params => {
        const uid = params.get('user_id');
        const prefix = params.get('user_id') + ':';
        const index = Number(params.get('offset') || 0);
        const generation = window.__ddmFollowGeneration;
        if (index === 0) renderedCounts.delete(uid);
        const deadline = Date.now() + 25000;
        let clicked = false, nextScroll = 0;
        while (Date.now() < deadline) {
            if (window.__ddmFollowCancelled || window.__ddmFollowGeneration !== generation) throw new Error('cancelled');
            const container = panel();
            if (!container && !clicked) clicked = openPanel();
            const props = container && listProps(container);
            if (props) {
                if (!props.isSelf || props.activeTab !== 0 || props.searchVal ||
                        String(props.currentUserInfo?.uid) !== uid) throw new Error('follow_panel_wrong_list');
                const count = renderedCounts.get(uid) || 0;
                const footer = container.querySelector('[data-e2e="user-fans-footer"]');
                const text = footer?.textContent.trim();
                const complete = props.refIsLoadingShow?.current === false &&
                    props.refNoMoreText?.current === '暂时没有更多了' &&
                    (text === '暂时没有更多了' || (!props.userList.length && text === '你还没有关注'));
                if (complete || props.userList.length > count) {
                    const followings = props.userList.slice(count).map(renderedUser);
                    renderedCounts.set(uid, props.userList.length);
                    return {status_code: 0, followings, has_more: complete ? 0 : 1,
                        offset: index + 1, min_time: 0, max_time: 0};
                }
            } else if (!renderedCounts.has(uid)) {
                const received = [...pages].filter(([key]) => key.startsWith(prefix));
                if (received[index]) {
                    // 本地页号只用于读取已经观察到的页面；官网自行决定请求游标。
                    return {...received[index][1], offset: index + 1, min_time: 0, max_time: 0};
                }
            }
            if (container) {
                const scroll = [container, ...container.querySelectorAll('*')].find(element =>
                    element.clientHeight > 0 && /auto|scroll/.test(getComputedStyle(element).overflowY));
                // 官网的滚动监听有 250ms 防抖；连续每 150ms 触发会使下一页永远不加载。
                if (scroll && Date.now() >= nextScroll) {
                    nextScroll = Date.now() + 600;
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
