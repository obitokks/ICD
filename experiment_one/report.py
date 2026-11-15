from io import BytesIO
from pathlib import Path
import os
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

def make_report(entries):
    local_font = Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/simhei.ttf'
    font = 'ICD-Han' if local_font.is_file() else 'STSong-Light'
    if font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font,str(local_font)) if local_font.is_file() else UnicodeCIDFont(font))
    body = ParagraphStyle('body',fontName=font,fontSize=9,leading=14,wordWrap='CJK')
    title = ParagraphStyle('title',parent=body,fontSize=19,leading=26,spaceAfter=12)
    heading = ParagraphStyle('heading',parent=body,fontSize=12,leading=18,spaceBefore=10)
    output = BytesIO()
    story = []

    def para(value, style=body):
        return Paragraph(escape(str(value)),style)

    def table(rows, widths):
        cells = [[para(value) for value in row] for row in rows]
        grid = Table(cells,colWidths=widths,repeatRows=1,hAlign='LEFT')
        grid.setStyle(TableStyle([
            ('FONTNAME',(0,0),(-1,-1),font),
            ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#CFE5E8')),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#F0F6F7')]),
            ('VALIGN',(0,0),(-1,-1),'TOP'),
            ('BOTTOMPADDING',(0,0),(-1,-1),5),
        ]))
        story.extend([grid,Spacer(1,8)])

    def format_value(value):
        if isinstance(value,list):
            return ', '.join(map(str,value))
        return value if value is not None else '—'

    def percentage(row,key):
        return f'{row[key]:.2%}' if row.get(key) is not None else '—'

    story.append(para('ICD 比特流压缩实验汇总',title))
    story.append(para(f'共 {len(entries)} 次实验。逐块计算，发送长度不超过各样本原长的 80% 为达标。'))
    table([['实验 ID','模式','状态','已测试 / 计划']]+[
        [run['id'],run['config']['mode'],run['status'],f'{run["completed"]} / {run["total"]}']
        for run,_ in entries],[215,50,100,140])
    for index,(run,dictionaries) in enumerate(entries,1):
        story.extend([PageBreak(),para(f'实验 {index} · {run["description"]["title"]}',title),
                      para(run['id']),
                      para(f'{run["created"]}  /  {run["finished"] or "进行中"}  /  {run["status"]}'),
                      para('实验参数',heading)])
        table([['参数','设定']]+[[row['key'],format_value(row['value'])] for row in run['description']['parameters']],[134,371])
        story.append(para('各数据源平均原始长度',heading))
        table([['来源','已测试样本数','平均原始长度（bit）']]+[
            [r['source'],r['samples'],f'{r["average_original_bits"]:.2f}']
            for r in run['source_lengths']
        ],[165,135,205])
        story.append(para('按数据源与算法统计',heading))
        story.append(para('总压缩率是逐条计算「实际发送长度 ÷ 原始长度」后的算术平均。实际发送长度含模式标记及适用的首部、校验。大于 100% 表示平均膨胀。'))
        rows = run['summary']
        table([['来源','算法','样本 / 失败','平均发送 bit','总压缩率','达标率','正确率']]+[
            [r['source'],r['algorithm'],f'{r["samples"]} / {r["failures"]}',
             f'{r["average_sent_bits"]:.2f}' if r.get('average_sent_bits') is not None else '—',
             percentage(r,'average_ratio'),
             percentage(r,'hit_rate'),percentage(r,'correctness')] for r in rows
        ],[60,65,65,75,104,65,71])
        story.append(para('编解码耗时',heading))
        def timing(row,phase):
            return ' / '.join(f'{row[key]:.2f}' if row.get(key) is not None else '—'
                              for key in (f'{phase}_mean_us',f'{phase}_p50_us',f'{phase}_p95_us'))
        table([['来源','算法','编码均值 / P50 / P95（μs）','解码均值 / P50 / P95（μs）']]+[
            [r['source'],r['algorithm'],timing(r,'encode'),timing(r,'decode')] for r in rows
        ],[75,75,175,180])
        if run.get('header_summary'):
            story.append(para('首部长度分组',heading))
            table([['来源','算法','首部 bit 区间','样本','平均发送比','达标率']]+[
                [r['source'],r['algorithm'],f'{r["header_bits_min"]}–{r["header_bits_max"]}',
                 r['samples'],percentage(r,'average_ratio'),percentage(r,'hit_rate')]
                for r in run['header_summary']
            ],[80,80,100,65,90,90])
        if run.get('environment'):
            story.append(para('环境与资源',heading))
            table([['字段','记录']]+[[key,format_value(value)] for key,value in run['environment'].items()
                                    if key!='previous_sessions'],[165,340])
        if dictionaries:
            story.append(para('共享字典',heading))
            table([['来源','字节数','字典 ID','SHA-256']]+[
                [r['source'],r['bytes'],r['id'],r['sha256']] for r in dictionaries
            ],[71,60,79,295])
    def footer(canvas,doc):
        canvas.setFont(font,9)
        canvas.drawRightString(550,22,str(doc.page))
    SimpleDocTemplate(output,pagesize=(595,842),leftMargin=36,rightMargin=36,
                      topMargin=36,bottomMargin=40,title='ICD experiment report').build(
                          story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()
