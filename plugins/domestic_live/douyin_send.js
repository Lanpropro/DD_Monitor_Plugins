// Execute once in the official room page; never export cookies or signed URLs.
async (task, inspectClient) => {
    window.__ddmChatStage = 'page';
    if (location.origin !== 'https://live.douyin.com' || location.pathname.replace(/\/$/, '') !== '/' + task.webRid)
        throw new Error('wrong_room_page');
    const deadline = Date.now() + 20000;
    while (Date.now() < deadline) {
        if (window.__ddmChatCancelled) throw new Error('cancelled_before_send');
        let require;
        const chunks = window.webpackChunkdouyin_live_v2;
        if (Array.isArray(chunks)) chunks.push([['ddm-chat-' + task.id], {}, r => { require = r; }]);
        const client = inspectClient(require);
        if (client.ready) {
            window.__ddmChatStage = 'module';
            const exports = require(client.id);
            const send = exports?.W;
            if (typeof send !== 'function') throw new Error('official_client_changed');
            window.__ddmChatStage = 'security';
            const securityEntry = Object.entries(require.m).find(([, factory]) => {
                const code = factory.toString();
                return code.includes('getSecuritySdkInitWebStatus:function') && code.includes('securitySdkInitWeb:function');
            });
            if (!securityEntry) throw new Error('official_security_changed');
            const security = require(securityEntry[0]);
            if (typeof security?.getSecuritySdkInitWebStatus !== 'function') throw new Error('official_security_changed');
            const status = await security.getSecuritySdkInitWebStatus();
            if (status === 'timeout') throw new Error('official_security_timeout');
            if (status !== 'success' && status !== 'skip_by_client_version') throw new Error('official_security_failed');
            if (window.__ddmChatCancelled) throw new Error('cancelled_before_send');
            if (location.origin !== 'https://live.douyin.com' || location.pathname.replace(/\/$/, '') !== '/' + task.webRid)
                throw new Error('wrong_room_page');
            window.__ddmChatSubmitted = true;
            window.__ddmChatStage = 'request';
            // The official client supplies device data, SDK initialization and per-request signatures.
            const response = await send({room_id_str: task.roomId, content: task.text,
                type: 0, rtf_content: '', paste_edit_method: 'non_paste'});
            window.__ddmChatStage = 'response';
            const payload = response?.data;
            const data = payload?.data;
            if (!payload || !Number.isInteger(payload.status_code)) throw new Error('invalid_response');
            return {status_code: payload.status_code, data: {
                msg_id_str: typeof data?.msg_id_str === 'string' ? data.msg_id_str : '',
                content_self_see_status: Number.isInteger(data?.content_self_see_status) ? data.content_self_see_status : null
            }};
        }
        await new Promise(resolve => setTimeout(resolve, 150));
    }
    throw new Error('official_client_not_ready');
}
