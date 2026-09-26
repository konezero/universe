import hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path
from contextlib import closing
from copy import deepcopy
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from todo_execution_journal import append,journal_path,resolve_role_result,JournalError
from persona_automation import read_host_review_pair,PersonaAutomationError


class ReworkProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.owner='owner';self.session='session';self.frame='host_rework'
        self.todo='todo';self.run='run';self.path=journal_path(self.root,self.todo)
        self.source_ref='universe://todo/'+self.todo
        self.content={'title':'unchanged','detail':'bounded acceptance'}
        self.scope={'repository_write_scope':'NONE','mutation_scope':{'operations':[],'targets':[]}}
        self.identity=dict(todo_id=self.todo,owner_ref=self.owner,run_id=self.run,task_frame_id=self.frame)
        self.pairs={};self.latest={};self.launch=None

    def result(self,role,attempt):
        rf=f'{self.frame}_{role}_{attempt}'
        path=self.root/'.ai/runtime/task_frames'/(hashlib.sha256((self.session+'\0'+rf).encode()).hexdigest()[:24]+'.sqlite3')
        path.parent.mkdir(parents=True,exist_ok=True)
        result=({'outcome':'COMPLETED','validation_state':'PASS','evidence_refs':['test://verified']}
                if role=='worker' else {'verdict':'PASS','evidence_refs':['test://reviewed']})
        actor=f'actor:{role}:{attempt}';receipt=f'receipt:{role}:{attempt}';turn=role+'-turn'
        envelope=dict(status='COMPLETED',turn_id=turn,result=result,result_receipt_ref=receipt,worker_id=actor)
        with closing(sqlite3.connect(path)) as db,db:
            db.executescript('''CREATE TABLE task_frame_context(frame_id,origin_session_id,origin_anchor_ref,source_ref,task_state,source_commit);
                CREATE TABLE task_turns(turn_id,state,claimed_by,result_json,created_at,completed_at);
                CREATE TABLE worker_execution_state(turn_id,result_receipt_ref,worker_result_envelope_json,worker_actor_ref);
                CREATE TABLE task_instructions(instruction_ordinal,expected_output_json,repository_write_scope);''')
            db.execute('INSERT INTO task_frame_context VALUES(?,?,?,?,?,?)',(rf,self.session,self.owner,self.source_ref,'COMPLETED','commit'))
            minute=attempt*10+(0 if role=='worker' else 2)
            db.execute('INSERT INTO task_turns VALUES(?,?,?,?,?,?)',(turn,'COMPLETED',actor,json.dumps(result),f'2026-09-23T01:{minute:02}:00Z',f'2026-09-23T01:{minute+1:02}:00Z'))
            db.execute('INSERT INTO worker_execution_state VALUES(?,?,?,?)',(turn,receipt,json.dumps(envelope),actor))
            db.execute('INSERT INTO task_instructions VALUES(?,?,?)',(1,json.dumps({'schema':f'universe.task-frame-host-{role}-output.v1'}),'NONE'))
        return resolve_role_result(self.root,self.session,self.owner,self.frame,self.todo,
                                   role.upper(),attempt,expected_source_ref=self.source_ref)

    def build(self,*,wrong_link=False,uncollected=False,closed=True):
        for attempt in range(1,5):
            for role in ('worker','reviewer'):
                collected=deepcopy(self.latest)
                if wrong_link and role=='reviewer' and attempt==4:collected['WORKER']['result_ref']='wrong-worker'
                payload=dict(role=role.upper(),attempt=attempt,todo=self.content,worker_write_scope=self.scope,collected_results=collected)
                ref=append(self.path,**self.identity,event_id=f'{role}-{attempt}',kind='ASSIGNED',payload=payload)
                if self.launch is None:self.launch=ref
                result=self.result(role,attempt)
                if not (uncollected and role=='reviewer' and attempt==4):
                    append(self.path,**self.identity,event_id=f'collect-{role}-{attempt}',kind='RESULT_COLLECTED',payload={'status':'COMPLETED',**result})
                self.latest[role.upper()]={'status':'COMPLETED',**result}
            self.pairs[attempt]={key+'_'+field:self.latest[key.upper()][field] for key in ('worker','reviewer') for field in ('result_ref','result_digest')}
        if closed:append(self.path,**self.identity,event_id='done',kind='MASTER_DONE',payload={'directive':'DONE'})

    def read(self,attempt=4,**kwargs):
        args=dict(launch_reference=self.launch,run_id=self.run,current_todo=self.content)
        return read_host_review_pair(self.root,self.session,self.owner,self.frame,self.todo,self.pairs[attempt],**{**args,**kwargs})

    def test_latest_fourth_pair_and_pass_spelling_are_supported(self):
        self.build();pair=self.read()
        self.assertEqual('PASS',pair['reviewer']['result']['verdict'])
        self.assertTrue(pair['completion_provenance_verified'])

    def test_goal_bound_result_requires_exact_goal_source(self):
        self.source_ref='universe://goals/goal_design'
        self.build()
        with self.assertRaises(JournalError):
            resolve_role_result(self.root,self.session,self.owner,self.frame,self.todo,
                                'REVIEWER',4)
        pair=self.read(expected_source_ref=self.source_ref)
        self.assertEqual('PASS',pair['reviewer']['result']['verdict'])
        with self.assertRaises(PersonaAutomationError):
            self.read(expected_source_ref='universe://goals/other_goal')

    def test_stale_pair_and_changed_current_todo_are_rejected(self):
        self.build()
        with self.assertRaises(JournalError):self.read(3)
        with self.assertRaises(JournalError):self.read(current_todo={'title':'new','detail':'changed'})
        self.assertFalse(self.read(current_todo=None)['completion_provenance_verified'])

    def test_reviewer_must_pin_selected_worker(self):
        self.build(wrong_link=True)
        with self.assertRaisesRegex(JournalError,'different Worker'):self.read()

    def test_uncollected_result_is_not_recovered(self):
        self.build(uncollected=True)
        with self.assertRaisesRegex(JournalError,'collection'):self.read()

    def test_closed_journal_required_and_later_assignment_invalidates_pair(self):
        self.build(closed=False)
        with self.assertRaisesRegex(JournalError,'closed Host'):self.read()
        append(self.path,**self.identity,event_id='done',kind='MASTER_DONE',payload={})
        append(self.path,**self.identity,event_id='pending',kind='ASSIGNED',payload={'role':'WORKER','attempt':5})
        with self.assertRaises(JournalError):self.read()

    def test_unjournaled_attempt_and_legacy_rework_are_rejected(self):
        self.build()
        with self.assertRaises(PersonaAutomationError):self.read(launch_reference=None)
        self.result('worker',5)
        with self.assertRaisesRegex(PersonaAutomationError,'Observed role'):self.read()

    def test_observed_idle_exit_can_replace_missing_done_but_not_missing_results(self):
        self.build(closed=False)
        closure=dict(task_frame_id=self.frame,alive=False,phase='EXITED',exit_reason='IDLE_TIMEOUT')
        self.assertEqual('PASS',self.read(host_closure=closure)['reviewer']['result']['verdict'])
        for changed in ({'alive':True},{'alive':None},{'task_frame_id':'other'},{'exit_reason':'CRASH'}):
            with self.subTest(changed=changed),self.assertRaises(JournalError):
                self.read(host_closure={**closure,**changed})
        append(self.path,**self.identity,event_id='pending',kind='ASSIGNED',payload={
            'role':'WORKER','attempt':5,'todo':self.content,'worker_write_scope':self.scope})
        with self.assertRaises(JournalError):self.read(host_closure=closure)

    def test_canonical_selector_rejects_unknown_or_noninteger_attempt(self):
        for role,attempt in [('MASTER',1),('WORKER',True),('WORKER',0),('WORKER','4')]:
            with self.subTest(role=role,attempt=attempt),self.assertRaises(JournalError):
                resolve_role_result(self.root,self.session,self.owner,self.frame,self.todo,role,attempt)

    def test_canonical_selector_does_not_accept_tampered_envelope(self):
        self.build()
        rf=self.frame+'_reviewer_4'
        path=self.root/'.ai/runtime/task_frames'/(hashlib.sha256((self.session+'\0'+rf).encode()).hexdigest()[:24]+'.sqlite3')
        with closing(sqlite3.connect(path)) as db,db:db.execute("UPDATE worker_execution_state SET result_receipt_ref='tampered'")
        with self.assertRaises(JournalError):resolve_role_result(self.root,self.session,self.owner,self.frame,self.todo,'REVIEWER',4)

    def test_idle_exit_does_not_upgrade_scoped_validation_to_full_pass(self):
        self.build(closed=False)
        rf=self.frame+'_worker_4'
        path=self.root/'.ai/runtime/task_frames'/(hashlib.sha256((self.session+'\0'+rf).encode()).hexdigest()[:24]+'.sqlite3')
        # Model an internally consistent immutable scoped result, not a digest mismatch.
        with closing(sqlite3.connect(path)) as db,db:
            result=json.loads(db.execute('SELECT result_json FROM task_turns').fetchone()[0])
            result['validation_state']='PASSED_SCOPED'
            envelope=json.loads(db.execute('SELECT worker_result_envelope_json FROM worker_execution_state').fetchone()[0])
            envelope['result']=result
            db.execute('UPDATE task_turns SET result_json=?',(json.dumps(result),))
            db.execute('UPDATE worker_execution_state SET worker_result_envelope_json=?',(json.dumps(envelope),))
        # Legacy single-pair reader validates the PASS gate separately from journal selection.
        from unittest.mock import patch
        selection={'attempts':{'worker':4,'reviewer':4},
                   'expected_frames':{f'{self.frame}_{role}_{attempt}' for role in ('worker','reviewer') for attempt in range(1,5)},
                   'completion_provenance_verified':True}
        self.pairs[4]['worker_result_digest']=hashlib.sha256(json.dumps(result,ensure_ascii=True,separators=(',',':'),sort_keys=True).encode()).hexdigest()
        with patch('task_frame_review_provenance.select_journal_review',return_value=selection):
            with self.assertRaisesRegex(PersonaAutomationError,'incomplete or unverified'):
                self.read(host_closure=dict(task_frame_id=self.frame,alive=False,phase='EXITED',exit_reason='IDLE_TIMEOUT'))
