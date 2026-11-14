"""Aggregate recorded results without dropping correctness failures."""
from collections import defaultdict
from math import ceil
from statistics import mean, median


def summarize(records, mode):
    grouped = defaultdict(list)
    for row in records:
        for source in (row['source'],'总体'):
            grouped[source,row['algorithm']].append(row)
    result = []
    for (source,algorithm),rows in sorted(grouped.items()):
        valid = [r for r in rows if r['ok']]
        measured = [r for r in rows if r.get('sent_bits') is not None]
        count = len(rows)
        transmitted = sum(r['sent_bits'] for r in measured)
        item = dict(mode=mode, source=source, algorithm=algorithm,samples=count,
                    success=len(valid), failures=count-len(valid), correctness=len(valid)/count,
                    sha_mismatch=sum(r['sha256_original']!=r['sha256_restored'] for r in rows),
                    bit_mismatch_samples=sum(r['bit_error_count']!=0 for r in rows),
                    bit_errors=sum(r['bit_error_count'] for r in rows),
                    average_original_bits=mean(r['original_bits'] for r in rows), measured=len(measured),
                    achieved=sum(5*r['sent_bits']<=4*r['original_bits'] for r in valid if r.get('sent_bits') is not None))
        item['hit_rate'] = item['achieved']/count
        if measured:
            average_ratio = mean(r['sent_bits']/r['original_bits'] for r in measured)
            item.update(average_sent_bits=transmitted/len(measured),
                        average_ratio=average_ratio,
                        total_ratio=transmitted/sum(r['original_bits'] for r in measured),
                        average_saving=1-average_ratio)
        for phase in ('encode','decode'):
            values=sorted(r[f'{phase}_ns']/1000 for r in rows if r.get(f'{phase}_ns') is not None)
            if values:
                item.update({f'{phase}_mean_us':mean(values),f'{phase}_p50_us':median(values),
                             f'{phase}_p95_us':values[ceil(.95*len(values))-1]})
        result.append(item)
    return result


def summarize_headers(records):
    groups = defaultdict(list)
    for row in records:
        # 32-bit intervals allow comparison without creating one row per exact layout.
        start = row['header_bits']//32*32
        groups[row['source'],row['algorithm'],start].append(row)
    result = []
    for (source,algorithm,start),rows in sorted(groups.items()):
        sizes = [r['sent_bits']/r['original_bits'] for r in rows if r.get('sent_bits') is not None]
        result.append(dict(source=source,algorithm=algorithm,header_bits_min=start,
                           header_bits_max=start+31,samples=len(rows),
                           average_ratio=mean(sizes) if sizes else None,
                           hit_rate=sum(r['ok'] and r.get('sent_bits') is not None and 5*r['sent_bits']<=4*r['original_bits']
                                        for r in rows)/len(rows)))
    return result
