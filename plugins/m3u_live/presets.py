"""Built-in channels selected from public IPTV playlists."""
from .playlist import Channel


PRESETS = {
    # https://github.com/iptv-org/iptv/blob/master/streams/cn.m3u
    'm3u:preset:cctv1': Channel(
        'https://liveplay-srs.voc.com.cn/hls/tv/134_180adf.m3u8', 'CCTV-1 综合', '综合'),
    # The alternate URLs were checked against best-fan/iptv-sources at c5181316.
    'm3u:preset:cctv1-backup': Channel(
        'http://222.223.41.27:8888/hls/1/index.m3u8', 'CCTV-1 综合（备用）', '综合',
        headers={'User-Agent': 'Mozilla/5.0'}),
    # https://github.com/CCSH/IPTV/blob/main/live_lite.txt
    'm3u:preset:cctv5plus': Channel(
        'http://183.129.255.66:8480/hls/6/index.m3u8', 'CCTV-5+', '体育赛事'),
    'm3u:preset:cctv5plus-backup': Channel(
        'http://59.39.89.130:60901/tsfile/live/0016_1.m3u8', 'CCTV-5+（备用）', '体育赛事',
        headers={'User-Agent': 'Mozilla/5.0'}),
    'm3u:preset:cctv6': Channel(
        'http://183.129.255.66:8480/hls/7/index.m3u8', 'CCTV-6 电影', '电影'),
    'm3u:preset:cctv6-backup': Channel(
        'http://1.24.39.180:9003/hls/6/index.m3u8', 'CCTV-6 电影（备用）', '电影'),
    'm3u:preset:cctv13': Channel(
        'http://183.129.255.66:8480/hls/14/index.m3u8', 'CCTV-13 新闻', '新闻'),
}

# Keep resolving cards added from the previous preset menu.
PRESET_CHANNELS = PRESETS | {
    # https://github.com/iptv-org/iptv/blob/master/streams/cn.m3u
    'm3u:preset:cgtn': Channel(
        'https://english-livebkali.cgtn.com/live/encgtn.m3u8', 'CGTN 英语', '新闻'),
    'm3u:preset:guangzhou-tv': Channel(
        'https://tencentplaybusiness.gztv.com/live/zonghes.m3u8', '广州综合', '地方频道'),
    'm3u:preset:hebei-tv': Channel(
        'https://event.pull.hebtv.com/live/live101.m3u8', '河北卫视', '地方频道'),
}
