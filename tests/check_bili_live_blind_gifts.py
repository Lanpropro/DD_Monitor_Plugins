"""Paid blind boxes use catalog identity and one official single-recipient form."""
from dataclasses import replace
import json
import unittest
from unittest.mock import Mock,patch
from bili_live_support import bili
from bili_live_fixtures import blind_payload
from ddm_plugin_bili_live import gift_api as api

ENDPOINT='https://api.live.bilibili.com/xlive/revenue/v2/gift/sendGoldMultiUser'

class BlindGiftTests(unittest.TestCase):
    def setUp(self):
        self.cookies={'SESSDATA':'fake','bili_jct':'csrf-token','buvid3':'device'}
        self.target=api.GiftTarget('90001',123,456)
        self.items=api.normalize_catalog(blind_payload(),'90001').tabs[0].items

    def test_public_blind_boxes_keep_their_own_prices_and_quantity_permissions(self):
        self.assertEqual([(i.gift_id,i.price) for i in self.items],[(32251,15000),(35206,5000)])
        for item in self.items:
            with self.subTest(gift=item.gift_id):
                self.assertFalse(item.disabled_reason)
                self.assertEqual(item.gift_type,6)
                self.assertEqual(item.counts,(1,10,50))
                self.assertFalse(item.allow_custom)
                self.assertEqual(item.max_count,5000)

    def test_permissions_room_restrictions_and_unknown_flows_still_block(self):
        cases=[({}, {'special':{'special_type':0,'is_use':0,'tips':'暂未开放'}}),
               ({'perm':0},{}),({'bind_roomid':90002},{}),({'coin_type':'silver'},{}),
               ({'gift_type':7},{}),({'type':1},{}),
               ({},{'special':{'special_type':3,'is_use':1}}),
               ({},{'gift_scene':{'scene':'guard_blind_box','pay_type':'interactive_pay'}}),
               ({},{'gift_scene':{'scene':'guard_attire','pay_type':'interactive_pay'}}),
               ({},{'gift_scene':{'scene':'unknown','pay_type':'send_gift'}})]
        for config,reference in cases:
            with self.subTest(config=config,reference=reference):
                data=blind_payload();data['gift_config']['base_config']['list'][0].update(config)
                data['gift_data']['room_gift_list']['gold_list'][0].update(reference)
                item=api.normalize_catalog(data,'90001').tabs[0].items[0]
                self.assertTrue(item.disabled_reason)
                with patch.object(api.requests,'post') as post,self.assertRaises(api.GiftSendError):
                    api.send_gift(self.target,item,1,cookies=self.cookies,wallet=999999,still_current=lambda:True)
                post.assert_not_called()

    def test_bag_blind_boxes_do_not_turn_into_paid_purchases(self):
        catalog=api.normalize_catalog(blind_payload(),'90001')
        item=api.normalize_bag({'list':[{'gift_id':32251,'bag_id':701,'gift_num':10}]},
            catalog,'90001',now=100).items[0]
        self.assertTrue(item.disabled_reason)
        with patch.object(api.requests,'post') as post,self.assertRaises(api.GiftSendError):
            api.send_gift(self.target,item,1,cookies=self.cookies,wallet=999999,still_current=lambda:True)
        post.assert_not_called()

    def test_sender_rechecks_blind_box_payment_route(self):
        item=self.items[0]
        alternatives=(replace(item,source='bag',bag_id=701,stock=10,disabled_reason=''),
            replace(item,pay_type='interactive_pay',gift_scene='{"scene":"guard_gift","pay_type":"interactive_pay"}',disabled_reason=''))
        for altered in alternatives:
            with self.subTest(source=altered.source,pay=altered.pay_type),patch.object(api.requests,'post') as post, self.assertRaises(api.GiftSendError):
                api.send_gift(self.target,altered,1,cookies=self.cookies,wallet=999999,still_current=lambda:True)
            post.assert_not_called()

    def test_only_one_form_is_sent_with_box_id_price_count_and_current_anchor(self):
        for item in self.items:
            with self.subTest(gift=item.gift_id):
                # Award IDs/values differ from the box being bought.
                payload={'code':0,'message':'ok','data':{'gift_list':[{'gift_id':32132,'price':2233000,'gift_num':1}]}}
                with patch.object(api.requests,'post',return_value=Mock(status_code=200,json=lambda:payload)) as post:
                    result=api.send_gift(self.target,item,10,cookies=self.cookies,wallet=999999,still_current=lambda:True)
                self.assertEqual(post.call_count,1)
                self.assertEqual(post.call_args.args[0],ENDPOINT)
                form=post.call_args.kwargs['data']
                for name,value in {'gift_id':item.gift_id,'price':item.price,'gift_num':10,
                        'uid':123,'ruid':456,'biz_id':'90001','send_ruid':0,'coin_type':'gold',
                        'bag_id':0,'all_flag':0,'platform':'pc','csrf':'csrf-token','csrf_token':'csrf-token','web_location':'444.8'}.items():
                    self.assertEqual(form[name],value)
                self.assertEqual(json.loads(form['receive_users']),[{'uid':456}])
                self.assertEqual(post.call_args.kwargs['cookies'],self.cookies)
                self.assertEqual(post.call_args.kwargs['timeout'],10)
                self.assertFalse(post.call_args.kwargs['allow_redirects'])
                self.assertEqual((result.gift_key,result.quantity,result.payload),(item.key,10,payload))

    def test_balance_quantity_credentials_and_stale_context_fail_before_post(self):
        cases=[(1,{},999999,True),(1,self.cookies,None,True),(10,self.cookies,149999,True),
               (2,self.cookies,999999,True),(1,self.cookies,999999,False)]
        for quantity,cookies,wallet,current in cases:
            with self.subTest(quantity=quantity,wallet=wallet,current=current),patch.object(api.requests,'post') as post,self.assertRaises(api.GiftSendError):
                api.send_gift(self.target,self.items[0],quantity,cookies=cookies,wallet=wallet,still_current=lambda:current)
            post.assert_not_called()

    def test_rejection_and_uncertain_response_do_not_retry_or_use_another_route(self):
        cases=[(Mock(status_code=200,json=lambda:{'code':-400,'message':'盲盒暂不可用'}),None,False),
               (None,api.requests.Timeout(),True)]
        for response,error,uncertain in cases:
            with self.subTest(uncertain=uncertain),patch.object(api.requests,'post',return_value=response,side_effect=error) as post:
                with self.assertRaises(api.GiftSendError) as caught:
                    api.send_gift(self.target,self.items[0],1,cookies=self.cookies,wallet=999999,still_current=lambda:True)
                self.assertEqual(caught.exception.uncertain,uncertain)
                self.assertEqual(post.call_count,1)
                self.assertEqual(post.call_args.args[0],ENDPOINT)

if __name__=='__main__':unittest.main()
