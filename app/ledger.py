import csv,hashlib,io,json
from datetime import datetime,timezone
from decimal import Decimal
from app import store
from app.config import RULE_VERSION
from app.errors import DomainError
from app.modules.m1_ingest.decode import decode
from app.modules.m1_ingest.reader import read
from app.workflow import run

# 接收层：每个到件文件当天登记为独立接收批次（证据），当天可查。
# 核算层：不按单文件出核算结果；跨接收批次合并后，班次进出卡补齐才进入期间核算。
DDL=(
    "CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, source TEXT NOT NULL, sha256 TEXT NOT NULL, received_at TEXT NOT NULL, bytes INTEGER NOT NULL, stats TEXT NOT NULL, parameters TEXT NOT NULL, content BLOB NOT NULL, status TEXT NOT NULL DEFAULT 'active', superseded_at TEXT)",
    "CREATE TABLE IF NOT EXISTS periods (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, created_at TEXT NOT NULL, receipts TEXT NOT NULL, provenance TEXT NOT NULL, parameters TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'current')",
)

def connection():
    db=store.connection()
    for statement in DDL:db.execute(statement)
    return db

def _now():return datetime.now(timezone.utc).isoformat()

def _receipt_id(source,digest):return hashlib.sha256((source+'\0'+digest).encode('utf-8')).hexdigest()[:20]

def _period_id(receipt_ids,parameters):
    raw=json.dumps({'receipts':sorted(receipt_ids),'parameters':parameters,'rules':RULE_VERSION},sort_keys=True,ensure_ascii=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]

def _receipt(row):
    return {'receipt_id':row['id'],'source':row['source'],'sha256':row['sha256'],
        'received_at':row['received_at'],'bytes':row['bytes'],'stats':json.loads(row['stats']),
        'parameters':json.loads(row['parameters']),'status':row['status'],'superseded_at':row['superseded_at']}

def _period(row):return {'period_id':row['id'],'run_id':row['run_id'],'created_at':row['created_at'],'status':row['status']}

def _stats(content):
    state=read(content)
    return {'input':state['input_count'],'accepted':len(state['rows']),'isolated':len(state['quarantine']),
        'blank':state['blank_count'],'headers':state['header_count'],'duplicates':state['duplicate_count'],
        'encoding':state['encoding']}

def _merge(parts):
    # 合并各有效接收批次为一份逻辑输入，并记录 记录ID->接收批次 的来源映射。
    header=header_line=None;bodies=[];provenance={}
    for rid,content in parts:
        text,_=decode(content)
        text=text.replace('\r\n','\n').replace('\r','\n').lstrip('\ufeff')
        first,_,rest=text.partition('\n')
        parsed=[c.strip() for c in next(csv.reader(io.StringIO(first)))]
        if header is None:header,header_line=parsed,first
        elif parsed!=header:raise DomainError('HEADER','来源表头不一致')
        if rest and not rest.endswith('\n'):rest+='\n'
        bodies.append(rest)
        position=header.index('id') if 'id' in header else 0
        for record in csv.reader(io.StringIO(rest)):
            if len(record)==len(header) and record[position].strip():
                key=record[position].strip();provenance.setdefault(key,[])
                if rid not in provenance[key]:provenance[key].append(rid)
    return (header_line+'\n'+''.join(bodies)).encode('utf-8'),provenance

def _classify(state,provenance,receipts_by_id):
    amounts={}
    for calc in state.get('calculations',[]):amounts[calc['id']]=amounts.get(calc['id'],Decimal(0))+Decimal(str(calc['amount']))
    segmented={s['id'] for s in state.get('segments',[])}
    order={'等卡中':0,'异常隔离':1,'已可核算':2};shifts=[]
    for shift in state.get('shifts',[]):
        record_ids=[str(r.get('_raw',{}).get('id','')).strip() for r in shift['sources']]
        rids=sorted({rid for record in record_ids for rid in provenance.get(record,[])})
        if shift['in'] is None or shift['out'] is None:status,missing='等卡中','IN' if shift['in'] is None else 'OUT'
        elif shift['id'] in segmented:status,missing='已可核算',''
        else:status,missing='异常隔离',''
        shifts.append({'id':shift['id'],'employee':shift['employee'],'day':shift['day'],
            'status':status,'missing':missing,
            'amount':str(amounts[shift['id']]) if status=='已可核算' and shift['id'] in amounts else None,
            'receipts':[{'receipt_id':r,'source':receipts_by_id[r]['source']} for r in rids if r in receipts_by_id]})
    return sorted(shifts,key=lambda s:(order[s['status']],s['id']))

