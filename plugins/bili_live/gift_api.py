"""Immutable gift models, conservative room/bag normalization and HTTP reads."""
from copy import deepcopy
from dataclasses import dataclass
import time
from types import MappingProxyType
import requests
from .bili_api import HEADERS, api_result, resolve_room

BASE = 'https://api.live.bilibili.com'
GOLD_ENDPOINT = BASE + '/xlive/revenue/v1/gift/sendGold'
BAG_ENDPOINT = BASE + '/xlive/revenue/v1/gift/sendBag'

@dataclass(frozen=True)
class GiftItem:
    key: str
    source: str
    gift_id: int
    name: str
    price: int
    gif_url: str
    image_url: str
    counts: tuple
    max_count: int
    allow_custom: bool
    disabled_reason: str
    bag_id: int = 0
    stock: int = 0
    expires_at: int = 0

@dataclass(frozen=True)
class GiftTab:
    key: str
    title: str
    items: tuple

@dataclass(frozen=True)
class GiftCatalog:
    tabs: tuple
    configs: object
    max_send_count: int

@dataclass(frozen=True)
class GiftTarget:
    room_id: str
    uid: int
    ruid: int

@dataclass(frozen=True)
class GiftResult:
    gift_key: str
    quantity: int
    payload: object

class GiftSendError(ValueError):
    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain

def _integer(value, default=None):
    if isinstance(value, bool) or not (isinstance(value,int) or isinstance(value,str) and value.isascii() and value.isdigit()):
        if default is not None:return default
        raise ValueError('礼物数值格式异常')
    return int(value)

def format_battery(raw):
    raw=_integer(raw)
    if raw<0:raise ValueError('余额不能为负数')
    whole, fraction=divmod(raw,100)
    return str(whole) if not fraction else f'{whole}.{fraction:02d}'.rstrip('0')

def _list(value):
    if value is None:return []
    if not isinstance(value,list) or any(not isinstance(x,dict) for x in value):
        raise ValueError('礼物列表格式异常')
    return value

def _configs(value):
    if isinstance(value,dict):value=value.get('list')
    return _list(value)

def _url(value):
    return value if isinstance(value,str) and value.startswith(('https://','http://')) else ''

def _reason(config, room_id):
    special=config.get('special') or {}
    if not isinstance(special,dict):return '未知礼物权限'
    if special.get('is_use',1) != 1:return str(special.get('tips') or '当前账号尚未解锁')
    if config.get('perm',1) != 1 or config.get('is_use',1) != 1:return '当前账号没有赠送权限'
    scene=config.get('gift_scene') or {}
    if not isinstance(scene,dict) or scene.get('pay_type','send_gift') != 'send_gift':return '暂不支持此购买流程'
    if (config.get('interactive_pay') or _integer(special.get('special_type',0),0) or
            _integer(config.get('gift_type',0),0) or _integer(config.get('type',0),0)):
        return '暂不支持特殊礼物流程'
    bound=config.get('bind_roomid') or config.get('room_id') or 0
    if str(bound) not in ('0','',str(room_id)):return '仅限指定直播间'
    rule=config.get('rule')
    if rule not in (None,'',0,{},[]):return '未知背包使用规则'
    return ''

def _item(config, key, room_id, max_count, *, source='gold', bag_id=0, stock=0, expires_at=0, now=0):
    gift_id=_integer(config.get('id',config.get('gift_id')),0)
    limit=_integer(config.get('max_send_limit',max_count),max_count)
    maximum=min(max_count,limit) if limit>0 else max_count
    if source=='bag':maximum=min(maximum,stock)
    counts=[]
    values=config.get('count_map')
    if values is None:values=[{'num':1}]
    if isinstance(values,dict):values=[{'num':k} for k in values]
    for value in values if isinstance(values,list) else []:
        count=_integer(value.get('num') if isinstance(value,dict) else value,0)
        if 0<count<=maximum and count not in counts:counts.append(count)
    reason=_reason(config,room_id)
    price=_integer(config.get('price'),-1)
    if not reason and (gift_id<=0 or price<0):reason='礼物资料不完整'
    if source=='gold' and config.get('coin_type')!='gold':
        source='unsupported';reason=reason or '暂不支持此支付方式'
    if source=='bag':
        if bag_id<=0 or stock<=0:reason=reason or '背包库存不足'
        if expires_at and expires_at<=now:reason=reason or '礼物已过期'
    if not counts and maximum>0:counts=[1]
    return GiftItem(key,source,gift_id,str(config.get('name') or f'礼物 {gift_id}'),max(0,price),
        _url(config.get('gif')),_url(config.get('img_basic')) or _url(config.get('img_dynamic')),
        tuple(counts),maximum,config.get('diy_count_map') in (1,True) and maximum>1,reason,
        bag_id,stock,expires_at)

