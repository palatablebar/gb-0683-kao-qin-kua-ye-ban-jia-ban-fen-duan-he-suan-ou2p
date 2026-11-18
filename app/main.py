import importlib,os
from flask import Flask,jsonify,request,redirect,Response,render_template
from app.config import MODULES,TITLE,SHORT
from app.errors import DomainError
from app.workflow import run,load
from app import store,ledger
from app.web import payload
from app.modules.m7_archive.export_out import bundle,period_bundle

def create_app():
    app=Flask(__name__);app.config['MAX_CONTENT_LENGTH']=2_000_000
    for entry in MODULES:app.register_blueprint(importlib.import_module('app.modules.'+entry[0]+'.handler').bp)
    @app.errorhandler(DomainError)
    def domain_error(error):return jsonify(code=error.code,message=error.message),error.status
    @app.errorhandler(404)
    def missing(error):return jsonify(code='NOT_FOUND',message='入口不存在'),404
    @app.get('/')
    def index():return redirect('/'+MODULES[0][0])
    @app.get('/healthz')
    def health():return jsonify(status='ok')
    @app.post('/api/runs')
    def submit():
        content,params=payload();state=run(content,params);return jsonify(store.save(state['run_id'],state))
    @app.get('/api/runs')
    def list_runs():return jsonify(store.history())
    @app.get('/api/runs/<run_id>')
    def get_run(run_id):return jsonify(store.get(run_id))
    @app.post('/api/runs/<run_id>/review')
    def review(run_id):
        data=request.get_json(silent=True) or {}
        if not isinstance(data.get('version'),int):raise DomainError('VERSION','请提交复核版本')
        return jsonify(store.review(run_id,data['version'],data.get('decision')))
    @app.get('/api/archive/<run_id>/export.csv')
    def download(run_id):
        state=store.get(run_id)
        if not state.get('reconcile',{}).get('balanced'):raise DomainError('UNBALANCED','对账未平')
        return Response(bundle(state),mimetype='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename=evidence.csv'})
    @app.post('/api/intake')
    def intake_submit():
        data=request.get_json(silent=True) or {}
        if not isinstance(data,dict):raise DomainError('PAYLOAD','请求必须是对象')
        if 'csv' in data and not isinstance(data['csv'],str):raise DomainError('CSV','CSV内容必须是文本')
        if 'csv' in data:content=data['csv'].encode()
        elif 'sample' in data:content=load(data['sample'])
        else:raise DomainError('CSV','请提交CSV内容')
        return jsonify(ledger.intake(data.get('source'),content,data.get('parameters',{})))
    @app.get('/api/intake')
    def intake_list():return jsonify(ledger.receipts())
    @app.get('/api/period')
    def period_status():return jsonify(ledger.overview())
    @app.get('/period')
    def period_page():
        ov=ledger.overview()
        state=store.get(ov['period']['run_id']) if ov['period'] else ledger.empty_state()
        return render_template('period.html',title=TITLE,short=SHORT,modules=MODULES,current=8,data=state,run=state,history=store.history(),ov=ov)
    @app.get('/api/period/export.csv')
    def period_export():
        ov=ledger.overview()
        if not ov['period']:raise DomainError('EMPTY','尚无期间核算结果',404)
        if not ov['period']['balanced']:raise DomainError('UNBALANCED','对账未平')
        return Response(period_bundle(ov,store.get(ov['period']['run_id'])),mimetype='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename=period-evidence.csv'})
    with app.app_context():
        if not store.history():
            state=run(load('dirty.csv'),{});store.save(state['run_id'],state)
    return app

app=create_app()
if __name__=='__main__':app.run(host='0.0.0.0',port=8000)
