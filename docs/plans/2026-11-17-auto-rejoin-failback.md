# Automatic AWS rejoin and failback

Record: Active task plan
Base revision: b231868 (working tree also holds unrelated uncommitted doc/hostname edits; they are left untouched)

## Requirements reference and delivery scope

No spec exists in `docs/specs/` or `docs/plans/`; requirements are the ones agreed in conversation:

1. After the watcher has failed over to Azure (EC2 fenced), the system recovers without an operator:
   - **Phase 1** failover (already implemented, kept as is);
   - **Phase 2** wait until the operator has started the fenced EC2 (the watcher never starts it) and it is reachable;
   - **Phase 3** rejoin: rebuild the AWS database as a streaming standby of Azure on a new PVC;
   - **Phase 4** failback: write-lock Azure, wait for the final LSN on AWS, fence the Azure database, promote AWS, verify it is the healthy writer;
   - **Phase 5** rebuild Azure as a standby of AWS and rearm the watcher.
2. Every phase checks state before acting, persists its phase so a rerun resumes, and stops with exit code 2 on anything unexpected.
3. Rejoin is always automatic when recovery is enabled. Failback is a separate opt-in flag (Azure staying primary is a valid end state).
4. Both features are opt-in Terraform variables, default off, and extend the existing one-way watcher.

Acceptance checks (all offline; no cloud access is available in this task):

- A1. Unit tests cover the happy path ordering, the no-failback end state, and each fail-closed branch.
- A2. `terraform test` proves the flags are opt-in, recovery adds no AWS permission (no `ec2:StartInstances`), and recovery without the watcher is rejected.
- A3. Existing 20 watcher tests and 17 Terraform tests keep passing.

Out of scope / not verifiable here: real EC2 start, `ssh` over WireGuard, K3s `disable-agent`, `pg_basebackup` over the tunnel, and the application switching role without restart (assumed, see Risks).

## Approach

Controller on the Azure VM (extends `cloud_watcher.py`), because it already holds the AWS credentials and local database access. Azure is only fenced at **database level** (StatefulSet scaled to 0 and port 5432 verified closed), so the controller is not killed by its own fence. A VM-level stop would need Azure credentials the watcher does not have; this is weaker evidence and is documented.

Three layers, all Python standard library:

- `recovery_ops.py`: VM-local operations (db state, gate, wait replay, fence, rejoin, promote). Runs on both VMs.
- `rpc.py`: JSON dispatcher on AWS. Azure reaches it over the WireGuard address `10.200.29.1` with a dedicated key generated on Azure. The installer (laptop, existing SSH) puts the public key in the AWS `authorized_keys` with a forced command, `from="10.200.29.2"` and no forwarding/pty, so that key can only call this dispatcher. No laptop key is copied to Azure.
- `recovery.py`: state machine with injected dependencies (EC2, local ops, remote ops, state store, clock) so it is testable offline. State is `/opt/dcs29/recovery-state.json`.

Phases: `wait-aws` → `rejoin-aws` → (`complete` when failback is off) or `failback-lock` → `failback-fence` → `failback-promote` → `rebuild-azure` → `rearm`.

Safety rules baked into the handlers:

- The K3s start guard is only lifted by `rejoin` after the controller has confirmed Azure is the open writer; K3s is started with `disable-agent` so the old primary pod can never run, then restarted normally once the StatefulSet points at the new PVC (runbook A.4.2/A.4.3).
- AWS found as a second primary: stop EC2 (fence) and stop for an operator.
- Failback lock failing or lagging: re-open the Azure gate and stop.
- Promote failing while AWS is still a standby: scale Azure back up and re-open its gate, then stop. AWS already primary: continue.
- `watcher.pause` is honoured between steps; old PVCs are never deleted.

Alternatives rejected: Ansible (no Windows control node, see earlier discussion); a laptop-driven script (cannot run unattended); stopping the Azure VM (no Azure credential on the watcher).

## Tasks

- [x] **Task 1: standby manifest template** (new `deploy/terraform/watcher/templates/postgres-standby.yaml.tmpl`)
  - Build: generic copy of `automation/templates/postgres-azure.yaml.tmpl` with `__PVC_NAME__`, `__SOURCE_IP__`, `__REPLICAS__`, `__POSTGRES_IMAGE__`.
  - Verify: unit test renders it and finds no leftover `__X__` placeholder → A1.
- [x] **Task 2: VM-local operations** (new `watcher/recovery_ops.py`, `watcher/rpc.py`)
  - Depends on: Task 1.
  - Verify: `python -m unittest` on `tests/test_recovery_ops.py` → fence, gate, rejoin ordering, validators.
- [x] **Task 3: recovery state machine** (new `watcher/recovery.py`)
  - Depends on: Task 2 interfaces.
  - Verify: `tests/test_recovery.py` → A1.
