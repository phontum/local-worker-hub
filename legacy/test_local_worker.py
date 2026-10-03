"""Regression checks for launcher failures, recovery, and concurrency (no LLM)."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

loader = importlib.machinery.SourceFileLoader('worker', str(Path(__file__).with_name('local-worker')))
spec = importlib.util.spec_from_loader(loader.name, loader)
w = importlib.util.module_from_spec(spec)
loader.exec_module(w)
REPORT = 'LOCAL_WORKER_REPORT\nStatus: COMPLETE\nFindings:\nFound entry.py:1.\nFiles:\nentry.py\nChecks:\nNot run (read-only investigation).\nRisks:\nNone identified.\nEND_LOCAL_WORKER_REPORT'


def session(text=REPORT, finish='stop', outcome='succeeded'):
    return {'info': {'outcome': outcome}, 'messages': [{'type': 'user'}, {
        'type': 'assistant', 'finish': finish, 'content': [{'type': 'text', 'text': text}]}]}


class ReportTests(unittest.TestCase):
    def test_complete(self):
        self.assertEqual(w.final_report(session())[1], 'COMPLETE')

    def test_inline_section_contents(self):
        text = REPORT
        for label in ('Findings', 'Files', 'Checks', 'Risks'):
            text = text.replace(label + ':\n', label + ': ')
        self.assertEqual(w.report_status(text), 'COMPLETE')

    def test_truncation_even_with_valid_envelope(self):
        self.assertIsNone(w.final_report(session(finish='length'))[1])

    def test_tool_activity_and_preamble_are_not_reports(self):
        for text in ('', 'I will inspect this.', REPORT.replace('Files:', '')):
            self.assertIsNone(w.final_report(session(text))[1])

    def test_previous_turn_cannot_count_as_current_report(self):
        p = session()
        p['messages'].append({'type': 'user'})
        self.assertIsNone(w.final_report(p)[1])

    def test_failed_session_rejected(self):
        self.assertIsNone(w.final_report(session(outcome='failed'))[1])

    def test_partial_and_blocked(self):
        for status in ('PARTIAL', 'BLOCKED'):
            self.assertEqual(w.report_status(REPORT.replace('COMPLETE', status)), status)

    def test_evidence_is_bounded_and_excludes_reasoning(self):
        p = session()
        p['messages'][-1]['content'] = [{'type': 'reasoning', 'text': 'PRIVATE REASONING'}] + [
            {'type': 'tool', 'name': 'read', 'state': {'status': 'completed', 'content': [
                {'type': 'text', 'text': 'x' * 10000}]}} for _ in range(30)]
        text = w.evidence_excerpt(p)
        self.assertNotIn('PRIVATE REASONING', text)
        self.assertLessEqual(len(text), 22000)
        self.assertIn('TRUNCATED', text)

    def test_read_only_and_report_policies(self):
        def effect(rules, action):
            selected = [r['effect'] for r in rules if r['action'] in ('*', action) and r['resource'] == '*']
            return selected[-1]
        rules = w.runtime_config()['agents']['local-worker']['permissions']
        for action in ('edit', 'shell', 'subagent', 'question', 'new-tool'):
            self.assertEqual(effect(rules, action), 'deny')
        for action in ('read', 'glob', 'grep'):
            self.assertEqual(effect(rules, action), 'allow')
        report = w.runtime_config(report_only=True)['agents']['local-worker-report']
        self.assertEqual(effect(report['permissions'], 'read'), 'deny')


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.binary = self.root / 'opencode'
        self.binary.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
case=os.environ.get('WORKER_TEST_CASE','complete')
report=os.environ['WORKER_TEST_REPORT']
if args==['--version']:
 print('opencode v2.0.22');sys.exit(0)
if args[0]=='run':
 cfg=json.loads(os.environ['OPENCODE_CONFIG_CONTENT'])
 recovery='local-worker-report' in args
 with open(os.environ['WORKER_TEST_CALLS'],'a') as f:f.write(('recovery' if recovery else 'work')+'\\n')
 if case=='runtime':sys.exit(9)
 print(json.dumps({'type':'text','sessionID':'ses_recovery' if recovery else 'ses_work','part':{'text':'progress'}}))
 sys.exit(0)
if args[:2]==['session','export']:
 recovery=args[-1]=='ses_recovery'
 text=report
 finish='stop'
 if case in ('missing','recovery_bad') and not recovery:text='';finish='length'
 if recovery:text=report.replace('COMPLETE','PARTIAL')
 if case=='recovery_bad' and recovery:text='still no report'
 if case=='partial':text=report.replace('COMPLETE','PARTIAL')
 print(json.dumps({'info':{'outcome':'succeeded','location':{'directory':'/wrong-directory' if case=='wrongdir' else os.getcwd()}},'messages':[{'type':'user'},{'type':'assistant','finish':finish,'content':[{'type':'text','text':text}]}]}))
''')
        self.binary.chmod(0o755)
        self.calls = self.root / 'calls'

    def invoke(self, case='complete', args=()):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(w.Path, 'home', return_value=self.root), mock.patch.object(w.shutil, 'which', return_value=str(self.binary)), \
             mock.patch.object(sys, 'argv', ['local-worker', *args, 'Inspect entry points']), \
             mock.patch.dict(os.environ, {'WORKER_TEST_CASE':case, 'WORKER_TEST_REPORT':REPORT, 'WORKER_TEST_CALLS':str(self.calls)}), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = w.main()
        return result, out.getvalue(), err.getvalue()

    def test_default_allows_targeted_edits(self):
        code, out, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertIn(REPORT, out)
        metadata = next(self.root.glob('.local/state/opencode/local-worker/*/metadata.json'))
        self.assertTrue(json.loads(metadata.read_text())['write'])

    def test_readonly_success_and_private_logs(self):
        self.assertEqual(self.invoke(args=['--read-only'])[0], 0)
        run = next(self.root.glob('.local/state/opencode/local-worker/*/report.txt'))
        self.assertEqual(run.stat().st_mode & 0o777, 0o600)

    def test_missing_recovers_once_without_execution_retry(self):
        code, out, _ = self.invoke('missing')
        self.assertEqual(code, 2)
        self.assertIn('Status: PARTIAL', out)
        self.assertEqual(self.calls.read_text().splitlines(), ['work','recovery'])

    def test_no_recovery(self):
        self.assertEqual(self.invoke('missing',['--no-recovery'])[0], 3)
        self.assertEqual(self.calls.read_text().splitlines(), ['work'])

    def test_failed_recovery_is_failure(self):
        self.assertEqual(self.invoke('recovery_bad')[0], 3)

    def test_runtime_error_does_not_retry_edits(self):
        self.assertEqual(self.invoke('runtime')[0], 4)
        self.assertEqual(self.calls.read_text().splitlines(), ['work'])

    def test_partial_not_success(self):
        self.assertEqual(self.invoke('partial')[0], 2)

    def test_two_slot_limit(self):
        root = self.root / '.local/state/opencode/local-worker'
        root.mkdir(parents=True)
        with (root/'slot-0.lock').open('a') as a, (root/'slot-1.lock').open('a') as b:
            for h in (a,b):w.fcntl.flock(h,w.fcntl.LOCK_EX|w.fcntl.LOCK_NB)
            self.assertEqual(self.invoke()[0],75)
        self.assertFalse(self.calls.exists())

    def test_conflicting_repository_lock(self):
        root = self.root / '.local/state/opencode/local-worker'
        root.mkdir(parents=True)
        git=subprocess.run(['git','rev-parse','--show-toplevel'],capture_output=True,text=True)
        repo=str(Path(git.stdout.strip()).resolve()) if git.returncode==0 else str(Path.cwd().resolve())
        key=w.hashlib.sha256(repo.encode()).hexdigest()[:20]
        with (root/f'repo-{key}.lock').open('a') as h:
            w.fcntl.flock(h,w.fcntl.LOCK_EX|w.fcntl.LOCK_NB)
            self.assertEqual(self.invoke(args=['--read-only'])[0],75)
        self.assertFalse(self.calls.exists())

    def test_wrong_directory_is_rejected(self):
        code, out, err = self.invoke('wrongdir')
        self.assertEqual(code, 4)
        self.assertEqual(out, '')
        self.assertIn('wrong directory', err)

    def test_timeout_kills_process_group(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            w.run_process([sys.executable,'-c','import time; time.sleep(30)'],dict(os.environ),
                          self.root/'out',self.root/'err',0.1)


if __name__=='__main__':unittest.main()
