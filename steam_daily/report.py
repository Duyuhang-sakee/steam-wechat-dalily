import html
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8), 'Asia/Shanghai')


def display_time(epoch):
    return datetime.fromtimestamp(epoch, BEIJING).strftime('%Y-%m-%d %H:%M') if epoch else '未知'


def row(offer, now):
    escape = html.escape
    due = ''
    if offer.free and offer.end_epoch and 0 < offer.end_epoch - now.timestamp() <= 86400:
        due = ' <strong>【未来24小时内结束】</strong>'
    note = ''
    if offer.deadline_note and not offer.end_epoch:
        note = f'<br>商店原文：{escape(offer.deadline_note)}（原文时区未核实，不换算）'
    return (
        f'<p><a href="{escape(offer.url, quote=True)}">{escape(offer.name)}</a>{due}<br>'
        f'{escape(offer.original)} → {escape(offer.current)} · -{offer.discount}%<br>'
        f'活动开始：{display_time(offer.start_epoch)}；结束：{display_time(offer.end_epoch)}{note}</p>'
    )


def build_pages(freebies, discounts, now, config, stats, warnings):
    stamp = now.astimezone(BEIJING).strftime('%Y-%m-%d %H:%M')
    header = (
        f'<h2>Steam 优惠晨报</h2><p>数据采集：{stamp}（北京时间） · 地区：{html.escape(config["country"])}<br>'
        f'限免：{len(freebies)} 项；折扣≥{config["minimum_discount"]}%：{len(discounts)} 项。'
        f'已遍历 {stats["pages"]} 页、{stats["total"]} 条公开优惠搜索结果。</p>'
    )
    footer = (
        '<hr><p>每天09:30检查一次；只反映本次取得的公开数据。价格和可领取状态以Steam商品页为准。'
        '时区未确认或来源未提供的活动时间显示未知；短活动可能在两次检查之间错过。'
        '包含DLC及礼包；有些商品需要拥有本体。长名单分多条消息，不截断。</p>'
    )
    blocks = ['<h3>限时永久领取</h3>']
    blocks += [row(item, now) for item in freebies] or ['<p>本次没有核验到有效限免。</p>']
    blocks += [f'<h3>折扣≥{config["minimum_discount"]}%</h3>']
    blocks += [row(item, now) for item in discounts] or ['<p>本次没有符合条件的打折商品。</p>']
    if warnings:
        blocks.append('<h3>数据核验提示</h3>')
        blocks += [f'<p>{html.escape(warning)}</p>' for warning in warnings]
    bodies, current = [], ''
    allowance = config['message_char_limit'] - len(header) - len(footer) - 100
    for block in blocks:
        if len(block) > allowance:
            raise ValueError('One item exceeded the message length budget')
        if current and len(current) + len(block) > allowance:
            bodies.append(current)
            current = ''
        current += block
    if current:
        bodies.append(current)
    return [header + f'<p>第 {i}/{len(bodies)} 页</p>' + body + footer for i, body in enumerate(bodies, 1)]
