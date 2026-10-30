# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This repository is the Ansible playbook and roles for the **Épicerie Le Détour**'s infrastructure (a self-managed grocery store). It configures VPS/self-hosted Debian servers and services: multi-store Borg backups, Google Drive proxies, Wordpress, Grafana, Mattermost, and two custom Le Détour applications (member status checking, food voucher scanning). See `README.md` for the full rationale and core values (infra-as-code, self-hosted OSS, Google auth, Wireguard-only network access, HTTPS everywhere).

The setup is mid-migration ("Ansible setup upgrade project") — `playbook.yml` has a large commented-out block of the old setup at the bottom being replaced piece by piece. `TODO.md` tracks the current in-flight migration steps (notably migrating `vps2` → `vps`).

## Environment & commands

Use `uv` to run everything (Ansible, ansible-lint, molecule) — dependencies are pinned in `pyproject.toml`/`uv.lock`. Do not `pip install` ansible tools globally.

```sh
# Local dev/testing with libvirt VMs (safe — never touches production)
uv run molecule create        # create the VMs (pi1, srv1, vps2, b72) defined in molecule/default/molecule.yml
uv run molecule prepare       # prepare VMs for the playbook
uv run molecule converge      # run playbook.yml against the VMs
uv run molecule verify        # check that wordpress, vouchers, membres, grafana and mattermost work
uv run molecule converge -- --tags vouchers --tags borgmatic_create   # run with extra tags
uv run molecule login --host vps2
uv run molecule destroy

# Linting
uv run ansible-lint
uv run yamllint .

# Production (real servers, defined in inventory/hosts.yml)
ansible-playbook playbook.yml
ansible-playbook playbook.yml --limit charles-ws --tags workstations_networkd   # configure a workstation Wireguard connection (never runs by default, tag is "never")
ansible-playbook playbook.yml --limit charles-lp --tags workstations_nm         # same, through NetworkManager
ansible-playbook playbook.yml --tags <role>         # run one role/tag, e.g. --tags wordpress

# Vault
./vault_password.sh                 # decrypts the vault password (used internally by ansible.cfg's vault_password_file)
ansible-vault encrypt --vault-id @prompt secret.yml
ansible-vault view secret.yml
ansible-vault decrypt secret.yml
```

There is no unit test suite; **Molecule is the test harness**. It boots libvirt VMs from Debian 13 (trixie) cloud images and converges the real playbook against them via `molecule/default/converge.yml`, which sets `deployment_is_dev: true` before importing the root `playbook.yml`. `molecule/default/verify.yml` (run by `molecule verify`) checks the web services after a converge; add a `molecule/default/verify/<service>.yml` for each new web app.

Molecule inventory is linked directly to `inventory/groups.yml` (see `molecule/default/molecule.yml`), so VM hostnames (`pi1`, `srv1`, `vps2`, `b72`) must match group membership there. Molecule uses its own vault_password_file pointing at the same `vault_password.sh`, so vault-encrypted vars work identically in dev and prod.

### Testing web services after converge

All web services are additionally exposed as `https://<service>.localhost` on the webserver host itself. `molecule verify` relies on this: its `uri` tasks run on the VM, so no tunnel is needed. Each `verify/<service>.yml` records its failure instead of stopping, so one run lists every broken service.

To investigate by hand, tunnel port 443 from the VM:

```sh
ssh -N -L 443:localhost:443 -i ~/.ansible/tmp/molecule.*/id_ssh_rsa molecule@<vm-ip>
curl -kL https://wordpress.localhost
curl -kL https://vouchers.localhost
curl -skL https://membres.localhost/members.json | jq   # add --cert/--key roles/membres/files/client.crt|key for non-anonymized data
```

## Architecture

### Playbook structure (`playbook.yml`)

A sequence of plays, each scoped to an inventory group, applying roles tagged for selective runs:

1. **workstations_networkd** / **workstations_nm** (tag `never`, `workstation`) — `workstations_networkd` / `workstations_nm` roles configure the workstation's Wireguard connection to the `vps2` endpoint through systemd-networkd or NetworkManager; explicitly opt-in only, run with `--limit <workstation>` on the workstation itself (the `workstations` parent group in `inventory/hosts.yml` sets `ansible_connection: local`, never ssh).
2. **servers** — `authorized_keys`, `sshd` (tag `ssh`).
3. **servers** — `common_facts`, `common_packages`, `hostname`, `timezone`, `upgrade`, `ufw`, `wireguard`, `srv` (tag `common`). Sets `group_suffix: "-server"`.
4. **backup_destinations** — `borg_destination` (tag `backup`, `destination`).
5. **backup_sources** — `systemd_handlers`, `borgmatic`, `borgmatic_system` (tag `backup`, `source`).
6. **webservers** — `systemd_handlers`, `caddy_handlers`, `caddy`, `vouchers`, `membres`, `wordpress` (tag `webservers`).
7. **mattermost** — `caddy_handlers`, `caddy`, `mattermost` (tag `mattermost`): Bâtiment 7's chat.batiment7.org on `b72`, Team Edition with PostgreSQL. `/usr/local/sbin/mattermost-import` on the host loads another server's data (database dump, `config.json`, attachments), migrating it through the ESR versions in `mattermost_import_steps`.

