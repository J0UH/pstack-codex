"""Real signals at subprocess ownership boundaries; no provider processes."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
WRAPPER = r'''
import json,os,signal,sys,time
from pathlib import Path
sys.path.insert(0, os.environ['WORKER_MODULES'])
import worker_common as worker

root=Path(os.environ['CASE_DIR'])
window=os.environ['STOP_WINDOW']
behavior=os.environ['CHILD_BEHAVIOR']
def pause():
    (root/'ready').write_text(window)
    while not (root/'release').exists(): time.sleep(0.01)
def child_ready():
    marker='heartbeat' if behavior=='heartbeat' else 'child.pid'
    deadline=time.monotonic()+5
    while not (root/marker).exists():
        if time.monotonic()>deadline: raise RuntimeError('child did not start')
        time.sleep(0.01)

if window=='claim':
    original=worker.claim_run_dir
    def claim(path):
        original(path)
        pause()
    worker.claim_run_dir=claim
elif window=='hash':
    original=worker.sha256_file
    def hash_file(path):
        result=original(path)
        pause()
        return result
    worker.sha256_file=hash_file
elif window=='output_file':
    original=worker.create_restricted
    def create_file(path):
        result=original(path)
        if path.endswith('stdout.jsonl'): pause()
        return result
    worker.create_restricted=create_file
elif window=='popen':
    original=worker.subprocess.Popen
    def slow_popen(*args,**kwargs):
        proc=original(*args,**kwargs)
        child_ready()
        pause()
        return proc
    worker.subprocess.Popen=slow_popen
elif window in ('process_record','receipt'):
    original=worker.atomic_write_json
    paused=False
    def write_record(path,payload):
        global paused
        original(path,payload)
        matches=(window=='process_record' and path.endswith('process.json') and payload.get('stage')=='spawned') or (window=='receipt' and path.endswith('receipt.json'))
        if matches and not paused:
            paused=True
            if window=='process_record': child_ready()
            pause()
    worker.atomic_write_json=write_record
    if window=='receipt' and behavior=='heartbeat':
        # Start the timeout clock only once the child is heartbeating and ignoring TERM,
        # so the attempt ends by a real timeout before its receipt is written.
        supervise_original=worker._supervise
        def supervise(*args,**kwargs):
            child_ready()
            return supervise_original(*args,**kwargs)
        worker._supervise=supervise
elif window in ('supervise','ignored_hup'):
    original=worker._supervise
    def supervise(*args,**kwargs):
        child_ready()
        (root/'ready').write_text(window)
        return original(*args,**kwargs)
    worker._supervise=supervise
elif window=='timeout_grace':
    # Pause inside the timeout termination sequence, after TERM reached the group.
    original=worker._signal_group
    def signal_group(pgid,pid,signum):
        original(pgid,pid,signum)
        if signum==signal.SIGTERM and not (root/'ready').exists(): pause()
    worker._signal_group=signal_group
elif window=='restore':
    # Pause after the receipt is final but before the launcher's handlers are removed.
    original=worker._SignalGuard.restore
    def restore(self):
        if not (root/'ready').exists(): pause()
        original(self)
    worker._SignalGuard.restore=restore

def disposition():
    return 'ignored' if signal.getsignal(signal.SIGHUP)==signal.SIG_IGN else 'default'
if window!='ignored_hup':
    # Keep the handled-signal cases independent of a nohup-style test runner.
    signal.signal(signal.SIGHUP,signal.SIG_DFL)
(root/'hup_before').write_text(disposition())

def parse(events,spec):
    result=events[-1] if events else {}
    return {'result_text':result.get('text',''),'observed_models':[result['model']] if result.get('model') else [],
            'is_error':False,'complete':bool(events)}

spec={'backend':'claude','model':'fixture','effort':'high','profile':'analysis',
      'cwd':str(root/'project'),'prompt_file':str(root/'prompt.txt'),'run_dir':str(root/'run'),
      'timeout_seconds':float(os.environ['TIMEOUT_SECONDS']),'term_grace_seconds':0.1}
command=[sys.executable,str(root/'child.py')]
if behavior=='missing_executable':
    # A real spawn failure: Popen raises because the executable does not exist, so no child ever runs.
    command=[str(root/'missing-executable'),str(root/'child.py')]
receipt=worker.run_process(spec,command,parse,stdin_text='fixture',
                           env={'PATH':os.defpath,'CASE_DIR':str(root),'STOP_WINDOW':window,'CHILD_BEHAVIOR':behavior})
(root/'hup_after').write_text(disposition())
print(json.dumps(receipt),flush=True)
raise SystemExit(receipt['exit_code'])
'''

CHILD = r'''
import json,os,signal,time
from pathlib import Path
root=Path(os.environ['CASE_DIR'])
signal.signal(signal.SIGTERM,signal.SIG_IGN)
(root/'child.pid').write_text(str(os.getpid()))
behavior=os.environ['CHILD_BEHAVIOR']
if behavior=='complete_on_release':
    while not (root/'release').exists(): time.sleep(0.01)
if behavior in ('complete','complete_on_release'):
    print(json.dumps({'model':'fixture','text':'complete'}),flush=True)
else:
    for count in range(1500):
        (root/'heartbeat').write_text(str(count))
        time.sleep(0.02)
'''


def ignore_hangup():
    signal.signal(signal.SIGHUP, signal.SIG_IGN)


@unittest.skipUnless(os.name == "posix", "requires POSIX process groups")
class WorkerSignalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pstack signals ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.cases = 0

    def new_case(self):
        self.cases += 1
        root = self.base / f"case {self.cases}"
        (root / 'project').mkdir(parents=True)
        (root / 'prompt.txt').write_text('Synthetic signal fixture')
        (root / 'wrapper.py').write_text(WRAPPER)
        (root / 'child.py').write_text(CHILD)
        return root

    def wrapper_env(self, root, window, child_behavior, timeout):
        return {'PATH': os.defpath, 'WORKER_MODULES': str(SCRIPTS), 'CASE_DIR': str(root), 'STOP_WINDOW': window,
                'CHILD_BEHAVIOR': child_behavior, 'TIMEOUT_SECONDS': timeout}

    def start_wrapper(self, root, window, child_behavior='heartbeat', timeout='10', preexec_fn=None):
        proc = subprocess.Popen([sys.executable, str(root / 'wrapper.py')], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=self.wrapper_env(root, window, child_behavior, timeout),
                                start_new_session=True, preexec_fn=preexec_fn)
        self.addCleanup(self.stop_wrapper, root, proc)
        return proc

    def stop_wrapper(self, root, proc):
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=3)
        proc.stdout.close()
        proc.stderr.close()
        if (root / 'child.pid').exists():
            try:
                os.killpg(int((root / 'child.pid').read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass

    def wait_for_window(self, root, proc, window):
        deadline = time.monotonic() + 5
        while not (root / 'ready').exists():
            if proc.poll() is not None:
                stdout, stderr = proc.communicate()
                self.fail(f'wrapper exited before test window: {stdout} {stderr}')
            if time.monotonic() > deadline:
                self.fail(f'wrapper never reached {window}')
            time.sleep(0.01)

    def signal_and_release(self, root, proc, stop_signal):
        os.kill(proc.pid, stop_signal)
        # Wait for the signal handler before releasing the deferred window.
        time.sleep(0.03)
        (root / 'release').write_text('continue')
        return proc.communicate(timeout=5)

    def durable_receipt(self, root):
        return json.loads((root / 'run/receipt.json').read_text())

    def assert_group_stopped(self, root, durable):
        self.assertTrue(durable['termination']['kill_sent'], 'fixture ignores TERM')
        heartbeat = (root / 'heartbeat').read_text()
        time.sleep(0.12)
        self.assertEqual(heartbeat, (root / 'heartbeat').read_text())
        with self.assertRaises(ProcessLookupError):
            os.killpg(durable['pgid'], 0)

    def assert_claim_retained(self, root):
        before = (root / 'run/receipt.json').read_bytes()
        again = subprocess.run([sys.executable, str(root / 'wrapper.py')], text=True, capture_output=True,
                               env=self.wrapper_env(root, 'supervise', 'heartbeat', '10'), timeout=3)
        self.assertNotEqual(0, again.returncode)
        self.assertIn('already exists', again.stderr)
        self.assertEqual(before, (root / 'run/receipt.json').read_bytes())

    def run_interruption(self, window, stop_signal=signal.SIGTERM):
        root = self.new_case()
        proc = self.start_wrapper(root, window, 'complete' if window == 'receipt' else 'heartbeat')
        self.wait_for_window(root, proc, window)
        stdout, stderr = self.signal_and_release(root, proc, stop_signal)
        self.assertEqual(130, proc.returncode, stderr)
        public = json.loads(stdout)
        durable = self.durable_receipt(root)
        self.assertEqual(public, durable)
        self.assertEqual('interrupted', durable['status'])
        self.assertEqual('interrupted', durable['lifecycle'])
        self.assertEqual(signal.Signals(stop_signal).name, durable['termination']['interrupt_signal'])
        self.assertTrue(durable['confirmed_terminated'])
        self.assertFalse(durable['requested_model_verified'])
        self.assertEqual(0o600, (root / 'run/receipt.json').stat().st_mode & 0o777)
        if window in ('claim', 'hash', 'output_file'):
            self.assertIsNone(durable['pid'])
            self.assertFalse((root / 'child.pid').exists(), 'stop before launch must not spawn a child')
        elif window != 'receipt':
            self.assert_group_stopped(root, durable)
        # Every completed interrupted attempt remains exclusively claimed.
        self.assert_claim_retained(root)

    def test_real_term_int_and_hup_while_supervising(self):
        for stop_signal in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with self.subTest(signal=stop_signal):
                self.run_interruption('supervise', stop_signal)

    def test_interruption_immediately_after_exclusive_claim(self):
        self.run_interruption('claim')

    def test_interruption_while_hashing_prompt(self):
        self.run_interruption('hash', signal.SIGINT)

    def test_interruption_while_creating_output_files(self):
        self.run_interruption('output_file', signal.SIGHUP)

    def test_signal_during_popen_return_window_does_not_orphan_child(self):
        self.run_interruption('popen')

    def test_signal_during_process_record_write(self):
        self.run_interruption('process_record')

    def test_signal_during_receipt_write_is_returned_and_persisted(self):
        self.run_interruption('receipt')

    def test_stop_signal_during_timeout_termination_keeps_the_timeout_cause(self):
        root = self.new_case()
        proc = self.start_wrapper(root, 'timeout_grace', 'heartbeat', timeout='0.5')
        self.wait_for_window(root, proc, 'timeout_grace')
        stdout, stderr = self.signal_and_release(root, proc, signal.SIGTERM)
        self.assertEqual(124, proc.returncode, stderr)
        durable = self.durable_receipt(root)
        self.assertEqual(json.loads(stdout), durable)
        self.assertEqual('timeout', durable['status'])
        self.assertEqual('timeout', durable['lifecycle'])
        self.assertTrue(durable['confirmed_terminated'])
        self.assertTrue(durable['termination']['term_sent'])
        self.assertIsNone(durable['termination']['interrupt_signal'])
        self.assertEqual('SIGTERM', durable['termination']['stop_signal_after_termination'])
        self.assertTrue(any(error.startswith('timeout:') for error in durable['errors']), durable['errors'])
        self.assertTrue(any(error.startswith('stop_signal:') and 'SIGTERM' in error for error in durable['errors']),
                        durable['errors'])
        self.assertFalse(any(error.startswith('interrupted:') for error in durable['errors']), durable['errors'])
        self.assertEqual('finished', json.loads((root / 'run/process.json').read_text())['stage'])
        self.assert_group_stopped(root, durable)
        self.assert_claim_retained(root)

    def assert_cause_retained_through_finalization(self, root, stdout, cause, exit_code):
        """The first stop signal arrived during the receipt write, after the attempt had ended by ``cause``."""
        durable = self.durable_receipt(root)
        self.assertEqual(json.loads(stdout), durable)
        self.assertEqual(cause, durable['status'])
        self.assertEqual(cause, durable['lifecycle'])
        self.assertEqual(exit_code, durable['exit_code'])
        self.assertTrue(durable['confirmed_terminated'])
        self.assertFalse(durable['requested_model_verified'])
        self.assertIsNone(durable['termination']['interrupt_signal'])
        self.assertEqual('SIGTERM', durable['termination']['stop_signal_after_termination'])
        self.assertEqual([], durable['termination']['late_parent_signals'])
        self.assertTrue(any(error.startswith(f'{cause}:') for error in durable['errors']), durable['errors'])
        self.assertTrue(any(error.startswith('stop_signal:') and 'SIGTERM' in error and cause in error
                            for error in durable['errors']), durable['errors'])
        self.assertFalse(any(error.startswith('interrupted:') for error in durable['errors']), durable['errors'])
        self.assertFalse(any(warning.startswith('late_parent_signals') for warning in durable['warnings']),
                         durable['warnings'])
        self.assertEqual(0o600, (root / 'run/receipt.json').stat().st_mode & 0o777)
        return durable

    def test_stop_signal_during_receipt_write_after_timeout_keeps_the_timeout_cause(self):
        root = self.new_case()
        proc = self.start_wrapper(root, 'receipt', 'heartbeat', timeout='0.1')
        self.wait_for_window(root, proc, 'receipt')
        stdout, stderr = self.signal_and_release(root, proc, signal.SIGTERM)
        self.assertEqual(124, proc.returncode, stderr)
        durable = self.assert_cause_retained_through_finalization(root, stdout, 'timeout', 124)
        self.assertTrue(durable['termination']['term_sent'])
        process = json.loads((root / 'run/process.json').read_text())
        self.assertEqual('finished', process['stage'])
        self.assertEqual('timeout', process['lifecycle'])
        self.assertEqual('SIGTERM', process['termination']['stop_signal_after_termination'])
        self.assertIsNone(process['termination']['interrupt_signal'])
        self.assert_group_stopped(root, durable)
        self.assert_claim_retained(root)

    def test_stop_signal_during_receipt_write_after_spawn_failure_keeps_the_spawn_failed_cause(self):
        root = self.new_case()
        proc = self.start_wrapper(root, 'receipt', 'missing_executable')
        self.wait_for_window(root, proc, 'receipt')
        stdout, stderr = self.signal_and_release(root, proc, signal.SIGTERM)
        self.assertEqual(1, proc.returncode, stderr)
        durable = self.assert_cause_retained_through_finalization(root, stdout, 'spawn_failed', 1)
        # The cause is the real Popen failure, not the pre-spawn stop path that never calls Popen.
        self.assertTrue(any('FileNotFoundError' in error for error in durable['errors']), durable['errors'])
        self.assertIsNone(durable['pid'])
        self.assertIsNone(durable['pgid'])
        self.assertIsNone(durable['returncode'])
        self.assertFalse(durable['termination']['term_sent'])
        self.assertTrue((root / 'run/launch.json').is_file())
        self.assertFalse((root / 'run/process.json').exists(), 'no child was spawned')
        self.assertFalse((root / 'child.pid').exists(), 'no child was spawned')
        self.assert_claim_retained(root)

    def test_signal_after_receipt_is_final_is_reported_not_dropped(self):
        root = self.new_case()
        proc = self.start_wrapper(root, 'restore', 'complete')
        self.wait_for_window(root, proc, 'restore')
        stdout, stderr = self.signal_and_release(root, proc, signal.SIGTERM)
        self.assertEqual(0, proc.returncode, stderr)
        durable = self.durable_receipt(root)
        self.assertEqual(json.loads(stdout), durable)
        self.assertEqual('success', durable['status'])
        self.assertEqual('exited', durable['lifecycle'])
        self.assertTrue(durable['requested_model_verified'])
        self.assertIsNone(durable['termination']['interrupt_signal'])
        self.assertEqual(['SIGTERM'], durable['termination']['late_parent_signals'])
        self.assertTrue(any(warning.startswith('late_parent_signals: SIGTERM') for warning in durable['warnings']),
                        durable['warnings'])
        self.assertEqual([], durable['errors'])
        self.assert_claim_retained(root)

    def test_inherited_ignored_hangup_stays_ignored(self):
        root = self.new_case()
        proc = self.start_wrapper(root, 'ignored_hup', 'complete_on_release', preexec_fn=ignore_hangup)
        self.wait_for_window(root, proc, 'ignored_hup')
        self.assertEqual('ignored', (root / 'hup_before').read_text(), 'fixture did not inherit SIG_IGN')
        stdout, stderr = self.signal_and_release(root, proc, signal.SIGHUP)
        self.assertEqual(0, proc.returncode, stderr)
        durable = self.durable_receipt(root)
        self.assertEqual(json.loads(stdout), durable)
        self.assertEqual('success', durable['status'])
        self.assertEqual('exited', durable['lifecycle'])
        self.assertIsNone(durable['termination']['interrupt_signal'])
        self.assertEqual([], durable['termination']['late_parent_signals'])
        self.assertEqual(['SIGHUP'], durable['termination']['ignored_parent_signals'])
        self.assertEqual('ignored', (root / 'hup_after').read_text(), 'launcher replaced the inherited SIG_IGN')
        self.assertEqual('complete', (root / 'run/result.txt').read_text())


if __name__ == '__main__':
    unittest.main()
