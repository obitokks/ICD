"""Isolated end-to-end checks. Creates its own temporary source files and database."""
import json
from pathlib import Path
import tempfile
import threading

from app import create_app
from compression import codec, compress_sample, pack, split_bits, join_bits
from validation import verify
from source_data import drone_records


def run():
    raw = pack(('10110101' * 375))
    ranges = [(0,3),(21,9),(2999,1)]
    protected,payload = split_bits(raw,ranges)
    assert join_bits(protected,payload,ranges) == raw
    result = compress_sample(raw,8,8,*codec('zlib-1'))
    assert not result['fallback'] and verify(raw,result['recovered'])['ok']
    assert verify(b'\x00',b'\x01')['bit_errors'] == [7]
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        telemetry = root/'fixture.tlog'
        telemetry.write_bytes(bytes(8)+b'\xfe\x03'+bytes(4)+b'abc'+b'\x12\x34'+bytes(2))
        audit = []
        record, = drone_records(telemetry,'MAVLink',audit)
        assert record.header == b'\xfe\x03'+bytes(4) and record.payload == b'abc'
        assert record.trailer == b'\x12\x34' and audit[0][1]==19
        flash = root/'fixture.bin'
        flash.write_bytes(b'\xa3\x95\x80\x81\x06'+bytes(84)+b'\xa3\x95\x81abc')
        record, = drone_records(flash,'DataFlash',[])
        assert record.payload == b'abc' and record.start == 89
        source = root/'ROAD/road/ambient/fixture.log'
        source.parent.mkdir(parents=True)
        with source.open('wb') as f:
            for i in range(2400):
                f.write(f'({i:010d}.000001) can0 0F4#{i%32:016X}\n'.encode())
        db = root/'fixture.sqlite3'
        app = create_app(db,root)
        client = app.test_client()
        assert client.get('/').status_code == 200
        assert client.get('/static/lab.js').status_code == 200
        assert client.get('/api/runs').json == []
        conf = dict(sources=['ROAD'],algorithms=['zlib-1','zstd-dict'],mode='A',
                    layout='embedded',count=3,seed=15,dict_size=256,
                    dictionary_plan='once',dictionary_delivery_bits=16)
        lab = app.extensions['lab']
        from unittest.mock import patch
        import lab as lab_module
        original = lab_module.compress_sample
        entered, release = threading.Event(), threading.Event()
        calls = []
        def gate(*args,**kwargs):
            calls.append(1)
            if len(calls)==2:
                entered.set()
                if not release.wait(8):
                    raise RuntimeError('Fixture timed out')
            return original(*args,**kwargs)
        with patch.object(lab_module,'compress_sample',gate):
            response = client.post('/api/runs',json=conf)
            assert response.status_code == 202
            base = '/api/runs/'+response.json['id']
            try:
                assert entered.wait(12)
                state = client.get(base).json
                assert state['completed']==1 and state['status']=='running',state
                inspect = client.get(base+'/samples/1').json
                assert len(inspect['results'])==1 and inspect['results'][0]['ok']
                assert inspect['raw']['length']==4512 and inspect['provenance']['header_bits']==1504
                assert client.post(base+'/pause').status_code==202
            finally:
                release.set()
            lab.worker.join(12)
        assert client.get(base).json['status']=='paused'
        app = create_app(db,root)
        client = app.test_client()
        assert client.post(base+'/resume').status_code==202
        app.extensions['lab'].worker.join(12)
        state=client.get(base+'?summary=1').json
        assert state['completed']==6 and state['success']==6 and state['status']=='completed',state
        assert state['header_summary']
        assert client.get(base+'/samples/1').json['raw']==inspect['raw']
        assert len(client.get(base+'/samples/1').json['results'])==2
        assert client.get(base+'/export/csv').status_code==200
        assert client.get(base+'/export/json').json['config']['mode']=='A'
        assert len(client.get(base+'/export/jsonl').text.splitlines())==6
        assert len(client.get(base+'/samples').json)==3
        conf.update(mode='B',algorithms=['zlib-1','zlib-6','zstd-1','zstd-dict'],dictionary_plan='each',dictionary_delivery_bits=1024)
        path='/api/runs/'+client.post('/api/runs',json=conf).json['id']
        app.extensions['lab'].worker.join(12)
        state=client.get(path).json
        assert state['success']==12 and state['status']=='completed',state
        sample=client.get(path+'/samples/1').json
        assert sample['provenance']['header_bits']==0
        dictionary=next(r for r in sample['results'] if r['algorithm']=='zstd-dict')
        assert dictionary['payload_bits']==3008 and dictionary['ok']
        assert len(sample['provenance']['records'])==47
    print('Isolated fixture passed: A/B, live inspection, pause/restart/resume, exported summaries, bit boundaries.')


if __name__=='__main__':
    run()
