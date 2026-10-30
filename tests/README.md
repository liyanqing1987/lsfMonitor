# lsfMonitor tests

A dependency-free test suite (Python standard library `unittest`) for the
lsfMonitor core, with a focus on the JSON CLI used by scripts and AI agents.

## Run

```bash
# from the repository root
./tests/run_tests.sh

# or directly
python3 -m unittest discover -s tests -v
```

`run_tests.sh` sets `LSFMONITOR_INSTALL_PATH` to the repository root, so no
installation is required for unit tests. CLI tests build a temporary install
tree and fake scheduler/FlexNet commands in a temporary `PATH`; they never
touch a real cluster and do not require the local machine to run LSF.

## What is covered

| File | Coverage |
|---|---|
| `test_common_lsf.py` | bjobs/bhosts/bqueues/busers/lsid/bmgroup/lsload parsers, exec-host parsing; LSF/volclava/OpenLava `lsid` detection, unit fallback (MB vs KB), UF parser dispatch and both UF formats |
| `test_common_sqlite3.py` | create/insert/query/update, read-only connection behavior |
| `test_common_license.py` | lmstat command construction |
| `test_installers.py` | `install.py` writes into the current directory; `--clean` removes generated tools and keeps directories |
| `test_bmonitor_cli.py` | end-to-end CLI JSON contract: cluster-info/jobs/hosts/queues/users/host-groups/job-output, invalid input, DB subcommands; cluster-info tool detection for LSF/volclava/OpenLava |

## Adding a fixture

CLI tests live in `test_bmonitor_cli.py`. Fake command outputs are plain shell
scripts under the temporary `fakebin/` directory created by
`support.make_install_tree(root, scheduler=...)`; pass `scheduler="lsf"`,
`"volclava"` or `"openlava"` to select the `lsid` fixture. Add an entry to
`make_fake_bin()`'s `outputs` dict (or a fixture in `support.py`) and assert on
the JSON returned by `bmonitor_cli`.

## Design notes

- No external test dependencies (unittest + unittest.mock only).
- Tests never call real `bjobs`/`bhosts`/`lmstat`; the fakebin directory is
  prepended to `PATH` only inside the temporary environment.
- All three supported schedulers are covered by fixtures: LSF, volclava and
  OpenLava. Tests do not assume the machine running them is an LSF client.
- The CLI is executed as a subprocess so stdout purity, exit codes and
  `common.bprint` redirection are tested exactly as users see them.
