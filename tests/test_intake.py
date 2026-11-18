import csv,io,json
from datetime import datetime,timezone
from decimal import Decimal

HEADER='id,employee,shift_day,version,punch,at,shift_start,shift_end,break_start,break_end,hourly_rate,paid,note'
IN_ROWS=[
 '00001,E001,2026-10-01,1,IN,2026-10-01T22:00:00+08:00,22:00,06:00,02:00,03:00,20,220,夜班进',
 '00003,E002,2026-10-01,1,IN,2026-10-01T22:00:00+08:00,22:00,06:00,02:00,03:00,20,220,夜班进']
OUT_ROWS=[
 '00002,E001,2026-10-01,1,OUT,2026-10-02T06:00:00+08:00,22:00,06:00,02:00,03:00,20,0,夜班出',
 '00004,E002,2026-10-01,1,OUT,2026-10-02T06:00:00+08:00,22:00,06:00,02:00,03:00,20,0,夜班出']
IN_FILE=HEADER+'\n'+'\n'.join(IN_ROWS)+'\n'
OUT_FILE=HEADER+'\n'+'\n'.join(OUT_ROWS)+'\n'
BOTH_FILE=HEADER+'\n'+'\n'.join(IN_ROWS+OUT_ROWS)+'\n'

def post(client,source,content):return client.post('/api/intake',json={'source':source,'csv':content})

def test_waiting_then_ready(client):
    # 第一批只有进卡：当天可查，班次等卡中，不出工资金额
    first=post(client,'早批',IN_FILE);assert first.status_code==200
    body=first.get_json();assert body['idempotent'] is False
    assert len(body['shifts'])==2 and all(s['status']=='等卡中' and s['missing']=='OUT' for s in body['shifts'])
    assert all(s['amount'] is None for s in body['shifts'])
    assert Decimal(body['period']['business']['total'])==0
    # 第二批到件补齐出卡：跨文件配对，形成期间核算结果
    second=post(client,'晚批',OUT_FILE);assert second.status_code==200
    done=second.get_json()
    assert all(s['status']=='已可核算' for s in done['shifts'])
    assert Decimal(done['period']['business']['total'])==Decimal('440')
    assert Decimal(done['period']['business']['difference'])==0
    sources={r['source'] for s in done['shifts'] for r in s['receipts']}
    assert sources=={'早批','晚批'}

def test_receipt_evidence_same_day(client):
    post(client,'早批',IN_FILE)
    receipts=client.get('/api/intake').get_json()
    assert len(receipts)==1 and receipts[0]['source']=='早批' and receipts[0]['status']=='active'
    assert len(receipts[0]['sha256'])==64 and receipts[0]['stats']['input']==2
    assert receipts[0]['received_at'][:10]==datetime.now(timezone.utc).date().isoformat()

def test_idempotent_reintake(client):
    # 重跑同一来源同一内容：不新增批次、不新增金额
    assert post(client,'早批',IN_FILE).status_code==200
    again=post(client,'早批',IN_FILE).get_json()
    assert again['idempotent'] is True and len(client.get('/api/intake').get_json())==1
    post(client,'晚批',OUT_FILE)
    before=client.get('/api/period').get_json()
    run_id=before['period']['run_id']
    calcs=len(client.get('/api/runs/'+run_id).get_json()['calculations'])
    assert post(client,'晚批',OUT_FILE).get_json()['idempotent'] is True
    assert post(client,'早批',IN_FILE).get_json()['idempotent'] is True
    after=client.get('/api/period').get_json()
    assert len(client.get('/api/intake').get_json())==2
    assert after['period']['period_id']==before['period']['period_id']
    assert after['period']['business']==before['period']['business']
    assert len(client.get('/api/runs/'+run_id).get_json()['calculations'])==calcs

def test_source_update_invalidates(client):
    # 同一来源内容更新：旧接收批次已取代，旧期间结果失效并重算
    p1=post(client,'早批',IN_FILE).get_json()['period']['period_id']
    updated=post(client,'早批',BOTH_FILE).get_json()
    receipts=client.get('/api/intake').get_json()
    assert len(receipts)==2 and {r['status'] for r in receipts}=={'active','superseded'}
    assert updated['period']['period_id']!=p1
    assert all(s['status']=='已可核算' for s in updated['shifts'])
    stale=client.get('/api/period').get_json()['stale']
    assert [p['period_id'] for p in stale]==[p1] and stale[0]['status']=='stale'
    # 回滚到旧内容：复用原接收批次与期间结果，不产生重复
    rolled=post(client,'早批',IN_FILE).get_json()
    assert rolled['period']['period_id']==p1 and len(client.get('/api/intake').get_json())==2
    assert all(s['status']=='等卡中' for s in rolled['shifts'])

def test_header_mismatch_keeps_ledger(client):
    post(client,'早批',IN_FILE)
    bad=client.post('/api/intake',json={'source':'晚批','csv':'a,b\n1,2\n'})
    assert bad.status_code==422 and len(client.get('/api/intake').get_json())==1

def test_intake_requires_source(client):
    r=client.post('/api/intake',json={'csv':IN_FILE})
    assert r.status_code==422 and r.get_json()['code']=='SOURCE'

def test_period_page_groups(client):
    assert '尚无接收批次' in client.get('/period').data.decode()
    post(client,'早批',IN_FILE)
    page=client.get('/period').data.decode()
    assert '等卡中' in page and '已可核算' not in page
    post(client,'晚批',OUT_FILE)
    page=client.get('/period').data.decode()
    assert '已可核算' in page and '440' in page

def test_export_traces_receipts_and_quarantine(client):
    post(client,'早批',IN_FILE)
    waiting=client.get('/api/period/export.csv')
    assert waiting.status_code==200
    text=waiting.data.decode('utf-8-sig')
    assert '等卡中' in text and 'MISSING' in text
    post(client,'晚批',OUT_FILE)
    text=client.get('/api/period/export.csv').data.decode('utf-8-sig')
    rows=list(csv.reader(io.StringIO(text)))
    types={row[0] for row in rows[1:]}
    assert {'运行清单','接收','班次','计算','对账'}<=types
    receipts=client.get('/api/intake').get_json()
    for r in receipts:assert r['receipt_id'] in text and r['source'] in text
    shift_rows=[row for row in rows if row[0]=='班次']
    assert shift_rows and all(row[4]=='已可核算' for row in shift_rows)
    assert all(json.loads(row[5])['receipts'] for row in shift_rows)

def test_empty_period(client):
    body=client.get('/api/period').get_json()
    assert body['period'] is None and body['receipts']==[]
    assert client.get('/api/period/export.csv').status_code==404
