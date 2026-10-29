// Synthetic official client only: no browser, network, accounts or real messages.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');
const source = fs.readFileSync(path.join(__dirname, '../plugins/domestic_live/douyin_send.js'), 'utf8');
async function fixture({cancelled=false, host='https://live.douyin.com', selfSee=0}={}) {
    let calls = 0, received;
    const client = {W: async params => {
        calls++; received=params;
        return {data:{status_code:0,data:{msg_id_str:'7560000000000000001',content_self_see_status:selfSee,
            content:'private',user:{secret:'not-exported'}}}};
    }};
    const requireModule = () => client;
    requireModule.m = {123:function(){return '/webcast/room/chat/ paste_edit_method';}};
    const chunks=[];
    chunks.push = function(entry){entry[2](requireModule);};
    const window={webpackChunkdouyin_live_v2:chunks,__ddmChatCancelled:cancelled};
    const fn = vm.runInNewContext('('+source+')', {window,location:{origin:host,pathname:'/123'},setTimeout});
    const task={id:'synthetic',webRid:'123',roomId:'999999',text:'hello'};
    if(cancelled || host!=='https://live.douyin.com') {
        await assert.rejects(fn(task));assert.strictEqual(calls,0);return;
    }
    const result=await fn(task);
    assert.strictEqual(calls,1);
    assert.strictEqual(received.room_id_str,'999999');
    assert.strictEqual(received.content,'hello');
    assert.strictEqual(received.paste_edit_method,'non_paste');
    assert.strictEqual(result.data.msg_id_str,'7560000000000000001');
    assert.strictEqual(result.data.content_self_see_status,Number.isInteger(selfSee)?selfSee:null);
    assert(!JSON.stringify(result).includes('private'));
    assert(!JSON.stringify(result).includes('secret'));
}
(async()=>{await fixture();await fixture({cancelled:true});await fixture({host:'https://example.com'});
await fixture({selfSee:{secret:'private'}});
console.log('PASS: official helper called once, internal room, cancellation, origin guard, sanitized receipt');
})().catch(error=>{console.error(error);process.exit(1)});
