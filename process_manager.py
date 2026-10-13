"""Track only the demo processes we launch, and report failures with log paths."""
import subprocess
import time


class ProcessManager:
    def __init__(self):
        self.jobs = {}

    def start(self, name, host, argv, log_path, persistent=True):
        if name in self.jobs:
            raise ValueError(f'process already tracked: {name}')
        log = open(log_path, 'w')
        try:
            process = host.popen(argv, stdout=log, stderr=subprocess.STDOUT)
        except Exception:
            log.close()
            raise
        self.jobs[name] = (process, log, log_path, persistent)
        self.wait(0.2)  # surface common startup errors before launching traffic
        return process

    def check(self):
        for name, (process, _, path, persistent) in self.jobs.items():
            code = process.poll()
            if code is not None and (persistent or code != 0):
                raise RuntimeError(f'{name} exited with status {code}; see {path}')

    def wait(self, seconds):
        deadline = time.monotonic() + seconds
        while True:
            self.check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.2))

    def stop(self, name):
        job = self.jobs.pop(name, None)
        if job is None:
            return
        process, log, _, _ = job
        try:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        finally:
            log.close()

    def close(self):
        errors = []
        for name in reversed(list(self.jobs)):
            try:
                self.stop(name)
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError('Failed to stop a demo process') from errors[0]