def normalize_catalog(data, room_id):
    if not isinstance(data,dict) or not isinstance(data.get('gift_data'),dict) or not isinstance(data.get('gift_config'),dict):
        raise ValueError('礼物列表格式异常')
    gifts=data['gift_data']; refs=gifts.get('room_gift_list')
    if not isinstance(refs,dict):raise ValueError('房间礼物列表格式异常')
    configs={}
    for row in (*_configs(data['gift_config'].get('base_config')), *_configs(data['gift_config'].get('room_config'))):
        ident=_integer(row.get('id',row.get('gift_id')),0)
        if ident>0:configs[ident]={**configs.get(ident,{}),**deepcopy(row)}
    maximum=_integer(gifts.get('max_send_gift'),5000)
    if maximum<=0:raise ValueError('房间赠送上限异常')
    groups=[('main','礼物',_list(refs.get('gold_list'))+_list(refs.get('silver_list')))]
    tabs=sorted(_list(gifts.get('tab_list')),key=lambda r:_integer(r.get('position'),0))
    for tab in tabs:groups.append((str(tab.get('tab_id')),str(tab.get('tab_name') or '礼物'),_list(tab.get('list'))))
    result=[]
    for key,title,rows in groups:
        items=[];seen=set()
        for row in sorted(rows,key=lambda r:_integer(r.get('position'),0)):
            ident=_integer(row.get('gift_id',row.get('id')),0)
            if ident<=0 or ident in seen:continue
            seen.add(ident)
            merged={**configs.get(ident,{}),**row,'id':ident}
            items.append(_item(merged,f'{key}:{ident}',room_id,maximum))
        result.append(GiftTab(key,title,tuple(items)))
    return GiftCatalog(tuple(result),MappingProxyType(configs),maximum)

def normalize_bag(data, catalog, room_id, *, now):
    if not isinstance(data,dict):raise ValueError('背包列表格式异常')
    configs=dict(catalog.configs) if catalog else {}
    for row in _configs(data.get('gift_config')):
        ident=_integer(row.get('id',row.get('gift_id')),0)
        configs[ident]={**configs.get(ident,{}),**row}
    items=[];seen=set()
    for row in _list(data.get('list')):
        bag_id=_integer(row.get('bag_id'),0)
        if bag_id in seen:continue
        seen.add(bag_id)
        ident=_integer(row.get('gift_id'),0)
        merged={**configs.get(ident,{}),**row,'id':ident}
        stock=_integer(row.get('gift_num',row.get('num')),0)
        expiry=_integer(row.get('expire_at',row.get('expire_time',0)),0)
        items.append(_item(merged,f'bag:{bag_id}',room_id,catalog.max_send_count if catalog else 5000,
            source='bag',bag_id=bag_id,stock=stock,expires_at=expiry,now=now))
    return GiftTab('bag','背包',tuple(items))

def _read(path, cookies, **params):
    response=requests.get(BASE+path,params=params,headers=HEADERS,cookies=dict(cookies),timeout=10)
    data=api_result(response).get('data')
    if not isinstance(data,dict):raise ValueError('礼物接口返回格式异常')
    return data

def get_catalog(room_id, *, cookies):
    return normalize_catalog(_read('/xlive/web-room/v1/giftPanel/roomGiftList',cookies,
        room_id=str(room_id),platform='pc',source='live'),room_id)

def get_wallet(*, cookies):
    raw=_integer(_read('/pay/v2/Pay/myWallet',cookies).get('gold'))
    if raw<0:raise ValueError('余额格式异常')
    return raw

def get_bag(room_id, catalog, *, cookies):
    return normalize_bag(_read('/xlive/web-room/v1/gift/bag_list',cookies,room_id=str(room_id)),
        catalog,room_id,now=int(time.time()))
