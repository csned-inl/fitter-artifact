"""Run the unchanged full validation commands in isolated memory-limited units."""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tarfile
import time
import threading
import urllib.request


def run_batch(rows, execute, *, workers, memory_gb):
    """Schedule separate processes, charging each task its memory reservation."""
    if workers < 1 or memory_gb < 1:
        raise ValueError('positive worker and aggregate memory limits required')
    if any(not 0 < row['memory_gb'] <= memory_gb for row in rows):
        raise ValueError('task reservation exceeds the aggregate memory budget')
    pending = list(rows)
    active = {}
    results = []
    used = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while pending or active:
            for row in list(pending):
                if len(active) == workers:
                    break
                if used + row['memory_gb'] <= memory_gb:
                    active[pool.submit(execute, row)] = row['memory_gb']
                    used += row['memory_gb']
                    pending.remove(row)
            done, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in done:
                used -= active.pop(future)
                results.append(future.result())
    return results


def main():
    assert socket.gethostname() == 'kubuntu-workstation', 'workstation-only validation'
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', nargs='+', help='run exactly these test commands, without replay or pipeline')
    parser.add_argument('--jobs', type=int, default=len(os.sched_getaffinity(0)))
    args = parser.parse_args()
    root = Path.cwd()
    python = str(root/'.venv/bin/python')
    # All test processes and their solver children inherit ONE memory cgroup.
    # The scope stays in the Harness process group so cancellation kills it all.
    if os.environ.get('CLARITY_VALIDATION_SCOPE') != '1':
        command = ['systemd-run', '--user', '--scope', '--collect',
                   '--unit=clarity-validation-'+str(os.getpid())+'.scope',
                   '-p', 'MemoryMax=64G', '-p', 'MemorySwapMax=0',
                   'env', 'CLARITY_VALIDATION_SCOPE=1', 'PYTHONPATH='+str(root/'src'),
                   'PYTHONDONTWRITEBYTECODE=1', 'OPENBLAS_NUM_THREADS=1', 'OMP_NUM_THREADS=1',
                   python, str(Path(__file__).resolve()), *sys.argv[1:]]
        raise SystemExit(subprocess.call(command))
    cgroup = next(line.split(':', 2)[2] for line in Path('/proc/self/cgroup').read_text().splitlines()
                  if line.startswith('0::'))
    control = Path('/sys/fs/cgroup')/cgroup.lstrip('/')
    assert (control/'memory.max').read_text().strip() == str(64*1024**3)
    assert (control/'memory.swap.max').read_text().strip() == '0'
    out = root/'outputs'/os.environ.get('CLARITY_VALIDATION_DIRECTORY',
                                      'validation_'+time.strftime('%Y%m%d_%H%M%S'))
    out.mkdir(parents=True, exist_ok=False)
    (out/'tests').mkdir()
    rows = json.loads((root/'tests/workstation_test_matrix.json').read_text())
    selected = set(args.only or [row['name'] for row in rows])
    unknown = selected - {row['name'] for row in rows}
    if unknown:
        raise ValueError('unknown tests: '+str(sorted(unknown)))
    rows = [row for row in rows if row['name'] in selected]
    report = {'host': socket.gethostname(), 'commit': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], text=True).strip(), 'tests': [],
        'workers': args.jobs, 'aggregate_memory_gib': 64, 'swap_bytes': 0}
    lock = threading.Lock()

    def run(name, command, extra_env=()):
        started = time.monotonic()
        log = out/(name+'.log')
        env = dict(os.environ, CLARITY_SOURCE_SOLVER_PROGRESS='1')
        env.update(item.split('=', 1) for item in extra_env)
        print('VALIDATION_START '+name, flush=True)
        with log.open('w') as stream:
            result = subprocess.call(command, stdout=stream, stderr=subprocess.STDOUT, env=env)
        record = {'name': name, 'command': command, 'exit_code': result,
                  'seconds': round(time.monotonic()-started, 3), 'log': str(log)}
        with lock:
            report['tests'].append(record)
            (out/'summary.json').write_text(json.dumps(report, indent=2)+'\n')
            print(json.dumps(record), flush=True)
            if result:
                print(log.read_text(errors='replace')[-12000:], flush=True)
        return record

    def execute(row):
        command = [argument.replace('outputs/compact_lazy_validation/',
                   str(out.relative_to(root))+'/') for argument in row['command']]
        return run(row['name'], [python, *command])

    # Independent suites overlap. Expensive dependants start only after their
    # prerequisites pass, so a known compiler failure cannot trigger training.
    for phase in sorted({row['phase'] for row in rows}):
        group = [row for row in rows if row['phase'] == phase]
        results = run_batch(group, execute, workers=args.jobs, memory_gb=60)
        if any(row['exit_code'] for row in results):
            print('VALIDATION_STOP failed phase '+str(phase), flush=True)
            return 1
    if args.only:
        return 0

    archive=out/'saved-policies.tar.gz'
    archive_hash='631d57df54d28817354388e6cb91c7433e4e37db56460c8c44665b12caf75df5'
    cached=list((root.parent/'clarity-compact-lazy'/'outputs').glob('*/saved-policies.tar.gz'))
    data=None
    for candidate in cached:
        candidate_data=candidate.read_bytes()
        if hashlib.sha256(candidate_data).hexdigest()==archive_hash:
            data=candidate_data
            break
    if data is None:
        data=urllib.request.urlopen('http://100.102.47.111:8877/saved-policies.tar.gz',timeout=30).read()
    assert hashlib.sha256(data).hexdigest()==archive_hash
    archive.write_bytes(data)
    with tarfile.open(archive) as tar:tar.extractall(out/'saved-policies',filter='data')
    run('saved_policy_replay',[python,'tests/training/validate_source_equation_replay.py',
        '--saved-runs',str(out/'saved-policies'),'--out-dir',str(out/'replay')])
    if report['tests'][-1]['exit_code']:
        return 1
    run('full_pipeline',['bash','run_fitting_sequence.sh','--dt','0.1','--training-jobs','6',
        '--collection-backend','auto','--collection-workers-per-job','3','--collection-start-method','spawn',
        '--override-tolerance','0.01','--discretization-optimization-timeout-ms','250',
        '--discretization-smt-timeout-ms','30000'],
        ['PYTHON_BIN='+python,'OUT_DIR='+str(out/'full')])
    print('VALIDATION_SUMMARY '+json.dumps(report),flush=True)
    return int(any(row['exit_code'] for row in report['tests']))


if __name__ == '__main__':
    raise SystemExit(main())
