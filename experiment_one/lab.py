"""Persistent run queue. The worker is the sole writer of sample results."""
from contextlib import contextmanager
from datetime import datetime
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import threading
import traceback
import uuid
import zlib

import psutil
import zstandard as zstd

from compression import ALGORITHMS, codec, compress_sample, frame_overhead
from presentation import describe
from metrics import summarize, summarize_headers, summarize_source_lengths
from samples import MIN_PAYLOAD_BYTES, select
from source_data import ROOT, SOURCES
from validation import verify


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def json_text(value):
    return json.dumps(value,ensure_ascii=False)


class Lab:
    def __init__(self, database, root=ROOT):
        self.db = Path(database)
        self.db.parent.mkdir(parents=True,exist_ok=True)
        self.root = root
        self.lock = threading.Lock()
        self.worker = None
        self.active = None
        self.current = {}
        with self.connect() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, config TEXT NOT NULL, status TEXT NOT NULL,
                    created TEXT NOT NULL, finished TEXT, note TEXT, environment TEXT,
                    prepared INTEGER DEFAULT 0, audit TEXT);
                CREATE TABLE IF NOT EXISTS samples (
                    run TEXT, id INTEGER, raw BLOB, meta TEXT, PRIMARY KEY (run,id));
                CREATE TABLE IF NOT EXISTS dictionaries (
                    run TEXT, source TEXT, data BLOB, info TEXT, PRIMARY KEY (run,source));
                CREATE TABLE IF NOT EXISTS results (
                    run TEXT, sample INTEGER, algorithm TEXT, data TEXT, compressed BLOB,
                    restored BLOB, wire BLOB, decoded BLOB, PRIMARY KEY (run,sample,algorithm));
                CREATE TABLE IF NOT EXISTS batch_jobs (
                    run TEXT PRIMARY KEY, batch TEXT NOT NULL, position INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS logs (run TEXT, time TEXT, message TEXT);
            """)
            conn.execute("UPDATE runs SET status='interrupted', note='进程已退出，可恢复' WHERE status='running' OR (status='queued' AND id NOT IN (SELECT run FROM batch_jobs))")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db,timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def history(self):
        with self.connect() as conn:
            return [dict(r, description=describe(json.loads(r['config']))) for r in conn.execute(
                'SELECT runs.id,status,created,finished,note,config,batch_jobs.batch,batch_jobs.position '
                'FROM runs LEFT JOIN batch_jobs ON runs.id=batch_jobs.run ORDER BY created DESC,runs.rowid DESC')]

    def log(self, run, message):
        with self.connect() as conn:
            conn.execute('INSERT INTO logs VALUES (?,?,?)',(run,now(),message))
            conn.execute('UPDATE runs SET note=? WHERE id=?',(message,run))

    def start(self, config=None, run=None):
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise ValueError('已有实验在运行')
            with self.connect() as conn:
                if run is None:
                    config = validate_config(config)
                    run = uuid.uuid4().hex
                    conn.execute('INSERT INTO runs (id,config,status,created) VALUES (?,?,?,?)',
                                 (run,json_text(config),'queued',now()))
                else:
                    item = conn.execute('SELECT status FROM runs WHERE id=?',(run,)).fetchone()
                    if not item or item['status'] not in ('interrupted','failed','paused','queued'):
                        raise ValueError('该实验无法恢复')
                    conn.execute("UPDATE runs SET status='queued',finished=NULL WHERE id=?",(run,))
            self.launch(run)
            return run

    def launch(self, run):
        self.active = run
        self.current = {}
        self.pause_requested = threading.Event()
        self.worker = threading.Thread(target=self.drain,args=(run,),daemon=True,name='experiment-worker')
        self.worker.start()

    def start_batch(self, config):
        base = validate_config(config)
        batch = uuid.uuid4().hex
        ids = []
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise ValueError('已有实验在运行')
            with self.connect() as conn:
                for mode in ('A','B'):
                    run = uuid.uuid4().hex
                    conf = dict(base, mode=mode, sources=list(SOURCES), algorithms=list(ALGORITHMS))
                    conn.execute('INSERT INTO runs (id,config,status,created) VALUES (?,?,?,?)',
                                 (run,json_text(conf),'queued',now()))
                    ids.append(run)
                    conn.execute('INSERT INTO batch_jobs VALUES (?,?,?)',(run,batch,len(ids)))
            self.launch(ids[0])
        return dict(id=ids[0],batch=batch,runs=ids)

    def drain(self, run):
        while run:
            self.active = run
            self.current = {}
            self.execute(run)
            with self.connect() as conn:
                state = conn.execute('SELECT status FROM runs WHERE id=?',(run,)).fetchone()[0]
                if state == 'paused':
                    return
                following = conn.execute("""
                    SELECT next.run FROM batch_jobs AS current
                    JOIN batch_jobs AS next ON next.batch=current.batch
                    JOIN runs ON runs.id=next.run
                    WHERE current.run=? AND runs.status='queued'
                    ORDER BY next.position LIMIT 1
                """,(run,)).fetchone()
            run = following[0] if following else None

    def pause(self, run):
        with self.lock:
            if self.active != run or not self.worker or not self.worker.is_alive():
                raise ValueError('该实验当前未运行')
            self.pause_requested.set()

    def execute(self, run):
        try:
            self.run(run)
        except Exception:
            message = traceback.format_exc()
            self.log(run,message)
            self.finish(run,'failed')

    def finish(self, run, state):
        with self.connect() as conn:
            conn.execute('UPDATE runs SET status=?,finished=? WHERE id=?',(state,now(),run))
        self.current = {}

    def run(self, run):
        process = psutil.Process()
        memory = process.memory_info()
        env = dict(cpu=platform.processor(), logical_cores=os.cpu_count(), physical_cores=psutil.cpu_count(logical=False),
                   memory_total_bytes=psutil.virtual_memory().total,
                   process_peak_bytes=getattr(memory,'peak_wset',memory.rss),
                   observed_process_threads=process.num_threads(), worker_threads=1,
                   os=platform.platform(),python=platform.python_version(),
                   zlib=zlib.ZLIB_RUNTIME_VERSION,zstandard=zstd.__version__,zstd_backend=zstd.ZSTD_VERSION,
                   codec_threads=1,gpu=False, resumed_or_started=now())
        with self.connect() as conn:
            row = conn.execute('SELECT * FROM runs WHERE id=?',(run,)).fetchone()
            config = json.loads(row['config'])
            if row['environment']:
                old = json.loads(row['environment'])
                env['previous_sessions'] = [*old.pop('previous_sessions',[]),old]
            conn.execute("UPDATE runs SET status='running',environment=? WHERE id=?",(json_text(env),run))
        self.log(run,'恢复已冻结的样本计划' if row['prepared'] else '生成训练与测试计划')
        if not row['prepared']:
            train, test, audit = select(self.root,config,lambda msg:self.log(run,msg))
            if self.pause_requested.is_set():
                self.log(run,'准备阶段已停止，恢复时重新生成确定性计划')
                self.finish(run,'paused')
                return
            dictionaries = []
            for source, items in train.items():
                if 'zstd-dict' not in config['algorithms']:
                    continue
                if len(items) < 8:
                    raise ValueError(f'{source}: dictionary needs more training samples')
                trained = zstd.train_dictionary(config['dict_size'],[data for data,_ in items])
                raw = trained.as_bytes()
                info = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                            version='sha256:'+hashlib.sha256(raw).hexdigest(),
                            method='zstandard.train_dictionary', id=trained.dict_id(),
                            deployment='preinstalled', training=[meta for _,meta in items])
                dictionaries.append((run,source,raw,json_text(info)))
            with self.connect() as conn:
                conn.executemany('INSERT INTO samples VALUES (?,?,?,?)',
                    ((run,i,raw,json_text(meta)) for i,(raw,meta) in enumerate(test,1)))
                conn.executemany('INSERT INTO dictionaries VALUES (?,?,?,?)',dictionaries)
                conn.execute('UPDATE runs SET prepared=1,audit=? WHERE id=?',(json_text(audit),run))
            self.log(run,f'已冻结 {len(test)} 个样本及训练字典')
        with self.connect() as conn:
            samples = conn.execute('SELECT * FROM samples WHERE run=? ORDER BY id',(run,)).fetchall()
            dictionaries = {r['source']:(r['data'],json.loads(r['info'])) for r in conn.execute(
                'SELECT * FROM dictionaries WHERE run=?',(run,))}
            complete = {(r['sample'],r['algorithm']) for r in conn.execute(
                'SELECT sample,algorithm FROM results WHERE run=?',(run,))}
        instances = {(source,name):codec(name,dictionaries[source][0] if name=='zstd-dict' else None)
                     for source in config['sources'] for name in config['algorithms']}
        for sample in samples:
            raw, meta, sid = sample['raw'],json.loads(sample['meta']),sample['id']
            source = meta['source']
            for name in config['algorithms']:
                if (sid,name) in complete:
                    continue
                if self.pause_requested.is_set():
                    self.log(run,'已暂停，已提交的样本可继续抽查')
                    self.update_environment(run,process,env)
                    self.finish(run,'paused')
                    return
                self.current = dict(sample=sid,algorithm=name,source=source)
                info = dictionaries[source][1] if name=='zstd-dict' else {}
                blob = dict(compressed=b'',recovered=b'',wire=b'',decoded=b'')
                try:
                    blob = compress_sample(raw,meta['header_bits'],meta['trailer_bits'],*instances[source,name])
                except Exception as exc:
                    blob['error'] = f'{type(exc).__name__}: {exc}'
                check = verify(raw,blob['recovered'],blob.get('error',''))
                base = len(blob['wire'])*8 if blob['wire'] else None
                data = {k:v for k,v in blob.items() if not isinstance(v,bytes)}
                data.update(check, experiment_id=run, sample_id=sid, source=source, algorithm=name, mode=config['mode'],
                            source_file=meta['file'], source_sha256=meta['sha256'],
                            source_start=meta['start'], source_end=meta['end'],
                            header_bits=meta['header_bits'],trailer_bits=meta['trailer_bits'],
                            payload_bits=len(raw)*8-meta['header_bits']-meta['trailer_bits'],
                            compressed_bits=len(blob['compressed'])*8, wire_bits=base,
                            codec_frame_overhead_bits=frame_overhead(name,blob['compressed']),
                            payload_pack_padding_bits=(-(len(raw)*8-meta['header_bits']-meta['trailer_bits']))%8,
                            parameters=ALGORITHMS[name],
                            compression_ratio= base/(len(raw)*8) if base is not None else None,
                            sent_bits=base,
                            dictionary_charged_bits=0,dictionary_sha256=info.get('sha256'),
                            dictionary_id=info.get('id'),dictionary_plan='preinstalled' if info else 'none')
                with self.connect() as conn:
                    conn.execute('INSERT INTO results VALUES (?,?,?,?,?,?,?,?)',
                                 (run,sid,name,json_text(data),blob['compressed'],
                                  blob['recovered'],blob['wire'],blob['decoded']))
                if not check['ok']:
                    self.log(run,f'样本 {sid} / {name}: {check["error"]}')
        self.update_environment(run,process,env)
        self.log(run,'测试完成')
        self.finish(run,'completed')

    def update_environment(self, run, process, env):
        memory = process.memory_info()
        env['process_peak_bytes'] = getattr(memory,'peak_wset',memory.rss)
        with self.connect() as conn:
            conn.execute('UPDATE runs SET environment=? WHERE id=?',(json_text(env),run))

    def status(self, run, include_summary=False):
        with self.connect() as conn:
            conn.execute('BEGIN')
            row = conn.execute('SELECT * FROM runs WHERE id=?',(run,)).fetchone()
            if row is None:
                raise KeyError(run)
            data = dict(row)
            batch = conn.execute('SELECT batch,position FROM batch_jobs WHERE run=?',(run,)).fetchone()
            if batch:
                jobs = conn.execute("""
                    SELECT runs.id,runs.status FROM batch_jobs JOIN runs ON runs.id=batch_jobs.run
                    WHERE batch=? ORDER BY position
                """,(batch['batch'],)).fetchall()
                data['batch'] = dict(batch, jobs=[dict(job) for job in jobs])
            data['experiment_id'] = run
            for key in ('config','environment','audit'):
                data[key] = json.loads(data[key]) if data[key] else None
            totals = conn.execute("""
                SELECT COUNT(*) AS completed,
                       COALESCE(SUM(json_extract(data,'$.ok')),0) AS success,
                       AVG(1.0*json_extract(data,'$.sent_bits')/json_extract(data,'$.original_bits')) AS average_ratio,
                       COUNT(CASE WHEN json_extract(data,'$.ok')=1
                                  AND 5*json_extract(data,'$.sent_bits')<=4*json_extract(data,'$.original_bits') THEN 1 END) AS achieved
                FROM results WHERE run=?
            """,(run,)).fetchone()
            latest = conn.execute('SELECT data FROM results WHERE run=? ORDER BY rowid DESC LIMIT 1',(run,)).fetchone()
            if include_summary:
                rows = [json.loads(r[0]) for r in conn.execute('SELECT data FROM results WHERE run=?',(run,))]
        data.update(dict(totals))
        conf = data['config']
        data['description'] = describe(conf)
        data['total'] = conf['count']*len(conf['sources'])*len(conf['algorithms'])
        data['failures'] = data['completed']-data['success']
        data['current'] = self.current if self.active == run else {}
        data['latest'] = json.loads(latest[0]) if latest else None
        if include_summary:
            data['summary'] = summarize(rows,conf['mode'])
            data['source_lengths'] = summarize_source_lengths(rows)
            data['header_summary'] = summarize_headers(rows) if conf['mode']=='A' else []
        return data

    def inspect(self, run, sample):
        from compression import bits
        with self.connect() as conn:
            row = conn.execute('SELECT * FROM samples WHERE run=? AND id=?',(run,sample)).fetchone()
            if not row:
                raise KeyError(sample)
            records = conn.execute('SELECT * FROM results WHERE run=? AND sample=? ORDER BY algorithm',(run,sample)).fetchall()
        def show(value):
            return dict(hex=value.hex(' '),bits=bits(value),length=len(value)*8)
        results = []
        for r in records:
            data = json.loads(r['data'])
            data.update({key:show(r[key]) for key in ('compressed','restored','wire','decoded')})
            results.append(data)
        return dict(id=sample, raw=show(row['raw']), provenance=json.loads(row['meta']),results=results)

    def delete(self, run):
        with self.lock, self.connect() as conn:
            row = conn.execute('SELECT status FROM runs WHERE id=?',(run,)).fetchone()
            if row is None:
                raise KeyError(run)
            if row['status'] in ('queued','running') or (
                self.active == run and self.worker and self.worker.is_alive()
            ):
                raise ValueError('实验仍在运行或等待执行，暂不能删除')
            conn.execute('DELETE FROM results WHERE run=?',(run,))
            conn.execute('DELETE FROM samples WHERE run=?',(run,))
            conn.execute('DELETE FROM dictionaries WHERE run=?',(run,))
            conn.execute('DELETE FROM logs WHERE run=?',(run,))
            conn.execute('DELETE FROM batch_jobs WHERE run=?',(run,))
            conn.execute('DELETE FROM runs WHERE id=?',(run,))

    def records(self, run):
        with self.connect() as conn:
            logs = [dict(r) for r in conn.execute('SELECT time,message FROM logs WHERE run=? ORDER BY rowid',(run,))]
            dictionaries = [dict(source=r['source'],**json.loads(r['info'])) for r in conn.execute(
                'SELECT source,info FROM dictionaries WHERE run=?',(run,))]
        return dict(logs=logs,dictionaries=dictionaries)


def validate_config(conf):
    if not isinstance(conf,dict):
        raise ValueError('Expected a JSON configuration object')
    config = dict(conf)
    for key, choices in [('sources',SOURCES),('algorithms',ALGORITHMS)]:
        values = config.get(key)
        if not isinstance(values,list) or not values or len(set(values))!=len(values) or any(v not in choices for v in values):
            raise ValueError(f'Invalid {key}')
    if config.get('mode') not in ('A','B'):
        raise ValueError('Invalid mode')
    config.pop('layout',None)
    config.pop('dictionary_delivery_bits',None)
    config['dictionary_plan'] = 'preinstalled'
    config['minimum_payload_bytes'] = MIN_PAYLOAD_BYTES
    for key, low, high in [('count',1,10000),('seed',0,2**53-1),('dict_size',256,65536)]:
        if type(config.get(key)) is not int or not low <= config[key] <= high:
            raise ValueError(f'Invalid {key}: {low}..{high}')
    return config

