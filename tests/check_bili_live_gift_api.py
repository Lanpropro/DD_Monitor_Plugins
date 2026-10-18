"""Catalog references, batch identity, integer money and captured read requests."""
import time
import unittest
from unittest.mock import Mock, patch
from bili_live_support import bili
from bili_live_fixtures import config,room_payload,bag_payload
from ddm_plugin_bili_live import gift_api as api

class ReadTests(unittest.TestCase):
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
