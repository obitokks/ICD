"""Chinese experiment labels shared by the web interface and PDF."""
FIELDS = {
    'mode': '测试模式', 'layout': '首部边界表传输方式', 'dictionary_plan': '字典部署',
    'sources': '数据来源', 'algorithms': '压缩算法', 'count': '每来源样本数',
    'seed': '随机种子', 'dict_size': '字典目标大小（字节）',
    'dictionary_delivery_bits': '每次字典分发附加开销（bit）',
}
MODES = {'A': '模式 A（按原始报文长度，保留首部）', 'B': '模式 B（按原始载荷长度）'}
LAYOUTS = {'embedded': '边界表随压缩块发送并计费', 'shared': '收发双方已共享边界表，块内无需发送'}
DEPLOYMENTS = {'preinstalled': '收发两端预置字典', 'once': '通过链路首次分发字典', 'each': '每个使用字典的压缩块随附字典'}
SOURCES = {'ROAD':'ROAD 汽车 CAN','MAVLink':'MAVLink 无人机通信','DataFlash':'DataFlash 飞行日志'}
CODECS = {'zlib-1':'zlib 级别 1','zlib-6':'zlib 级别 6','zstd-1':'Zstandard 级别 1（无字典）',
          'zstd-dict':'Zstandard 级别 1（共享字典）'}


def describe(config):
    with_dict = 'zstd-dict' in config.get('algorithms', [])
    layout = LAYOUTS.get(config.get('layout'),'未记录') if config.get('mode') == 'A' else '不适用（纯载荷）'
    deployment = DEPLOYMENTS.get(config.get('dictionary_plan'),'未记录') if with_dict else '未使用字典'
    mode = MODES.get(config.get('mode'),'未记录')
    values = dict(config, mode=mode,layout=layout,dictionary_plan=deployment,
                  sources='、'.join(SOURCES.get(x,x) for x in config.get('sources',[])),
                  algorithms='、'.join(CODECS.get(x,x) for x in config.get('algorithms',[])))
    for key in ('layout','dictionary_delivery_bits'):
        if key not in config:
            values.pop(key,None)
    if not with_dict:
        for key in ('dict_size','dictionary_delivery_bits'):
            if key in values:
                values[key] = '不适用'
    params = [dict(key=FIELDS.get(key,key),value=values[key])
              for key in dict.fromkeys([*FIELDS,*config]) if key in values]
    title = ' · '.join([mode,*([layout] if 'layout' in config and config.get('mode')=='A' else []),deployment])
    details = f'{values["sources"]} ｜ {values["algorithms"]} ｜ 每源 {config.get("count","—")} 条 ｜ 种子 {config.get("seed","—")}'
    if with_dict:
        details += f' ｜ 字典 {config.get("dict_size","—")} B'
    return dict(title=title,details=details,parameters=params)