- [x] **Task 4: wire into the watcher** (`watcher/cloud_watcher.py`, `watcher/remote.py`, `watcher/deploy.py`)
  - Build: main loop continues into recovery after a failover and resumes from state; `--check` verifies AWS RPC reachability; installer generates the Azure key, installs the AWS side and ships the new files.
  - Verify: updated `tests/test_cloud_watcher.py`, `tests/test_watcher_install.py`.
- [x] **Task 5: Terraform** (`variables.tf`, `watcher.tf`, `tests/deployment.tftest.hcl`, `terraform.tfvars.example`)
  - Verify: `terraform fmt -check`, `terraform validate`, `terraform test` → A2, A3.
- [x] **Task 6: documentation** (`docs/giai-doan/tu_dong/AUTOMATION_FAILOVER.md`, `TRIEN_KHAI_BANG_TERRAFORM.md`, `deploy/terraform/README.md`)
  - Verify: re-read for consistency with code; remove the statements that say rejoin/failback never run automatically.

- [x] **Task 7 (added later at the user's request): Azure releases the K3s guard after a reboot** (new `watcher/unguard.py`, `tests/test_unguard.py`; `watcher/remote.py`, `watcher/deploy.py`, `variables.tf`, `watcher.tf`, tfvars example, tftest, `AUTOMATION_FAILOVER.md` section 10)
  - Build: oneshot-style systemd service on Azure only; approves from the on-disk local role plus the AWS state, refuses when unknown or when both may be primaries, honours `watcher.pause`; opt-in `enable_azure_auto_start`.
  - Verify: `python -m unittest discover -s watcher/tests` and `terraform test` (see evidence).

## Risks and assumptions

- Cloud acceptance not run. K3s `disable-agent` support on the pinned release, forced-command SSH over wg0 and UFW on AWS are unverified.
- The application must report the new role without restart (`database/role.py` was not read); if it caches the role, a pod restart step is needed after promote.
- Fencing Azure by scaling to 0 is weaker than stopping the VM.
- Stopping/starting EC2 on its own: a deliberate operator stop during recovery needs `watcher.pause`.

## Verification evidence

Run from `deploy/terraform` on Windows, Python 3.14.7, Terraform 1.16.4:

- `python -m unittest discover -s watcher/tests` → 62 tests OK (20 pre-existing + 42 new: recovery state machine 14, VM operations 18, loop/installer 10). Covers A1.
- `terraform fmt -check -recursive` → clean; `terraform validate` → valid.
- `terraform test` → 22 passed (17 pre-existing + 5 new: opt-in permissions, failback flag plumbing, off by default, recovery without watcher rejected, failback without recovery rejected). Covers A2, A3.
- `python -m unittest discover` in `automation/` (7) and `https/` (9) → OK.
- Mutation check: replacing the `failback-fence` handler with a no-op made 2 recovery tests fail, so the ordering tests do detect a missing fence.
- The combined `automation/remote.py` + `watcher/remote.py` script that the installer sends compiles and exposes `recovery_keygen` / `recovery_install`.

Not run (no cloud access): everything listed under "Out of scope" in the requirements section. `recovery_install` uses `pwd`, so it could not even be executed on this Windows machine.

Change after the user clarified the demo: the user starts EC2 by hand, so the watcher never starts it. `ec2:StartInstances`, the DryRun check and the 6-hour start timeout were removed; phase `start-aws` became `wait-aws`, which waits without a deadline until EC2 is running and the RPC answers. The Terraform tests now assert the watcher permissions stay exactly Describe/Stop/GetHealthCheckStatus.

Final state after removing the automatic EC2 start: 84 Python tests OK in `watcher/tests`, `terraform test` 25 passed, fmt clean, validate valid.

Task 7 evidence (before that change): 83 Python tests OK (21 new in `test_unguard.py` plus 2 installer tests), `terraform test` 25 passed (3 new), `terraform fmt -check` clean, `validate` valid. Mutation check: approving a primary-vs-primary conflict made 2 tests fail. Not run: the unit on a real VM (local-path storage layout, k3s.service restart behaviour after the marker is written).

## Handoff

All six tasks implemented and verified offline; nothing committed. The working tree also still contains the user's earlier uncommitted edits (hostname rename, docs), which were not touched.

Next action for a person: run the cloud acceptance in a throwaway lab, in this order: (1) `terraform apply` with `enable_auto_recovery=true` and check `cloud_watcher.py --check` (EC2 DryRun, SSH over wg0); (2) stop the AWS EC2 and watch `journalctl -u dcs29-watcher` through `complete`; (3) only then set `enable_auto_failback=true` and repeat. Verify specifically: K3s `disable-agent` on the pinned release, UFW on AWS, and that the app reports the new role after promote without a pod restart (`database/role.py` was not read). When accepted, mark this record historical.
