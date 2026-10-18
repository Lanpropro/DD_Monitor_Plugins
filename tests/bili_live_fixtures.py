"""Synthetic fixtures following the public roomGiftList schema."""
def config(gift_id=1, **changes):
    return dict({'id':gift_id,'name':'Gift','price':5200,'coin_type':'gold',
        'count_map':[{'num':n} for n in (1,10,100,520)], 'diy_count_map':1,
        'max_send_limit':1000,'gif':'https://example.invalid/gift.gif',
        'img_basic':'https://example.invalid/gift.png'},**changes)

def room_payload():
    return {'gift_data':{'room_gift_list':{'gold_list':[
        {'gift_id':1,'position':2,'special':{'is_use':1}},
        {'gift_id':2,'position':1,'special':{'is_use':1}}]},
        'tab_list':[{'tab_id':9,'tab_name':'Fans','position':1,'list':[
            {'gift_id':3,'position':1,'special':{'is_use':0,'tips':'locked'}}]}],
        'max_send_gift':500},
        'gift_config':{'base_config':{'list':[config(),config(2,name='Second'),config(3),config(99)]},
        'room_config':[{'id':1,'name':'Room gift','price':105}]}}

def bag_payload():
    return {'list':[{'gift_id':1,'gift_num':10,'bag_id':701,'expire_at':2000},
        {'gift_id':1,'gift_num':2,'bag_id':702,'expire_at':3000}],
        'gift_config':[config(1)]}