Roles run in dependency order within each play; e.g. `caddy` must run before the site roles that register Caddy vsites and `notify: caddy changed`.

### Key cross-cutting patterns

- **`deployment_is_dev` / `deployment_is_prod` / `deployment_suffix`**: set once by `roles/common_facts` (defaulting `deployment_is_dev` to `false` unless Molecule's `converge.yml` sets it). Roles branch on these to install dev-only Caddy internal CA certs, etc. When adding new roles that behave differently in Molecule vs. production, follow this pattern rather than inventing a new flag.
- **`group_suffix`**: set per-play (`"-server"`) and used by the `wireguard` role to `include_tasks: "wireguard{{ group_suffix }}.yml"`, selecting a host-class-specific task file. Same technique to reuse for any role that needs materially different behavior per host class.
- **Handler pattern**: config-writing roles (`caddy`, `vouchers`, `membres`, etc.) `notify` shared handlers defined in small dedicated `*_handlers` roles (`caddy_handlers`, `systemd_handlers`) rather than each role owning its own handler — this lets multiple roles safely reload the same service (e.g. `caddy changed` → `systemd: name: caddy, state: reloaded`) without duplicate handler definitions.
- **Backup topology (Borg/borgmatic)**: `borg_destination` role provisions a `borg` user + repo storage on hosts in `backup_destinations`; `borgmatic`+`borgmatic_system` on `backup_sources` hosts creates SSH trust to every destination (`roles/borgmatic/tasks/destination.yml`, looped via `groups['backup_destinations']`, using `delegate_to`) and templates per-source borgmatic config. `borgmatic_snippet` is a reusable sub-role (`include_role`) that other backup roles (e.g. `borgmatic_system`) call with `borgmatic_snippet_name`/`borgmatic_snippet_file` set, to add another backup "set" (a systemd unit + optional service data dir) without duplicating the borgmatic config/repo-init/create/restore logic. `create`/`restore` are gated on `ansible_run_tags` containing `borgmatic_create`/`borgmatic_restore` (opt-in via `--tags`, not run by default).
- **Caddy vsites**: each web app role (wordpress, vouchers, membres) templates its own `/etc/caddy/sites.d/<app>.caddy` file (Caddyfile with `import sites.d/*.caddy`-style layout, implied) rather than one central vhost file — keep this per-role-owns-its-vsite convention for new web apps.
- **Inventory** (`inventory/hosts.yml` + `inventory/groups.yml`): `hosts.yml` maps inventory names to `ansible_host`/`ansible_user` (bootstrap connection info); `groups.yml` assigns hosts to functional groups (`servers`, `backup_destinations`, `backup_sources`, `webservers`, `vouchers`) and, in `hosts.yml`, `workstations_networkd`/`workstations_nm` that the playbook's plays target. A host can belong to multiple groups (e.g. `vps2` is a server, a backup source, and a webserver).
- **Vault-encrypted values** live inline in `group_vars`/`host_vars`/role `vars` files as `!vault |` blocks (see `roles/wordpress/vars/main.yml`), decrypted via `ansible.cfg`'s `vault_password_file=vault_password.sh`, which in turn calls `age -d` using the SSH private key matching the encrypter's public key in `keys/`.

### First-time production bootstrap

New production machines start with only SSH + passwordless sudo for a `debian` user; the very first `ansible-playbook` run must be pointed at the LAN IP explicitly (`-e "ansible_host=<ip> ansible_user=debian"`) because Wireguard (used for all subsequent connections) doesn't exist yet — see README "First setup of a production machine" for the full sequence. Expect a reboot for full-disk-encryption unlock on first run.

## Language convention

Internal docs (`REUNIONS.md`, `SCRUMS.md`, RFCs in `rfc/`) are in French per Le Détour's convention (private French, public English). This repository itself, its code, and its comments are in English since it's public. Don't translate one into the other.