def overview():
    with connection() as db:
        receipt_rows=db.execute('SELECT * FROM receipts ORDER BY rowid').fetchall()
        period_rows=db.execute('SELECT * FROM periods ORDER BY rowid').fetchall()
    receipts=[_receipt(r) for r in receipt_rows];by_id={r['receipt_id']:r for r in receipts}
    current=next((p for p in period_rows if p['status']=='current'),None)
    out={'receipts':receipts,'period':None,'shifts':[],
        'stale':[_period(p) for p in reversed(period_rows) if p['status']=='stale']}
    if current:
        state=store.get(current['run_id'])
        shifts=_classify(state,json.loads(current['provenance']),by_id)
        counts={}
        for s in shifts:counts[s['status']]=counts.get(s['status'],0)+1
        out['period']=dict(_period(current),receipts=json.loads(current['receipts']),
            business=state.get('business'),balanced=state.get('reconcile',{}).get('balanced',False),counts=counts)
        out['shifts']=shifts
    return out

def receipts():
    with connection() as db:rows=db.execute('SELECT * FROM receipts ORDER BY rowid').fetchall()
    return [_receipt(r) for r in rows]

def intake(source,content,parameters=None):
    if not isinstance(source,str) or not source.strip():raise DomainError('SOURCE','来源标识必填')
    source=source.strip()
    if isinstance(content,str):content=content.encode('utf-8')
    if not isinstance(content,(bytes,bytearray)):raise DomainError('CSV','CSV内容必须是文本')
    content=bytes(content)
    if len(content)>2_000_000:raise DomainError('SIZE','文件超过2MB',413)
    parameters=parameters or {}
    if not isinstance(parameters,dict):raise DomainError('PARAMETERS','参数必须是对象')
    digest=hashlib.sha256(content).hexdigest();rid=_receipt_id(source,digest)
    with connection() as db:existing=db.execute('SELECT status FROM receipts WHERE id=?',(rid,)).fetchone()
    if existing and existing['status']=='active':
        # 同一来源同一内容重复到件：幂等命中，不新增批次、不重算、金额不变。
        result=overview();result['idempotent']=True;return result
    stats=_stats(content)
    with connection() as db:active=db.execute("SELECT * FROM receipts WHERE status='active' ORDER BY rowid").fetchall()
    parts=[(r['id'],r['content']) for r in active if r['source']!=source]
    parts.append((rid,content))
    merged,provenance=_merge(parts)
    state=run(merged,parameters)
    store.save(state['run_id'],state)
    pid=_period_id([p for p,_ in parts],parameters);now=_now()
    with connection() as db:
        # 同源新内容：旧接收批次标记已取代，旧期间结果整体失效，按最新有效接收集重算。
        db.execute("UPDATE receipts SET status='superseded', superseded_at=? WHERE source=? AND id!=? AND status='active'",(now,source,rid))
        db.execute("INSERT INTO receipts(id,source,sha256,received_at,bytes,stats,parameters,content,status) VALUES (?,?,?,?,?,?,?,?,'active') "
            "ON CONFLICT(id) DO UPDATE SET status='active', superseded_at=NULL",
            (rid,source,digest,now,len(content),json.dumps(stats,ensure_ascii=False),json.dumps(parameters,ensure_ascii=False),content))
        db.execute("UPDATE periods SET status='stale' WHERE status='current'")
        db.execute("INSERT INTO periods(id,run_id,created_at,receipts,provenance,parameters,status) VALUES (?,?,?,?,?,?,'current') "
            "ON CONFLICT(id) DO UPDATE SET status='current', run_id=excluded.run_id, created_at=excluded.created_at, receipts=excluded.receipts, provenance=excluded.provenance, parameters=excluded.parameters",
            (pid,state['run_id'],now,json.dumps([p for p,_ in parts]),json.dumps(provenance),json.dumps(parameters,ensure_ascii=False)))
    result=overview();result['idempotent']=False
    return result

def empty_state():
    return {'rows':[],'quarantine':[],'input_count':0,'business':None,'run_id':'—',
        'manifest':{'generated_at':'—','rule_version':RULE_VERSION},
        'shifts':[],'segments':[],'calculations':[],'decision':'待接收','review_version':'—'}
