"""Catalog references, batch identity, integer money and captured read requests."""
import time
import unittest
from unittest.mock import Mock, patch
from bili_live_support import bili
from bili_live_fixtures import config,room_payload,bag_payload,guard_payload,upgrade_payload
from ddm_plugin_bili_live import gift_api as api

class ReadTests(unittest.TestCase):
    def test_anonymous_blind_box_is_available_while_server_disabled_gift_stays_blocked(self):
        # Minimal public roomGiftList metadata observed in both 21452505 and
        # 22637261, 2026-10-23. No account or live request is used by this test.
        data=room_payload()
        data['gift_config']['base_config']=[
            config(32251,name='心动盲盒',price=15000,gift_type=6),
            config(36081,name='九秋飞仙',price=3000000,max_send_limit=66)]
        data['gift_config']['room_config']=[]
        data['gift_data']['room_gift_list']['gold_list']=[
            {'gift_id':32251,'gift_scene':None,'special':{'special_type':0,'is_use':1,'tips':''}},
            {'gift_id':36081,'gift_scene':{'scene':'default_gift','pay_type':'send_gift'},
             'special':{'special_type':0,'is_use':0,'tips':'网络异常，请稍后再试'}}]
        items={i.gift_id:i for i in api.normalize_catalog(data,'21452505').tabs[0].items}
        self.assertFalse(items[32251].disabled_reason)
        self.assertEqual(items[36081].disabled_reason,'网络异常，请稍后再试')
        # An ordinary route becomes usable only when the fresh server response
        # grants permission. A client-side gift ID allowlist must not override it.
        data['gift_data']['room_gift_list']['gold_list'][1]['special']['is_use']=1
        unlocked=api.normalize_catalog(data,'21452505').tabs[0].items[-1]
        self.assertFalse(unlocked.disabled_reason)
        for item in (items[36081],):
            with self.subTest(gift=item.name),patch.object(api.requests,'post') as post:
                with self.assertRaises(api.GiftSendError):
                    api.send_gift(api.GiftTarget('21452505',123,456),item,1,
                        cookies={'SESSDATA':'fake','bili_jct':'fake'},wallet=99999999,still_current=lambda:True)
                post.assert_not_called()

    def test_upgrade_gifts_keep_their_identity_price_and_quantity_rules(self):
        data=upgrade_payload()
        data['gift_config']['room_config']=[{'id':35640,'price':1700}]
        catalog=api.normalize_catalog(data,'90001')
        self.assertEqual([i.gift_id for i in catalog.tabs[0].items],[35638])
        parent=catalog.tabs[0].items[0]
        variants=getattr(parent,'variants',())
        self.assertEqual([(i.key,i.gift_id,i.name,i.price) for i in variants],[
            ('main:35638:35641',35641,'真好听',1000),
            ('main:35638:35640',35640,'贴贴',1700),
            ('main:35638:35639',35639,'摸摸头',1500)])
        self.assertEqual([(i.counts,i.allow_custom) for i in variants],
            [((1,),False),((1,),False),((1,10,100),True)])
        self.assertTrue(all(i.payment_scene=='default_gift' and not i.disabled_reason for i in variants))

    def test_upgrade_locks_missing_configs_and_child_permissions_block_send(self):
        data=upgrade_payload();row=data['gift_data']['room_gift_list']['gold_list'][0]
        row['upgrade_gift'][0].update(locked=True,lock_tip='尚未解锁')
        data['gift_config']['base_config']['list'][2]['perm']=0
        data['gift_config']['base_config']['list'].pop()
        parent=api.normalize_catalog(data,'90001').tabs[0].items[0]
        variants=getattr(parent,'variants',())
        self.assertEqual(len(variants),3)
        self.assertIn('尚未解锁',variants[0].disabled_reason)
        self.assertTrue(all(i.disabled_reason for i in variants))
        for item in variants:
            with self.subTest(gift=item.gift_id),self.assertRaises(api.GiftSendError):
                api.validate_send(item,1,cookies={'SESSDATA':'fake','bili_jct':'csrf'},wallet=99999)

    def test_upgrade_gifts_inherit_parent_restrictions_and_ignore_duplicate_refs(self):
        data=upgrade_payload();row=data['gift_data']['room_gift_list']['gold_list'][0]
        row['special'].update(is_use=0,tips='房间未解锁')
        row['upgrade_gift'] += [row['upgrade_gift'][0],{'gift_id':35638},{'gift_id':0}]
        variants=getattr(api.normalize_catalog(data,'90001').tabs[0].items[0],'variants',())
        self.assertEqual([i.gift_id for i in variants],[35641,35640,35639])
        self.assertTrue(all(i.disabled_reason=='房间未解锁' for i in variants))

    def test_direct_guard_and_unlocked_attire_use_their_server_payment_scene(self):
        for scene in ('guard_gift','guard_attire'):
            with self.subTest(scene=scene):
                item=api.normalize_catalog(guard_payload(scene=scene),'90001').tabs[0].items[0]
                self.assertEqual(item.disabled_reason,'')
                api.validate_send(item,1,cookies={'SESSDATA':'fake','bili_jct':'csrf'},wallet=19998000)
        locked=api.normalize_catalog(guard_payload(scene='guard_attire',unlocked=False),'90001').tabs[0].items[0]
        self.assertEqual(locked.disabled_reason,'请先开通大航海')

    def test_descriptive_paid_gift_rule_does_not_disable_an_ordinary_gift(self):
        data=room_payload();data['gift_config']['room_config'][0]['rule']='展示礼物特效'
        self.assertEqual(api.normalize_catalog(data,'90001').tabs[0].items[1].disabled_reason,'')

    def test_unimplemented_interactive_scenes_stay_visible_without_posting(self):
        for scene in ('guard_blind_box','blind_box','unknown'):
            with self.subTest(scene=scene):
                item=api.normalize_catalog(guard_payload(scene=scene),'90001').tabs[0].items[0]
                self.assertIn(scene,item.disabled_reason)
                with patch.object(api.requests,'post') as post,self.assertRaises(api.GiftSendError):
                    api.send_gift(api.GiftTarget('90001',123,456),item,1,
                        cookies={'SESSDATA':'fake','bili_jct':'csrf'},wallet=19998000,still_current=lambda:True)
                post.assert_not_called()

    def test_fan_club_light_sign_uses_ordinary_send_when_unlocked(self):
        for gift_id in (31164,34001):
            with self.subTest(gift_id=gift_id):
                data=room_payload()
                data['gift_config']['base_config']['list'].append(config(gift_id,name='粉丝团灯牌',price=100,type=0,gift_type=0))
                data['gift_data']['room_gift_list']['gold_list']=[{'gift_id':gift_id,
                    'special':{'special_type':3,'is_use':1},'gift_scene':{'pay_type':'send_gift'}}]
                item=api.normalize_catalog(data,'90001').tabs[0].items[0]
                self.assertEqual(item.disabled_reason,'')
                api.validate_send(item,1,cookies={'SESSDATA':'fake','bili_jct':'csrf'},wallet=100)

    def test_light_sign_exception_keeps_permissions_and_special_flow_restrictions(self):
        for changes in ({'special':{'special_type':3,'is_use':0,'tips':'locked'}},
                        {'gift_scene':{'pay_type':'buy_guard'}},{'gift_type':1},
                        {'interactive_pay':{'id':1}},{'gift_id':999}):
            with self.subTest(changes=changes):
                data=room_payload()
                data['gift_config']['base_config']['list'].append(config(31164,name='粉丝团灯牌',price=100))
                data['gift_data']['room_gift_list']['gold_list']=[dict({
                    'gift_id':31164,'special':{'special_type':3,'is_use':1},
                    'gift_scene':{'pay_type':'send_gift'}},**changes)]
                item=api.normalize_catalog(data,'90001').tabs[0].items[0]
                self.assertTrue(item.disabled_reason)

    def test_references_order_room_override_and_permissions(self):
        c=api.normalize_catalog(room_payload(),'90001')
        self.assertEqual([t.title for t in c.tabs],['礼物','Fans'])
        self.assertEqual([i.gift_id for i in c.tabs[0].items],[2,1])
        first=c.tabs[0].items[1]
        self.assertEqual((first.name,first.price,first.counts,first.max_count),('Room gift',105,(1,10,100),500))
        self.assertTrue(first.allow_custom)
        self.assertEqual(c.tabs[1].items[0].disabled_reason,'locked')
        self.assertNotIn(99,[i.gift_id for t in c.tabs for i in t.items])

    def test_counts_special_flows_and_image_fallback(self):
        data=room_payload()
        data['gift_config']['room_config']=[config(1,count_map=[{'num':n} for n in (1,1,-1,0,'bad',10,9999)],
            diy_count_map=0,gif='',interactive_pay={'foo':1})]
        item=api.normalize_catalog(data,'90001').tabs[0].items[1]
        self.assertEqual(item.counts,(1,10))
        self.assertFalse(item.allow_custom)
        self.assertTrue(item.disabled_reason)
        self.assertEqual(item.gif_url,'')
        self.assertEqual(item.image_url,'https://example.invalid/gift.png')

    def test_explicit_unavailable_counts_never_create_single_send(self):
        for counts in ([{'num':10}],[],[{'num':'invalid'}]):
            with self.subTest(counts=counts):
                data=room_payload()
                data['gift_config']['room_config']=[config(1,count_map=counts,max_send_limit=5,diy_count_map=0)]
                item=api.normalize_catalog(data,'90001').tabs[0].items[1]
                self.assertEqual(item.counts,())
                self.assertTrue(item.disabled_reason)
                with patch.object(api.requests,'post') as post,self.assertRaises(api.GiftSendError):
                    api.send_gift(api.GiftTarget('90001',123,456),item,1,
                        cookies={'SESSDATA':'fake','bili_jct':'csrf'},wallet=99999,still_current=lambda:True)
                post.assert_not_called()

    def test_permitted_custom_counts_and_single_bag_stock_remain_sendable(self):
        data=room_payload()
        data['gift_config']['room_config']=[config(1,count_map=[{'num':10}],max_send_limit=5,diy_count_map=1)]
        item=api.normalize_catalog(data,'90001').tabs[0].items[1]
        self.assertTrue(item.allow_custom)
        api.validate_send(item,1,cookies={'SESSDATA':'fake','bili_jct':'csrf'},wallet=99999)
        data=bag_payload()
        data['list']=[{'gift_id':1,'gift_num':1,'bag_id':701,'expire_at':0}]
        data['gift_config']=[config(1,count_map=[{'num':10}],diy_count_map=0)]
        item=api.normalize_bag(data,None,'90001',now=100).items[0]
        self.assertEqual(item.counts,(1,))
        self.assertFalse(item.disabled_reason)
        api.validate_send(item,1,cookies={'SESSDATA':'fake','bili_jct':'csrf'},wallet=None)

    def test_unknown_flow_types_never_post(self):
        for field in ('special_type','gift_type','type'):
            for value in ('unknown-flow',{},[],None,True,1.5):
                with self.subTest(field=field,value=value):
                    data=room_payload()
                    reference=data['gift_data']['room_gift_list']['gold_list'][0]
                    if field=='special_type':reference['special'][field]=value
                    else:reference[field]=value
                    item=api.normalize_catalog(data,'90001').tabs[0].items[1]
                    self.assertTrue(item.disabled_reason)
                    with patch.object(api.requests,'post') as post,self.assertRaises(api.GiftSendError):
                        api.send_gift(api.GiftTarget('90001',123,456),item,1,
                            cookies={'SESSDATA':'fake','bili_jct':'csrf'},wallet=99999,still_current=lambda:True)
                    post.assert_not_called()

    def test_integer_battery_and_unknown_wallet(self):
        self.assertEqual(api.format_battery(5200),'52')
        self.assertEqual(api.format_battery(105),'1.05')
        self.assertEqual(api.format_battery(0),'0')
        for data in ({},{'gold':None},{'gold':1.5},{'gold':-1}):
            with patch.object(api.requests,'get',return_value=Mock(status_code=200,json=lambda:{'code':0,'data':data})),self.assertRaises(ValueError):
                api.get_wallet(cookies={'SESSDATA':'fake'})
        with patch.object(api.requests,'get',return_value=Mock(status_code=200,json=lambda:{'code':0,'data':{'gold':0}})):
            self.assertEqual(api.get_wallet(cookies={'SESSDATA':'fake'}),0)

    def test_bag_batches_independent_without_catalog(self):
        bag=api.normalize_bag(bag_payload(),None,'90001',now=100)
        self.assertEqual([(i.bag_id,i.stock) for i in bag.items],[(701,10),(702,2)])
        self.assertTrue(all(not i.disabled_reason for i in bag.items))
        self.assertEqual(api.normalize_bag({'list':None,'gift_config':None},None,'90001',now=1).items,())
        data=bag_payload()
        data['list'][0]['expire_at']=99
        data['list'][1]['room_id']=888
        bag=api.normalize_bag(data,None,'90001',now=100)
        self.assertTrue(all(i.disabled_reason for i in bag.items))
        data=bag_payload();data['list'][0]['rule']='unknown'
        self.assertTrue(api.normalize_bag(data,None,'90001',now=100).items[0].disabled_reason)

    def test_malformed_catalog_is_not_empty_success(self):
        for data in (None,[],{}, {'gift_data':{},'gift_config':{}}, {'gift_data':{'room_gift_list':[]},'gift_config':{}}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                api.normalize_catalog(data,'90001')

    def test_reads_use_captured_cookies_and_timeout(self):
        cookies={'SESSDATA':'fake','bili_jct':'fake'}
        for method,payload in ((lambda:api.get_catalog('90001',cookies=cookies),room_payload()),
                               (lambda:api.get_wallet(cookies=cookies),{'gold':'105'}),
                               (lambda:api.get_bag('90001',None,cookies=cookies),bag_payload())):
            with patch.object(api.requests,'get',return_value=Mock(status_code=200,json=lambda:{'code':0,'data':payload})) as get:
                method()
                self.assertEqual(get.call_args.kwargs['cookies'],cookies)
                self.assertEqual(get.call_args.kwargs['timeout'],10)


class SendTests(unittest.TestCase):
    def test_gift_post_preserves_explicit_auth_status_for_relogin(self):
        for code,want in ((-101,True),(-111,True),(-400,False)):
            with self.subTest(code=code),patch.object(api.requests,'post',return_value=Mock(
                    status_code=200,json=lambda:{'code':code,'message':'opaque rejection'})):
                with self.assertRaises(api.GiftSendError) as caught:
                    api.send_gift(self.target,self.item,1,cookies=self.cookies,wallet=99999,still_current=lambda:True)
                self.assertEqual(getattr(caught.exception,'login_required',False),want)

    def test_guard_payment_posts_one_interactive_form_with_original_scene(self):
        import json
        for scene in ('guard_gift','guard_attire'):
            with self.subTest(scene=scene):
                item=api.normalize_catalog(guard_payload(scene=scene),'90001').tabs[0].items[0]
                with patch.object(api.requests,'get',return_value=Mock(status_code=200,
                        json=lambda:{'code':0,'data':{'addr':'203.0.113.1'}})) as get,patch.object(
                        api.requests,'post',return_value=Mock(status_code=200,json=lambda:{'code':0,'message':'ok'})) as post:
                    api.send_gift(self.target,item,1,cookies=self.cookies,wallet=19998000,still_current=lambda:True)
                self.assertEqual(post.call_count,1)
                self.assertEqual(post.call_args.args[0],api.BASE+'/xlive/revenue/v2/interactive_pay/send')
                form=post.call_args.kwargs['data']
                self.assertEqual(json.loads(form['gift_scene']),{'scene':scene,'pay_type':'interactive_pay','hide_price':False})
                for key,value in {'context_id':'90001','room_id':'90001','receiver_uid':456,'anchor_id':456,
                        'uid':123,'gift_id':item.gift_id,'gift_num':1,'price':item.price,
                        'context_type':1,'remote_ip':'203.0.113.1','csrf':'csrf-token','csrf_token':'csrf-token'}.items():
                    self.assertEqual(form[key],value)
                self.assertNotIn('bag_id',form);self.assertNotIn('coin_type',form)
                self.assertFalse(post.call_args.kwargs['allow_redirects'])
                self.assertEqual(get.call_args.kwargs['cookies'],self.cookies)

    def test_guard_context_change_during_ip_read_cancels_before_post(self):
        item=api.normalize_catalog(guard_payload(),'90001').tabs[0].items[0]
        current=[True]
        def read(*args,**kwargs):
            current[0]=False
            return Mock(status_code=200,json=lambda:{'code':0,'data':{'addr':''}})
        with patch.object(api.requests,'get',side_effect=read) as get,patch.object(api.requests,'post') as post:
            with self.assertRaises(api.GiftSendError):
                api.send_gift(self.target,item,1,cookies=self.cookies,wallet=19998000,still_current=lambda:current[0])
            self.assertEqual(get.call_count,1);post.assert_not_called()

    def test_guard_timeout_never_retries_or_falls_back_to_gold(self):
        item=api.normalize_catalog(guard_payload(),'90001').tabs[0].items[0]
        with patch.object(api.requests,'get',return_value=Mock(status_code=200,
                json=lambda:{'code':0,'data':{'addr':''}})),patch.object(api.requests,'post',side_effect=api.requests.Timeout()) as post:
            with self.assertRaises(api.GiftSendError) as caught:
                api.send_gift(self.target,item,1,cookies=self.cookies,wallet=19998000,still_current=lambda:True)
            self.assertTrue(caught.exception.uncertain)
            self.assertEqual(post.call_count,1)
            self.assertEqual(post.call_args.args[0],api.BASE+'/xlive/revenue/v2/interactive_pay/send')

    def test_zero_code_ok_is_success_and_nonzero_ok_remains_failure(self):
        for message in ('ok','OK','Ok'):
            with self.subTest(message=message),patch.object(api.requests,'post',return_value=Mock(
                    status_code=200,json=lambda:{'code':0,'message':message})):
                result=api.send_gift(self.target,self.item,1,cookies=self.cookies,wallet=9999,still_current=lambda:True)
                self.assertEqual((result.gift_key,result.quantity),(self.item.key,1))
        with patch.object(api.requests,'post',return_value=Mock(status_code=200,
                json=lambda:{'code':-1,'message':'ok'})),self.assertRaises(api.GiftSendError):
            api.send_gift(self.target,self.item,1,cookies=self.cookies,wallet=9999,still_current=lambda:True)

    def setUp(self):
        self.cookies={'SESSDATA':'fake','bili_jct':'csrf-token','buvid3':'device'}
        self.target=api.GiftTarget('90001',123,456)
        self.item=api.normalize_catalog(room_payload(),'90001').tabs[0].items[1]

    def test_prepare_target_distinguishes_sender_and_anchor(self):
        with patch.object(api,'resolve_room',return_value=('90001',456)),patch.object(api.requests,'get',return_value=Mock(status_code=200,json=lambda:{'code':0,'data':{'mid':123}})) as get:
            self.assertEqual(api.prepare_target('213',cookies=self.cookies),self.target)
        self.assertEqual(get.call_args.kwargs['cookies'],self.cookies)

    def test_form_uses_sender_anchor_real_room_and_csrf(self):
        import json,threading,requests
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        from urllib.parse import parse_qs
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(s):
                received.append((s.path,dict(s.headers),parse_qs(s.rfile.read(int(s.headers['Content-Length'])).decode())))
                body=b'{"code":0,"data":{}}'
                s.send_response(200);s.send_header('Content-Length',str(len(body)));s.end_headers();s.wfile.write(body)
            def log_message(*a):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        transport=requests.Session();transport.trust_env=False
        def post(url,**kw):
            self.assertEqual(url,'https://api.live.bilibili.com/xlive/revenue/v1/gift/sendGold')
            self.assertFalse(kw['allow_redirects']);self.assertEqual(kw['timeout'],10)
            return transport.post(f'http://127.0.0.1:{server.server_port}/gift/sendGold',**kw)
        try:
            with patch.object(api.requests,'post',side_effect=post):
                api.send_gift(self.target,self.item,10,cookies=self.cookies,wallet=99999,still_current=lambda:True)
        finally:
            transport.close();server.shutdown();server.server_close();thread.join(2)
        self.assertEqual(len(received),1)
        _,headers,form=received[0]
        for field,value in {'uid':'123','ruid':'456','biz_id':'90001','gift_id':'1','gift_num':'10','price':'105','csrf':'csrf-token','csrf_token':'csrf-token','coin_type':'gold','bag_id':'0'}.items():
            self.assertEqual(form[field],[value])
        self.assertEqual(headers['Origin'],'https://live.bilibili.com')
        self.assertEqual(headers['Referer'],'https://live.bilibili.com/90001')

    def test_bag_uses_selected_batch_without_paid_fallback(self):
        from dataclasses import replace
        item=api.normalize_bag(bag_payload(),None,'90001',now=100).items[1]
        item=replace(item,expires_at=int(time.time())+100)
        with patch.object(api.requests,'post',return_value=Mock(status_code=200,json=lambda:{'code':0})) as post:
            api.send_gift(self.target,item,1,cookies=self.cookies,wallet=None,still_current=lambda:True)
        self.assertEqual(post.call_count,1)
        self.assertEqual(post.call_args.args[0],'https://api.live.bilibili.com/xlive/revenue/v1/gift/sendBag')
        self.assertEqual(post.call_args.kwargs['data']['bag_id'],702)

    def test_invalid_quantity_funds_credentials_or_special_never_post(self):
        from dataclasses import replace
        with patch.object(api.requests,'post') as post:
            for item,count,wallet,cookies,current in ((self.item,0,9999,self.cookies,True),
                (self.item,1,None,self.cookies,True),(self.item,100,1,self.cookies,True),
                (self.item,1,9999,{},True),(replace(self.item,disabled_reason='locked'),1,9999,self.cookies,True),
                (replace(self.item,source='unsupported'),1,9999,self.cookies,True),
                (self.item,1,9999,self.cookies,False),(self.item,1.5,9999,self.cookies,True)):
                with self.assertRaises(api.GiftSendError):
                    api.send_gift(self.target,item,count,cookies=cookies,wallet=wallet,still_current=lambda:current)
        post.assert_not_called()

    def test_timeout_disconnect_and_bad_success_json_never_retry(self):
        import requests
        for error in (requests.Timeout(),requests.ConnectionError()):
            with patch.object(api.requests,'post',side_effect=error) as post,self.assertRaises(api.GiftSendError) as caught:
                api.send_gift(self.target,self.item,1,cookies=self.cookies,wallet=9999,still_current=lambda:True)
            self.assertTrue(caught.exception.uncertain);self.assertEqual(post.call_count,1)
        for payload,uncertain in (([],True),({'code':0,'message':'f'},False),({'code':-101,'message':'no'},False)):
            with patch.object(api.requests,'post',return_value=Mock(status_code=200,json=lambda:payload)) as post,self.assertRaises(api.GiftSendError) as caught:
                api.send_gift(self.target,self.item,1,cookies=self.cookies,wallet=9999,still_current=lambda:True)
            self.assertEqual(caught.exception.uncertain,uncertain);self.assertEqual(post.call_count,1)

if __name__=='__main__':unittest.main()
