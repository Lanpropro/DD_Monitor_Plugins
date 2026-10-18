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

if __name__=='__main__':unittest.main()
