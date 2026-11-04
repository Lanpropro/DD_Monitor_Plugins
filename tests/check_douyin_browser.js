// Synthetic official client only: no browser, network, accounts or real messages.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');
const source = fs.readFileSync(path.join(__dirname, '../plugins/domestic_live/douyin_send.js'), 'utf8');
const inspectClient = vm.runInNewContext('(' + fs.readFileSync(path.join(__dirname, '../plugins/domestic_live/douyin_client.js'), 'utf8') + ')');
function checkDependencies() {
    const requireModule=()=>{throw new Error('Read-only inspection must not initialize modules');};
    requireModule.m={123:function(module,exports,$){$('456');return '/webcast/room/chat/ paste_edit_method';}};
    assert.strictEqual(inspectClient(requireModule).reason,'dependency');
    requireModule.m[456]=function(){};
    assert.strictEqual(inspectClient(requireModule).ready,true);
    assert.strictEqual(inspectClient(requireModule).id,'123');
}
async function fixture({cancelled=false, host='https://live.douyin.com', selfSee=0, securityStatus='success',
    cancelDuringSecurity=false, roomId='999999', uid='42', lateContext=false, switchAccountDuringSecurity=false,
    lateModule=false}={}) {
    let calls = 0, received, securityReady=false, captures=0;
    const client = {W: async params => {
        assert(securityReady,'Send must wait for security initialization');
        calls++; received=params;
        return {data:{status_code:0,data:{msg_id_str:'7560000000000000001',content_self_see_status:selfSee,
            content:'private',user:{secret:'not-exported'}}}};
    }};
    const security = {getSecuritySdkInitWebStatus:async()=>{
        await new Promise(resolve=>setTimeout(resolve,10));
        if(cancelDuringSecurity)window.__ddmChatCancelled=true;
        if(switchAccountDuringSecurity)window.__STORE__.userStore.userInfo.id_str='43';
        securityReady=['success','skip_by_client_version'].includes(securityStatus);
        return securityStatus;
    }};
    const requireModule = id => id==='789'?security:client;
    requireModule.m = {123:function(){return '/webcast/room/chat/ paste_edit_method';},
        789:function(){return 'getSecuritySdkInitWebStatus:function securitySdkInitWeb:function';}};
    if(lateModule){
        const factory=requireModule.m[123];
        delete requireModule.m[123];
        setTimeout(()=>{requireModule.m[123]=factory;},30);
    }
    const chunks=[];
    const loadedChunks=new Set();
    chunks.push = function(entry){
        if(entry[0].some(id=>!loadedChunks.has(id))){captures++;entry[2](requireModule);}
        entry[0].forEach(id=>loadedChunks.add(id));
    };
    const window={webpackChunkdouyin_live_v2:chunks,__ddmChatCancelled:cancelled};
    const context={roomStore:{roomInfo:{roomId}},userStore:{userInfo:{id_str:uid}}};
    if(lateContext){
        window.__STORE__={roomStore:{roomInfo:{roomId:'0'}},userStore:{userInfo:{id_str:'0'}}};
        setTimeout(()=>{window.__STORE__=context;},30);
    }
    else window.__STORE__=context;
    const fn = vm.runInNewContext('('+source+')', {window,location:{origin:host,pathname:'/123'},setTimeout});
    const task={id:'synthetic',webRid:'123',roomId:'999999',senderUid:'42',text:'hello'};
    if(cancelled || host!=='https://live.douyin.com' || !['success','skip_by_client_version'].includes(securityStatus)
        || cancelDuringSecurity || roomId!==task.roomId || uid!==task.senderUid || switchAccountDuringSecurity) {
        await assert.rejects(fn(task,inspectClient));assert.strictEqual(calls,0);return;
    }
    const result=await fn(task,inspectClient);
    assert.strictEqual(calls,1);
    assert.strictEqual(captures,1);
    assert.strictEqual(received.room_id_str,'999999');
    assert.strictEqual(received.content,'hello');
    assert.strictEqual(received.paste_edit_method,'non_paste');
    assert.strictEqual(result.data.msg_id_str,'7560000000000000001');
    assert.strictEqual(result.data.content_self_see_status,Number.isInteger(selfSee)?selfSee:null);
    assert(!JSON.stringify(result).includes('private'));
    assert(!JSON.stringify(result).includes('secret'));
}
(async()=>{checkDependencies();await fixture();await fixture({cancelled:true});await fixture({host:'https://example.com'});
await fixture({selfSee:{secret:'private'}});
await fixture({securityStatus:'timeout'});await fixture({securityStatus:'fail'});
await fixture({securityStatus:'skip_by_client_version'});await fixture({securityStatus:'unknown'});
await fixture({cancelDuringSecurity:true});
await fixture({roomId:'888888'});await fixture({uid:'43'});await fixture({lateContext:true});
await fixture({switchAccountDuringSecurity:true});
await fixture({lateModule:true});
console.log('PASS: official helper called once, internal room, cancellation, origin guard, sanitized receipt');
})().catch(error=>{console.error(error);process.exit(1)});
