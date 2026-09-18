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
import json,os,sys,time
from pathlib import Path
sys.path.insert(0, os.environ['WORKER_MODULES'])
import worker_common as worker

root=Path(os.environ['CASE_DIR'])
window=os.environ['STOP_WINDOW']
def pause():
    (root/'ready').write_text(window)
    while not (root/'release').exists(): time.sleep(0.01)
def child_ready():
    deadline=time.monotonic()+5
    while not (root/'heartbeat').exists():
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
elif window=='supervise':
    original=worker._supervise
    def supervise(*args,**kwargs):
        child_ready()
        (root/'ready').write_text(window)
        return original(*args,**kwargs)
    worker._supervise=supervise

def parse(events,spec):
    result=events[-1] if events else {}
    return {'result_text':result.get('text',''),'observed_models':[result['model']] if result.get('model') else [],
            'is_error':False,'complete':bool(events)}

spec={'backend':'claude','model':'fixture','effort':'high','profile':'analysis',
      'cwd':str(root),'prompt_file':str(root/'prompt.txt'),'run_dir':str(root/'run'),
      'timeout_seconds':10,'term_grace_seconds':0.1}
receipt=worker.run_process(spec,[sys.executable,str(root/'child.py')],parse,stdin_text='fixture',
                           env={'PATH':os.defpath,'CASE_DIR':str(root),'STOP_WINDOW':window})
print(json.dumps(receipt),flush=True)
raise SystemExit(receipt['exit_code'])
'''

CHILD = r'''
import json,os,signal,time
from pathlib import Path
root=Path(os.environ['CASE_DIR'])
signal.signal(signal.SIGTERM,signal.SIG_IGN)
(root/'child.pid').write_text(str(os.getpid()))
if os.environ['STOP_WINDOW']=='receipt':
    print(json.dumps({'model':'fixture','text':'complete'}),flush=True)
else:
    for count in range(1500):
        (root/'heartbeat').write_text(str(count))
        time.sleep(0.02)
'''


@unittest.skipUnless(os.name == "posix", "requires POSIX process groups")
class WorkerSignalTests(unittest.TestCase):
    def run_interruption(self, window, stop_signal=signal.SIGTERM):
        with tempfile.TemporaryDirectory(prefix="pstack signals ") as directory:
            root=Path(directory).resolve()
            (root/'prompt.txt').write_text('Synthetic signal fixture')
            (root/'wrapper.py').write_text(WRAPPER)
            (root/'child.py').write_text(CHILD)
            proc=subprocess.Popen([sys.executable,str(root/'wrapper.py')],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                  text=True,env={'PATH':os.defpath,'WORKER_MODULES':str(SCRIPTS),
                                                 'CASE_DIR':str(root),'STOP_WINDOW':window},start_new_session=True)
            child_pid=None
            try:
                deadline=time.monotonic()+5
                while not (root/'ready').exists():
                    if proc.poll() is not None:
                        stdout,stderr=proc.communicate()
                        self.fail(f'wrapper exited before test window: {stdout} {stderr}')
                    if time.monotonic()>deadline:
                        self.fail(f'wrapper never reached {window}')
                    time.sleep(0.01)
                if (root/'child.pid').exists(): child_pid=int((root/'child.pid').read_text())
                os.kill(proc.pid,stop_signal)
                # Wait for the signal handler before releasing the deferred window.
                time.sleep(0.03)
                (root/'release').write_text('continue')
                stdout,stderr=proc.communicate(timeout=5)
                self.assertEqual(130,proc.returncode,stderr)
                public=json.loads(stdout)
                durable=json.loads((root/'run/receipt.json').read_text())
                self.assertEqual(public,durable)
                self.assertEqual('interrupted',durable['status'])
                self.assertEqual('interrupted',durable['lifecycle'])
                self.assertEqual(signal.Signals(stop_signal).name,durable['termination']['interrupt_signal'])
                self.assertTrue(durable['confirmed_terminated'])
                self.assertFalse(durable['requested_model_verified'])
                self.assertEqual(0o600,(root/'run/receipt.json').stat().st_mode & 0o777)
                if window in ('claim','hash','output_file'):
                    self.assertIsNone(durable['pid'])
                    self.assertFalse((root/'child.pid').exists(), 'stop before launch must not spawn a child')
                elif window!='receipt':
                    self.assertTrue(durable['termination']['kill_sent'], 'fixture ignores TERM')
                    heartbeat=(root/'heartbeat').read_text()
                    time.sleep(0.12)
                    self.assertEqual(heartbeat,(root/'heartbeat').read_text())
                    with self.assertRaises(ProcessLookupError):
                        os.killpg(durable['pgid'],0)
                # Every completed interrupted attempt remains exclusively claimed.
                before=(root/'run/receipt.json').read_bytes()
                again=subprocess.run([sys.executable,str(root/'wrapper.py')],text=True,capture_output=True,
                                     env={'PATH':os.defpath,'WORKER_MODULES':str(SCRIPTS),
                                          'CASE_DIR':str(root),'STOP_WINDOW':'supervise'},timeout=3)
                self.assertNotEqual(0,again.returncode)
                self.assertIn('already exists',again.stderr)
                self.assertEqual(before,(root/'run/receipt.json').read_bytes())
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid,signal.SIGKILL)
                    proc.wait(timeout=3)
                proc.stdout.close()
                proc.stderr.close()
                if child_pid is None and (root/'child.pid').exists():
                    child_pid=int((root/'child.pid').read_text())
                if child_pid is not None:
                    try: os.killpg(child_pid,signal.SIGKILL)
                    except ProcessLookupError: pass

    def test_real_term_int_and_hup_while_supervising(self):
        for stop_signal in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):
            with self.subTest(signal=stop_signal):
                self.run_interruption('supervise',stop_signal)

    def test_interruption_immediately_after_exclusive_claim(self):
        self.run_interruption('claim')

    def test_interruption_while_hashing_prompt(self):
        self.run_interruption('hash',signal.SIGINT)

    def test_interruption_while_creating_output_files(self):
        self.run_interruption('output_file',signal.SIGHUP)

    def test_signal_during_popen_return_window_does_not_orphan_child(self):
        self.run_interruption('popen')

    def test_signal_during_process_record_write(self):
        self.run_interruption('process_record')

    def test_signal_during_receipt_write_is_returned_and_persisted(self):
        self.run_interruption('receipt')


if __name__=='__main__':
    unittest.main()
