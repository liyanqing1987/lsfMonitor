# -*- coding: utf-8 -*-
################################
# File Name   : run_executor.py
# Author      : liyanqing.1987
# Created On  : 2026-08-24
# Description : Background executor for the Run panel. Runs a shell command across
#               many hosts concurrently with per-host timeout, emitting signals
#               as each host finishes so the GUI can stream results. Assumes ssh
#               passwordless access is already configured on the target hosts.
################################

import shlex
import threading
import time

from PyQt5.QtCore import QThread, pyqtSignal

try:
    import pexpect

    HAS_PEXPECT = True
except ImportError:
    pexpect = None
    HAS_PEXPECT = False


def _ssh_command(host, command, ssh_command_base):
    """Build the ssh command string used to run `command` on `host`.

    ``ssh_command_base`` is the configurable base ssh command from config_run.py
    (default_ssh_command). It already includes ConnectTimeout (same as the run executor).
    The host and the shlex-quoted command are appended.
    """
    return f'{ssh_command_base} {host} ' + shlex.quote(command)


def _run_on_host(host, command, timeout, ssh_command_base):
    """
    Run `command` on `host` via ssh+pexpect.

    Returns a result dict: {host, output, status, exit_code, duration}.
    status is one of: OK / TIMEOUT / FAIL / UNREACH / NO_PEXPECT.
    """
    start = time.time()
    result = {
        'host': host,
        'output': '',
        'status': 'FAIL',
        'exit_code': -1,
        'duration': 0.0,
    }

    if not HAS_PEXPECT:
        result['status'] = 'NO_PEXPECT'
        result['output'] = 'pexpect is not installed.'
        result['duration'] = time.time() - start

        return result

    cmd = _ssh_command(host, command, ssh_command_base)

    child = None

    try:
        child = pexpect.spawn(cmd, timeout=timeout, use_poll=True, encoding='utf-8')
        child.expect(pexpect.EOF)
        output = child.before or ''
        child.close()
        result['output'] = output.rstrip()
        result['exit_code'] = child.exitstatus if child.exitstatus is not None else -1
        result['status'] = 'OK' if child.exitstatus == 0 else 'FAIL'
    except pexpect.exceptions.TIMEOUT:
        result['status'] = 'TIMEOUT'
        result['output'] = f'Timeout after {timeout}s.'
    except pexpect.exceptions.EOF:
        # ssh exited without producing the expected EOF marker (e.g. unreachable).
        result['status'] = 'UNREACH'
        result['output'] = 'Host unreachable or ssh failed to connect.'
    except Exception as error:
        result['status'] = 'FAIL'
        result['output'] = f'{type(error).__name__}: {error}'
    finally:
        if child is not None and child.isalive():
            child.close(force=True)

        result['duration'] = time.time() - start

    return result


class RunThread(QThread):
    """
    Run a shell command across a list of hosts concurrently.

    Signals:
        host_finished(str, dict): (host, result) emitted as each host completes.
        progress(int, int): (done, total) emitted as each host completes.
        all_finished(): emitted when every host has been processed (or stopped).
    """

    host_finished = pyqtSignal(str, dict)
    progress = pyqtSignal(int, int)
    all_finished = pyqtSignal()

    def __init__(self, hosts_list, command, timeout, max_parallel, ssh_targets=None, ssh_command_base=None, parent=None):
        super().__init__(parent)
        self.hosts_list = list(hosts_list)
        self.command = command
        self.timeout = timeout
        self.max_parallel = max(1, int(max_parallel))
        # Configurable base ssh command from config_run.py (default_ssh_command).
        self.ssh_command_base = ssh_command_base or 'ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o GSSAPIAuthentication=no -t -q'
        # {display_name: ssh_connect_addr}. When a host is absent, ssh to the
        # display name itself (hostname sourced from LSF has no separate addr).
        self.ssh_targets = ssh_targets or {}
        self._lock = threading.Lock()
        self._done = 0

    def run(self):
        total = len(self.hosts_list)

        if total == 0:
            self.all_finished.emit()

            return

        semaphore = threading.Semaphore(self.max_parallel)
        threads_list = []

        for host in self.hosts_list:
            semaphore.acquire()

            t = threading.Thread(target=self._worker, args=(host, semaphore))
            t.start()
            threads_list.append(t)

        for t in threads_list:
            t.join()

        self.all_finished.emit()

    def _worker(self, host, semaphore):
        try:
            # Connect to the ssh target (IP) but report results under the
            # display name (hostname) so the GUI row matches the Host column.
            ssh_target = self.ssh_targets.get(host, host)
            result = _run_on_host(ssh_target, self.command, self.timeout, self.ssh_command_base)
            result['host'] = host

            with self._lock:
                self._done += 1
                done = self._done

            self.host_finished.emit(host, result)
            self.progress.emit(done, len(self.hosts_list))
        finally:
            semaphore.release()
