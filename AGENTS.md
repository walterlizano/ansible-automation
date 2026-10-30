# Ansible Automation Repository

## Overview
This repository contains Ansible playbooks and roles for managing the Épicerie Le Détour's infrastructure. It uses a combination of Ansible, Molecule (for testing), and Vagrant (for development) with Debian-based systems.

## Key Commands
- `uv run molecule create` - Create virtual machines for testing
- `uv run molecule prepare` - Prepare virtual machines for development  
- `uv run molecule converge` - Run Ansible playbook on virtual machines
- `uv run molecule login --host vps2` - Login to a specific VM
- `uv run molecule destroy` - Destroy all virtual machines
- `ansible-playbook playbook.yml` - Run full playbook on production
- `ansible-playbook playbook.yml --limit <workstation> --tags workstations_networkd` (or `workstations_nm`) - Configure a workstation Wireguard connection
- `ansible-vault encrypt/view/decrypt secret.yml` - Manage encrypted files

## Architecture
- Uses Debian 13 (trixie) as base OS
- Molecule tests run in libvirt VMs via cloud images
- Vault password is GPG/age encrypted and named `.vault_password.d/encrypted-vault-password-for-username`
- Inventory groups: servers, backup_destinations, backup_sources, webservers, vouchers
- Roles for backup (borg), web services (caddy), membership management (membres), voucher scanning (vouchers), etc.

## Special Requirements
- Install system dependencies: age, build-essential, cloud-image-utils, qemu-kvm, libguestfs-tools, libvirt-daemon-system, libvirt-dev, pkg-config, python3-dev
- Add user to libvirt group: `sudo usermod --append --groups libvirt $USER && newgrp libvirt`
- Setup wireguard VPN connections for both servers and workstations
- Run `./vault_password.sh` to decrypt vault passwords when needed

## Development Workflow
1. Create VMs with `uv run molecule create`
2. Prepare VMs with `uv run molecule prepare`  
3. Run playbook locally with `uv run molecule converge`
4. Test services by setting up SSH tunneling (e.g., `ssh -N -L 443:localhost:443 molecule@10.10.10.152`)
5. Destroy VMs when done with `uv run molecule destroy`

## Vault Password Management
- Encrypted with age using your SSH private key
- Stored in `.vault_password.d/encrypted-vault-password-for-username`
- Scripts to manage encryption/decryption available in repository
- To add new administrators: Add ssh public key in `keys` folder and re-encrypt vault password

## Testing
- Molecule is used for local testing with libvirt VMs
- `uv run molecule verify` checks the web services (wordpress, vouchers, membres, grafana) after a converge, see `molecule/default/verify.yml`
- Web service tests can be accessed via localhost tunneling after SSH connection
- Services tested with curl against localhost endpoints (e.g., `curl https://vouchers.localhost`)