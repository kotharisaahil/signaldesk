"""Single-user local application. No third-party Python dependencies."""
import argparse
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .engine import ROUTES, starter_profile, validate_profile, validate_assessments, score
from .pipeline import Runtime, manual_run, inquiry_source
from .research import research
from .workspace import Workspace, parse_csv, export_csv, brief

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'app' / 'static'


def load_examples(store):
    if store.setting('profile') or store.leads():
        raise ValueError('Examples only load into an empty workspace. Existing inputs and settings are preserved.')
    profile = store.save_profile(json.loads((ROOT / 'examples' / 'product-profile.json').read_text()))
    added = store.import_rows(parse_csv((ROOT / 'examples' / 'leads.csv').read_text()))
    for lead_id in added['added']:
        lead = store.lead(lead_id)
        sources = inquiry_source(lead)
        assessments = []
        for c in profile['criteria']:
            missing_owner = 'Harbor' in lead['company'] and c['id'] == 'ready1'
            assessments.append({'criterion_id':c['id'], 'status':'not_met' if missing_owner else 'met',
                                'reason':'Prepared fictional example: the supplied inquiry states this explicitly.',
                                'citations':[{'source_id':'inquiry','quote':lead['inquiry']}]})
        validated, warnings = validate_assessments(profile,sources,assessments)
        store.save_run(lead_id, {'profile':profile,'sources':sources,'assessments':validated,'warnings':warnings,
                                'result':score(profile,validated),'usage':{},'method':'example','duration_seconds':0})
    return added


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(120)

    def log_message(self, fmt, *args):
        pass  # Never log inquiry text, credentials, or request bodies.

    def reply(self, status, data, mime='application/json; charset=utf-8', filename=None):
        if isinstance(data,(dict,list)):
            data = json.dumps(data,ensure_ascii=False).encode()
        elif isinstance(data,str):
            data = data.encode()
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if filename:
            self.send_header('Content-Disposition','attachment; filename="'+filename+'"')
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError):
            pass

    def valid_host(self):
        port = str(self.server.server_address[1])
        return self.headers.get('Host','') in ('127.0.0.1:'+port,'localhost:'+port)

    def do_GET(self):
        if not self.valid_host():
            return self.reply(403,{'error':'Use the local application address.'})
        path = urlsplit(self.path).path
        store, runtime = self.server.store, self.server.runtime
        try:
            if path == '/api/state':
                return self.reply(200,{'profile':store.setting('profile'),'starter':starter_profile(),
                                      'connection':runtime.connection(),'leads':store.leads(),'jobs':store.jobs(),
                                      'routes':ROUTES,'token':self.server.token})
            if path.startswith('/api/lead/'):
                lead = store.details(path.rsplit('/',1)[-1])
                lead['research'] = store.setting('research:'+lead['id'],{})
                return self.reply(200,lead)
            if path == '/api/export.csv':
                return self.reply(200,export_csv(store.leads()),'text/csv; charset=utf-8','signaldesk-results.csv')
            if path == '/api/export.json':
                payload={'profile':store.setting('profile'),'leads':[store.details(l['id']) for l in store.leads()]}
                return self.reply(200,json.dumps(payload,indent=2),'application/json; charset=utf-8','signaldesk-results.json')
            if path == '/api/profile.json':
                return self.reply(200,json.dumps(store.setting('profile') or starter_profile(),indent=2),
                                  'application/json; charset=utf-8','product-profile.json')
            if path == '/api/template.csv':
                return self.reply(200,(ROOT/'examples'/'leads.csv').read_bytes(),'text/csv; charset=utf-8','lead-import-template.csv')
            if path.startswith('/api/brief/'):
                lead = store.details(path.rsplit('/',1)[-1])
                return self.reply(200,brief(lead),'text/markdown; charset=utf-8',lead['id']+'-brief.md')
            assets={'/':('index.html','text/html'),'/app.js':('app.js','text/javascript'),
                    '/style.css':('style.css','text/css'),'/favicon.svg':('favicon.svg','image/svg+xml')}
            if path in assets:
                filename,mime = assets[path]
                return self.reply(200,(STATIC/filename).read_bytes(),mime+'; charset=utf-8')
            return self.reply(404,{'error':'Not found.'})
        except KeyError:
            return self.reply(404,{'error':'Lead or result not found.'})

    def do_POST(self):
        if not self.valid_host() or self.headers.get('Origin', 'http://'+self.headers.get('Host','')) != 'http://'+self.headers.get('Host',''):
            return self.reply(403,{'error':'Requests must come from the local application.'})
        if not secrets.compare_digest(self.headers.get('X-SignalDesk-Token',''),self.server.token):
            return self.reply(403,{'error':'Your session changed. Reload the page and try again.'})
        if self.headers.get('Content-Type','').split(';')[0] != 'application/json':
            return self.reply(415,{'error':'Use JSON for this request.'})
        store, runtime = self.server.store, self.server.runtime
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0 < length <= 2000000:
                return self.reply(413,{'error':'Request too large or empty.'})
            body=json.loads(self.rfile.read(length))
            if not isinstance(body,dict):
                raise ValueError('Request must be an object.')
            path=urlsplit(self.path).path
            if path=='/api/profile':
                return self.reply(200,store.save_profile(body))
            if path=='/api/profile-preview':
                return self.reply(200,validate_profile(body))
            if path=='/api/connection':
                return self.reply(200,runtime.configure(body))
            if path=='/api/leads':
                return self.reply(201,store.import_rows([body]))
            if path=='/api/import':
                return self.reply(201,store.import_rows(parse_csv(body.get('csv'))))
            if path=='/api/import-preview':
                return self.reply(200,{'rows':parse_csv(body.get('csv'))})
            if path=='/api/examples':
                return self.reply(201,load_examples(store))
            if path=='/api/jobs':
                return self.reply(202,runtime.start(body.get('lead_ids'),body.get('fetch_website',True),body.get('consent')))
            if path.startswith('/api/cancel/'):
                return self.reply(200,runtime.cancel_job(path.rsplit('/',1)[-1]))
            if path.startswith('/api/manual/'):
                if not store.setting('profile') or body.get('profile_version') != store.setting('profile')['version']:
                    raise ValueError('The product profile changed. Reload before submitting the assessment.')
                return self.reply(201,manual_run(store,path.rsplit('/',1)[-1],body.get('assessments')))
            if path.startswith('/api/research/'):
                lead=store.lead(path.rsplit('/',1)[-1])
                if not lead['website']:
                    raise ValueError('This lead has no website. Use its inquiry as evidence.')
                sources,warnings=research(lead['website'])
                result={'sources':sources,'warnings':warnings}
                store.set_setting('research:'+lead['id'],result)
                return self.reply(200,result)
            if path.startswith('/api/review/'):
                return self.reply(200,store.review(path.rsplit('/',1)[-1],body.get('route'),body.get('reason','')))
            return self.reply(404,{'error':'Not found.'})
        except KeyError:
            return self.reply(404,{'error':'Lead or result not found.'})
        except (ValueError,TypeError) as error:
            return self.reply(400,{'error':str(error)})


def make_server(port, db_path):
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.store=Workspace(db_path)
    server.runtime=Runtime(server.store)
    server.token=secrets.token_urlsafe(32)
    return server


def main():
    parser=argparse.ArgumentParser(description='SignalDesk — qualify your leads against your own product criteria')
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--db',default=str(ROOT/'.data'/'workspace.db'))
    args=parser.parse_args()
    server=make_server(args.port,args.db)
    print('SignalDesk: http://127.0.0.1:{} (local only)'.format(args.port),flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.runtime.cancel.set()
        server.server_close()


if __name__=='__main__':
    main()
