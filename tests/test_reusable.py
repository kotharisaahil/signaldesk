"""Contract, evidence, import, persistence, networking, and HTTP workflow tests."""
import copy
import csv
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError

from app.engine import starter_profile,validate_profile,validate_assessments,score
from app.workspace import Workspace,parse_csv,export_csv,brief
from app.provider import analyze,parse_response,build_request,ProviderError
from app.research import clean_url,public_ip,research,ReadableHTML
from app.pipeline import Runtime,manual_run,qualify_one
from app.server import make_server,load_examples


def profile():
    p=starter_profile()
    p.update(name='Acme Analytics',description='Analytics for B2B SaaS. Requires usage data and a project owner.',customer='B2B SaaS companies.')
    return validate_profile(p)


def source():
    return {'id':'inquiry','kind':'inquiry','title':'Inquiry','url':'','retrieved_at':'2026-10-07T12:00:00Z',
            'text':'We sell B2B SaaS and want a demo within 30 days. Our operations lead owns the project and our usage data is ready.'}


def assessments(p=None,status='met'):
    p=p or profile()
    return [{'criterion_id':c['id'],'status':status,'reason':'Explicit evidence supplied.',
             'citations':[{'source_id':'inquiry','quote':source()['text']}] if status!='unknown' else []} for c in p['criteria']]


def response(data):
    return {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'assessments':data})}]}],
            'usage':{'input_tokens':1000,'output_tokens':500}}


class PolicyTests(unittest.TestCase):
    def test_custom_product_is_not_tied_to_support(self):
        p=profile();a,w=validate_assessments(p,[source()],assessments(p))
        self.assertEqual(score(p,a)['route'],'Sales ready')
        self.assertEqual(w,[])

    def test_profile_version_changes_with_policy(self):
        p=profile();p['criteria'][0]['weight']=80
        self.assertNotEqual(p['version'],validate_profile(p)['version'])

    def test_missing_group_rejected(self):
        p=profile();p['criteria']=[c for c in p['criteria'] if c['group']!='intent']
        with self.assertRaisesRegex(ValueError,'each group'):validate_profile(p)

    def test_duplicate_ids_rejected(self):
        p=profile();p['criteria'][1]['id']=p['criteria'][0]['id']
        with self.assertRaises(ValueError):validate_profile(p)

    def test_invalid_weights_and_thresholds(self):
        for weight in (0,101,True,'20'):
            p=profile();p['criteria'][0]['weight']=weight
            with self.subTest(weight=weight),self.assertRaises(ValueError):validate_profile(p)
        p=profile();p['thresholds']['fit']=0
        with self.assertRaises(ValueError):validate_profile(p)

    def test_unknown_not_false(self):
        p=profile();a=assessments(p,'unknown');r=score(p,a)
        self.assertEqual(r['route'],'Review needed')
        self.assertEqual((r['dimensions']['fit']['low'],r['dimensions']['fit']['high']),(0,100))

    def test_display_rounding_cannot_promote_a_lead(self):
        p=profile()
        fit=[]
        for i,weight in enumerate((100,100,12,91)):
            c=dict(p['criteria'][0],id='f'+str(i),weight=weight,required=False)
            fit.append(c)
        p['criteria']=fit+[c for c in p['criteria'] if c['group']!='fit']
        p=validate_profile(p)
        a=assessments(p);a[3]['status']='not_met'
        r=score(p,a)
        self.assertEqual(r['dimensions']['fit']['high'],70.0)
        self.assertEqual(r['route'],'Not a fit')

    def test_required_fit_fails_even_if_other_scores_high(self):
        p=profile();a=assessments(p);a[0]['status']='not_met'
        self.assertEqual(score(p,a)['route'],'Not a fit')

    def test_intent_failure_goes_to_nurture(self):
        p=profile();a=assessments(p);a[2]['status']='not_met'
        self.assertEqual(score(p,a)['route'],'Nurture')

    def test_unknown_readiness_goes_to_discovery(self):
        p=profile();a=assessments(p);a[4]['status']='unknown'
        self.assertEqual(score(p,a)['route'],'Discovery')

    def test_conflict_precedes_other_routes(self):
        p=profile();a=assessments(p);a[0]['status']='conflict'
        self.assertEqual(score(p,a)['route'],'Review needed')

    def test_hallucinated_quote_becomes_unknown(self):
        p=profile();a=assessments(p);a[0]['citations'][0]['quote']='We have a $1 million budget.'
        checked,warnings=validate_assessments(p,[source()],a)
        self.assertEqual(checked[0]['status'],'unknown');self.assertEqual(len(warnings),1)

    def test_website_cannot_establish_intent_or_readiness(self):
        p=profile();s=source();s.update(id='web1',kind='website');a=assessments(p)
        for item in a:item['citations'][0]['source_id']='web1'
        checked,warnings=validate_assessments(p,[s],a)
        self.assertEqual([v['status'] for v in checked],['met','met','unknown','unknown','unknown','unknown'])

    def test_duplicate_citations_do_not_prove_conflict(self):
        p=profile();a=assessments(p);a[0]['status']='conflict';a[0]['citations']*=2
        self.assertEqual(validate_assessments(p,[source()],a)[0][0]['status'],'unknown')

    def test_missing_criterion_does_not_silently_succeed(self):
        with self.assertRaises(ValueError):validate_assessments(profile(),[source()],assessments()[:-1])

    def test_reordered_criteria_are_matched_by_id(self):
        checked,_=validate_assessments(profile(),[source()],list(reversed(assessments())))
        self.assertEqual(checked[0]['criterion_id'],'fit1')


class ImportStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Path(self.tmp.name)/'workspace.db';self.store=Workspace(self.db)
    def tearDown(self):self.tmp.cleanup()
    def lead(self):
        return self.store.import_rows([{'company':'Example','inquiry':source()['text']}])['added'][0]

    def test_empty_workspace_by_default(self):
        self.assertEqual(self.store.leads(),[]);self.assertIsNone(self.store.setting('profile'))

    def test_csv_bom_quotes_and_multiline(self):
        rows=parse_csv('\ufeffcompany,website,inquiry\n"Example, Inc.",,"First line\nSecond line"\n')
        self.assertEqual(rows[0]['company'],'Example, Inc.');self.assertIn('\n',rows[0]['inquiry'])

    def test_bad_headers_and_partial_rows(self):
        for content in ('name,notes\nX,Y','company,company,inquiry\nX,Y,Z','company,website,inquiry\nX,https://example.com'):
            with self.subTest(content=content),self.assertRaises(ValueError):parse_csv(content)

    def test_invalid_row_import_is_atomic(self):
        with self.assertRaises(ValueError):self.store.import_rows([{'company':'Valid','inquiry':'Hello'},{'company':'','inquiry':'Invalid'}])
        self.assertEqual(self.store.leads(),[])

    def test_duplicates_skipped_on_reimport(self):
        self.lead();result=self.store.import_rows([{'company':'Example','inquiry':source()['text']}])
        self.assertEqual(result['skipped'],1);self.assertEqual(len(Workspace(self.db).leads()),1)

    def test_csv_limit(self):
        with self.assertRaises(ValueError):parse_csv('company,inquiry\n'+'X,hello\n'*101)

    def test_manual_workflow_snapshot_and_review(self):
        p=self.store.save_profile(profile());lead_id=self.lead();run=manual_run(self.store,lead_id,assessments(p))
        self.store.review(run['id'],'Discovery','Confirm prerequisites with the buyer.')
        changed=copy.deepcopy(p);changed['customer']='A different market';self.store.save_profile(changed)
        lead=Workspace(self.db).details(lead_id)
        self.assertTrue(lead['stale']);self.assertEqual(lead['route'],'Discovery')
        self.assertEqual(lead['latest_run']['profile']['version'],p['version'])
        self.assertIn('Confirm prerequisites',brief(lead))

    def test_new_run_does_not_inherit_previous_review(self):
        p=self.store.save_profile(profile());lead_id=self.lead();old=manual_run(self.store,lead_id,assessments(p))
        self.store.review(old['id'],'Nurture','Different timing.')
        manual_run(self.store,lead_id,assessments(p))
        self.assertIsNone(self.store.details(lead_id)['review'])
        self.assertEqual(len(self.store.details(lead_id)['runs'][1]['reviews']),1)

    def test_override_requires_reason(self):
        p=self.store.save_profile(profile());run=manual_run(self.store,self.lead(),assessments(p))
        with self.assertRaises(ValueError):self.store.review(run['id'],'Nurture','')

    def test_exports_defuse_spreadsheet_formulas(self):
        self.store.import_rows([{'company':'=1+1','inquiry':'@test'}])
        row=list(csv.DictReader(io.StringIO(export_csv(self.store.leads()))))[0]
        self.assertEqual(row['company'],"'=1+1");self.assertEqual(row['inquiry'],"'@test")

    def test_examples_do_not_replace_user_data(self):
        self.lead()
        with self.assertRaises(ValueError):load_examples(self.store)
        self.assertIsNone(self.store.setting('profile'))


