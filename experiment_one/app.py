from pathlib import Path
import csv
import io
import os

from flask import Flask, Response, jsonify, render_template, request

from presentation import CODECS, SOURCES
from lab import Lab
from source_data import ROOT


def create_app(db=None, root=ROOT):
    app = Flask(__name__)
    lab = Lab(db or Path(__file__).parent/'runs/experiments.sqlite3',root)
    app.extensions['lab'] = lab

    @app.get('/')
    def index():
        return render_template('index.html',sources=SOURCES,algorithms=CODECS)

    @app.get('/api/runs')
    def history():
        return jsonify(lab.history())

    @app.post('/api/runs')
    def start():
        return jsonify(id=lab.start(request.get_json())),202

    @app.post('/api/runs/batch')
    def batch():
        return jsonify(lab.start_batch(request.get_json())),202

    @app.post('/api/runs/report')
    def report():
        from report import make_report
        selection = request.get_json()
        if not isinstance(selection,dict):
            raise ValueError('请选择要汇总的实验')
        ids = selection.get('ids')
        if ids is not None and (not isinstance(ids,list) or any(not isinstance(i,str) for i in ids)):
            raise ValueError('实验列表无效')
        available = {row['id']:row for row in lab.history()}
        chosen = list(reversed(available)) if ids is None else list(dict.fromkeys(ids))
        if any(i not in available for i in chosen):
            raise KeyError('run')
        entries = []
        for run in chosen:
            status = lab.status(run,True)
            if status['completed']:
                records = lab.records(run)
                entries.append((status,records['dictionaries']))
        if not entries:
            raise ValueError('所选实验还没有已提交的结果')
        return Response(make_report(entries),mimetype='application/pdf',
                        headers={'Content-Disposition':'attachment; filename=icd-experiment-report.pdf'})

    @app.get('/api/runs/<run>')
    def status(run):
        return jsonify(lab.status(run,request.args.get('summary')=='1'))

    @app.post('/api/runs/<run>/resume')
    def resume(run):
        return jsonify(id=lab.start(run=run)),202

    @app.post('/api/runs/<run>/pause')
    def pause(run):
        lab.pause(run)
        return jsonify(status='pausing'),202

    @app.get('/api/runs/<run>/samples/<int:sample>')
    def inspect(run,sample):
        return jsonify(lab.inspect(run,sample))

    @app.get('/api/runs/<run>/records')
    def records(run):
        return jsonify(lab.records(run))

    @app.get('/api/runs/<run>/samples')
    def sample_index(run):
        lab.status(run)
        with lab.connect() as conn:
            rows = conn.execute("SELECT id, json_extract(meta,'$.file') AS file FROM samples WHERE run=? ORDER BY id",(run,))
            return jsonify([dict(row) for row in rows])

    @app.get('/api/runs/<run>/export/<kind>')
    def export(run,kind):
        data = lab.status(run,True)
        if kind=='json':
            data.update(lab.records(run))
            response = jsonify(data)
        elif kind=='csv':
            rows = data['summary']
            output = io.StringIO()
            if rows:
                fields = list(dict.fromkeys(key for row in rows for key in row))
                writer = csv.DictWriter(output,fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            response = Response('\ufeff'+output.getvalue(),mimetype='text/csv')
        elif kind=='jsonl':
            with lab.connect() as conn:
                rows = conn.execute('SELECT data FROM results WHERE run=? ORDER BY sample,algorithm',(run,))
                body = ''.join(row['data']+'\n' for row in rows)
            response = Response(body,mimetype='application/x-ndjson')
        else:
            raise KeyError(kind)
        response.headers['Content-Disposition'] = f'attachment; filename=mode-{data["config"]["mode"]}-{data["id"]}.{kind}'
        return response

    @app.errorhandler(KeyError)
    def missing(error):
        return jsonify(error='未找到记录'),404

    @app.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)),400

    return app


if __name__=='__main__':
    import socket
    # Hold a separate loopback lock for this workspace's persistent writer.
    import hashlib
    lock_port = 20000 + int.from_bytes(hashlib.sha256(str(Path(__file__).resolve()).encode()).digest()[:2],'big')%20000
    lock = socket.socket()
    if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
        lock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
    try:
        lock.bind(('127.0.0.1',lock_port))
        lock.listen(1)
    except OSError as exc:
        raise SystemExit('该工作区的实验平台已在运行，或本地锁端口被占用。') from exc
    create_app().run(host='127.0.0.1',port=int(os.getenv('ICD_LAB_PORT','8765')),threaded=True)
