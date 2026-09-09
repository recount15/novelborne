from __future__ import annotations
import argparse, json, os, shutil, sqlite3, tempfile, sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.dont_write_bytecode = True

def events(resp):
    out=[]
    for line in resp.text.splitlines():
        if line.strip():
            try: out.append(json.loads(line))
            except Exception: out.append({"type":"parse_error","raw":line[:200]})
    return out

def main():
    p=argparse.ArgumentParser(); p.add_argument('--key',default=os.getenv('FATE_API_KEY','')); p.add_argument('--book',default=''); p.add_argument('--base-url',default=''); p.add_argument('--model',default='gpt-5.4-mini'); p.add_argument('--keep-runtime',action='store_true'); a=p.parse_args()
    if not a.key: raise SystemExit('missing --key or FATE_API_KEY')
    if not a.book: raise SystemExit('missing --book (TXT path)')
    if not a.base_url: raise SystemExit('missing --base-url (OpenAI-compatible endpoint)')
    runtime=Path(tempfile.mkdtemp(prefix='novelborne-smoke-')); os.environ['FATE_VAR_DIR']=str(runtime); os.environ['FATE_DISTILL_WORKERS']='1'
    try:
        db=runtime/'db'/'fate_engine.db'; db.parent.mkdir(parents=True); sqlite3.connect(db).close()
        import core.engine.character_library as cl, core.engine.work_distiller as wd
        cl.USER_LIBRARY_DIR=runtime/'characters'; cl.OVERRIDES_DIR=cl.USER_LIBRARY_DIR/'overrides'; wd._work_library_path=lambda: runtime/'work_library.md'
        from fastapi.testclient import TestClient
        from core import server
        server.operation_journal.path=runtime/'operations.jsonl'
        c=TestClient(server.app); book=Path(a.book)
        with book.open('rb') as f: r=c.post('/api/uploads',files={'file':(book.name,f,'text/plain')},data={'kind':'novel'})
        assert r.status_code==200,r.text; sid=r.json()['session_id']; uid=r.json()['upload']['upload_id']
        cfg={'session_id':sid,'provider':'custom','base_url':a.base_url,'api_key':a.key,'model':a.model,'mode':'强化模式','novel_upload_id':uid,'role':'孙悟空','protagonist_gender':'male','difficulty':'D4 普通','distill_enabled':True,'story_richness':300,'paper_tier':1}
        def stream(path,payload):
            rr=c.post(path,json=payload,timeout=1800)
            if rr.status_code != 200: raise RuntimeError(f'{path}: HTTP {rr.status_code}')
            ev=events(rr); bad=[x for x in ev if x.get('type') in ('error','parse_error')]
            if bad:
                data=bad[-1].get('data') or {}; raise RuntimeError(f'{path}: {data.get("message", "provider or protocol failure")}')
            if not any(x.get('type')=='done' for x in ev): raise RuntimeError(f'{path}: stream ended without done')
            return ev
        stream('/api/sessions/start',cfg); stream(f'/api/sessions/{sid}/messages',{'message':'确认无金手指'}); e1=stream(f'/api/sessions/{sid}/messages',{'message':'确认开局'})
        st=c.get(f'/api/sessions/{sid}/state').json()['state']; r1=int(st.get('round',0)); assert st.get('save_stage') in ('opening','committed')
        option_keys=[x.get('key') for x in (st.get('options') or [])]
        assert len(option_keys)==6 and option_keys==list('ABCDEF'), f'options invalid: count={len(option_keys)} keys={option_keys}'
        stream(f'/api/sessions/{sid}/messages',{'message':'A'}); st=c.get(f'/api/sessions/{sid}/state').json()['state']; assert int(st.get('round',0))==r1+1; assert st.get('save_stage')=='committed'
        assert all(x.get('committed') is True for x in st.get('story_ledger',[])[-2:])
        assert c.post(f'/api/sessions/{sid}/save',json={'save_id':'smoke'}).status_code==200
        loaded=c.post('/api/saves/load',json={'save_id':'smoke'}); assert loaded.status_code==200,loaded.text
        ex=c.post(f'/api/sessions/{sid}/export-novel',json={'provider':'custom','base_url':a.base_url,'api_key':a.key,'model':a.model},timeout=1800); assert ex.status_code==200,ex.text
        report={'ok':True,'round':st['round'],'save_stage':st['save_stage'],'options':[x['key'] for x in st['options']],'runtime':str(runtime)}; print(json.dumps(report,ensure_ascii=False))
    finally:
        if not a.keep_runtime:
            # runtime 由 tempfile.mkdtemp 生成；删除前仍校验确实位于系统临时目录内
            if str(runtime.resolve()).startswith(str(Path(tempfile.gettempdir()).resolve()) + os.sep):
                shutil.rmtree(runtime, ignore_errors=True)
if __name__=='__main__': main()