class ProviderResearchTests(unittest.TestCase):
    def test_documented_request_contract_and_usage(self):
        captured=[]
        def transport(payload):captured.append(payload);return response(assessments())
        items,usage=analyze(profile(),{'company':'Example'},[source()],'test-only-key',transport=transport)
        self.assertEqual(len(items),6);self.assertEqual(len(captured),1)
        self.assertFalse(captured[0]['store']);self.assertTrue(captured[0]['text']['format']['strict'])
        self.assertEqual(usage['estimated_cost_usd'],.0012)
        self.assertNotIn('test-only-key',json.dumps(captured))

    def test_refusal_and_incomplete_responses_fail(self):
        for r in ({'status':'incomplete'}, {'status':'completed','output':[{'content':[{'type':'refusal'}]}]}, {'status':'completed','output':[]}):
            with self.subTest(response=r),self.assertRaises(ProviderError):parse_response(r)

    def test_key_is_required(self):
        with self.assertRaises(ProviderError):analyze(profile(),{'company':'X'},[source()],'')

    def test_research_url_restrictions(self):
        for url in ('http://example.com','https://127.0.0.1','https://[::1]/','https://169.254.169.254','https://name:password@example.com','https://example.com:999','https://x.local'):
            with self.subTest(url=url),self.assertRaises(ValueError):clean_url(url)
        self.assertEqual(clean_url('example.com'),'https://example.com/')

    def test_dns_rebinding_and_mixed_private_answers_blocked(self):
        with patch('app.research.socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443)),(2,1,6,'',('10.0.0.1',443))]):
            with self.assertRaises(ValueError):public_ip('example.com')

    def test_html_discards_executable_text(self):
        parser=ReadableHTML();parser.feed('<h1>Useful facts</h1><script>steal secrets</script><style>hidden</style><p>More evidence</p>')
        self.assertEqual(parser.parts,['Useful facts','More evidence'])

    def test_crawler_collects_sources_and_stays_on_site(self):
        calls=[]
        def fetcher(url,deadline,allowed=None):
            calls.append(url)
            if url.endswith('robots.txt'):return url,404,'text/plain',''
            if allowed and not allowed(url):raise ValueError('Disallowed')
            return url,200,'text/html','<title>Example</title><p>'+('Our software helps teams analyze their customer data. '*5)+'</p><a href="/about">About</a><a href="https://external.example/about">External</a>'
        sources,warnings=research('https://example.com',fetcher)
        self.assertEqual(len(sources),2);self.assertEqual(warnings,[])
        self.assertTrue(all('external.example' not in c for c in calls))
        self.assertEqual(sources[0]['kind'],'website')

    def test_robots_disallow_avoids_page_fetch(self):
        calls=[]
        def fetcher(url,deadline,allowed=None):calls.append(url);return url,200,'text/plain','User-agent: *\nDisallow: /'
        sources,warnings=research('https://example.com',fetcher)
        self.assertEqual(sources,[]);self.assertEqual(len(calls),1);self.assertTrue(warnings)

    def test_live_pipeline_contract_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Workspace(Path(folder)/'db');p=store.save_profile(profile())
            lead_id=store.import_rows([{'company':'Test','website':'https://example.com','inquiry':source()['text']}])['added'][0]
            def analyzer(p,l,s,key,model):return analyze(p,l,s,key,model,transport=lambda _:response(assessments(p)))
            run=qualify_one(store,lead_id,p,'test-key','gpt-4.1-mini',True,researcher=lambda _:([],['Website unavailable']),analyzer=analyzer)
            self.assertEqual(run['result']['route'],'Sales ready');self.assertIn('Website unavailable',run['warnings'])
            self.assertNotIn('test-key',json.dumps(store.details(lead_id)))


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Workspace(Path(self.tmp.name)/'db');self.store.save_profile(profile())
        self.ids=self.store.import_rows([{'company':'One','inquiry':source()['text']},{'company':'Two','inquiry':source()['text']}])['added']
    def tearDown(self):self.tmp.cleanup()

    def test_cancel_skips_remaining_and_keeps_completed(self):
        started,release=threading.Event(),threading.Event()
        def qualifier(store,lead_id,p,key,model,research):
            started.set();release.wait(2);return manual_run(store,lead_id,assessments(p))
        runtime=Runtime(self.store,qualifier);runtime.configure({'api_key':'test-key'})
        job=runtime.start(self.ids,False,True);self.assertTrue(started.wait(2))
        runtime.cancel_job(job['id']);release.set()
        for _ in range(100):
            if runtime.active is None:break
            time.sleep(.01)
        saved=self.store.jobs()[0]
        self.assertEqual(saved['status'],'cancelled')
        self.assertEqual([i['status'] for i in saved['items']],['completed','cancelled'])

    def test_failed_lead_does_not_erase_other_results(self):
        def qualifier(store,lead_id,p,key,model,research):
            if lead_id==self.ids[0]:raise ValueError('No evidence')
            return manual_run(store,lead_id,assessments(p))
        runtime=Runtime(self.store,qualifier);runtime.configure({'api_key':'test-key'});runtime.start(self.ids,False,True)
        for _ in range(100):
            if runtime.active is None:break
            time.sleep(.01)
        self.assertEqual(self.store.jobs()[0]['status'],'completed_with_errors')
        self.assertIsNotNone(self.store.details(self.ids[1])['latest_run'])

    def test_restart_marks_jobs_interrupted_no_retry(self):
        self.store.save_job({'id':'old','status':'running','items':[{'lead_id':self.ids[0],'status':'running'}]})
        Runtime(self.store,lambda *args:self.fail('Should not run'))
        self.assertEqual(self.store.jobs()[0]['status'],'interrupted')

    def test_account_error_stops_remaining_paid_calls(self):
        calls=[]
        def qualifier(*args):
            calls.append(args[1])
            raise ProviderError('Account quota reached.',stop_batch=True)
        runtime=Runtime(self.store,qualifier);runtime.configure({'api_key':'test-key'})
        runtime.start(self.ids,False,True)
        for _ in range(100):
            if runtime.active is None:break
            time.sleep(.01)
        self.assertEqual(len(calls),1)
        job=self.store.jobs()[0]
        self.assertEqual(job['status'],'completed_with_errors')
        self.assertEqual([i['status'] for i in job['items']],['failed','skipped'])

    def test_key_never_persisted_or_exposed(self):
        runtime=Runtime(self.store);runtime.configure({'api_key':'secret-for-test'})
        self.assertTrue(runtime.connection()['key_available'])
        self.assertNotIn('secret-for-test',json.dumps(runtime.connection()))
        self.assertNotIn(b'secret-for-test',Path(self.store.path).read_bytes())
        runtime.configure({'clear_key':True});self.assertFalse(runtime.connection()['key_available'])


class AppHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.server=make_server(0,Path(self.tmp.name)/'db')
        self.url='http://127.0.0.1:'+str(self.server.server_address[1])
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()
    def request(self,path,body=None,headers=None):
        h={'Content-Type':'application/json','X-SignalDesk-Token':self.server.token};h.update(headers or {})
        return urlopen(Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,headers=h),timeout=3)
    def test_http_profile_import_manual_review_export(self):
        with self.request('/api/profile',profile()) as r:p=json.load(r)
        with self.request('/api/import',{'csv':'company,inquiry\nExample,"'+source()['text']+'"\n'}) as r:lead_id=json.load(r)['added'][0]
        with self.request('/api/manual/'+lead_id,{'profile_version':p['version'],'assessments':assessments(p)}) as r:run=json.load(r)
        with self.request('/api/review/'+run['id'],{'route':'Discovery','reason':'Clarify pilot scope.'}) as r:self.assertEqual(json.load(r)['route'],'Discovery')
        with self.request('/api/export.csv') as r:self.assertIn('Clarify pilot scope.',r.read().decode())
        with self.request('/api/brief/'+lead_id) as r:self.assertIn('Current route: Discovery',r.read().decode())
    def test_session_token_and_origin(self):
        for headers in ({'X-SignalDesk-Token':'wrong'},{'Origin':'https://external.example'}):
            with self.subTest(headers=headers),self.assertRaises(HTTPError) as ctx:self.request('/api/profile',profile(),headers)
            self.assertEqual(ctx.exception.code,403)
    def test_import_preview_has_no_side_effect(self):
        with self.request('/api/import-preview',{'csv':'company,inquiry\nExample,hello\n'}) as r:self.assertEqual(len(json.load(r)['rows']),1)
        self.assertEqual(self.server.store.leads(),[])
    def test_no_key_is_explicit_error_not_fake_result(self):
        self.server.runtime.key='';self.server.store.save_profile(profile())
        lead_id=self.server.store.import_rows([{'company':'X','inquiry':'Hello'}])['added'][0]
        with self.assertRaises(HTTPError) as ctx:self.request('/api/jobs',{'lead_ids':[lead_id],'consent':True})
        self.assertIn('API key',json.load(ctx.exception)['error']);self.assertEqual(self.server.store.jobs(),[])
    def test_stale_manual_form_rejected(self):
        self.server.store.save_profile(profile());lead_id=self.server.store.import_rows([{'company':'X','inquiry':'Hello'}])['added'][0]
        with self.assertRaises(HTTPError) as ctx:self.request('/api/manual/'+lead_id,{'profile_version':'old','assessments':assessments()})
        self.assertEqual(ctx.exception.code,400)
    def test_assets_and_no_key_in_state(self):
        self.server.runtime.key='secret-for-test'
        with self.request('/api/state') as r:self.assertNotIn('secret-for-test',r.read().decode())
        for path in ('/','/app.js','/style.css','/api/template.csv'):
            with self.subTest(path=path),self.request(path) as r:self.assertEqual(r.status,200)


if __name__=='__main__':unittest.main()
