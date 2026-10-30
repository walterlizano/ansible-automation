# TODO

## migration

Move everything from `vps` (Debian 10: nginx + certbot, wordpress, membres, old
grafana/influxdb) and `vps2` (Debian 12: vouchers) to a reinstalled `vps2`
(Debian 13), configured by this playbook.

### What gets migrated

| data | from | how |
|---|---|---|
| vouchers database `ldt-vouchers.sqlite3` | vps2 `/srv/www/vouchers.epicerieledetour.org` | `sqlite3 -readonly .dump`, restored by `tmp_migration/restore.sh` |
| vouchers `files/` (emission report.ods) | same folder | tar, restored by `restore.sh` |
| wordpress database `wordpress` | vps MariaDB 10.3 | `mysqldump wordpress`, restored by `restore.sh` |
| wordpress `wp-content` (uploads, themes `ledetour` and `twentytwentyone-child`, plugins, languages) | vps `/srv/www/wordpress-5.7/wordpress/wp-content` | tar, restored by `restore.sh` |

Not migrated, on purpose:

- membres: the JSON files are regenerated every hour from the Google script,
  and the client CA in `roles/membres/files/client-ca.crt` is the same one vps
  uses (same sha256 fingerprint)
- old grafana, influxdb, loki, promtail, telegraf on vps: dropped, replaced by
  the srv1 stack
- TLS certificates: certbot's certificates are not reused, caddy gets new ones
- old `backup@pi1` borg backups of vps and vps2: stay on pi1
- vps2 ssh host key: already in the vault (`host_vars/vps2`), the playbook
  reinstalls it
- vouchers code: prod runs branch `v2` at `c565791`; the playbook runs `main`,
  which is `c565791` plus 2 commits that only add the `ldtvouchers` script entry
  and a template file. Same database schema, and `db init` is idempotent

Notes from the molecule dry run:

- The vouchers database `.dump` rebuilds with identical row counts
  (8930 vouchers, 61490 actions on 2026-10-03) and passes `integrity_check`
- WordPress home/siteurl are `https://wordpress.epicerieledetour.org` in the
  database. `restore.sh` rewrites them to `https://epicerieledetour.org`
  (~350 replacements; `guid` columns are deliberately left unchanged)
- WordPress core is downloaded fresh (newer than prod): `restore.sh` runs
  `wp core update-db`
- The wordpress role's "Enable plugins auto-updates" task fails when only some
  of its plugins already have auto-updates on, which is the case after the
  restore. `restore.sh` turns all auto-updates off first as a workaround. To fix
  in the role later.
- `/` answers 302 to `/fr/` (Polylang), same as prod today. `molecule verify`
  expects 200 on `/` so it will fail on wordpress with migrated content: it's
  expected
- Both Google login plugins end up active: the old
  `miniorange-login-with-eve-online-google-facebook` (from the prod database)
  and `login-with-google` (from the playbook). miniorange is removed once
  Google login is confirmed working, see the checklist. Tested on molecule:
  the login page then only shows `login-with-google`, and the playbook stays
  clean.
- In development, the vouchers report no longer emails: the unit has no SMTP
  settings and no `emailreport` step, and its timer is disabled and stopped.
  Production is unchanged.
- The molecule vps2 VM holds production data: destroy it
  (`uv run molecule destroy`) when done.

### Decisions

- `vouchers2.epicerieledetour.org` is dropped: not in the caddy config, its
  DNS record gets deleted
- `epicerieledetour.org` serves wordpress; `wordpress.epicerieledetour.org`
  and `www.epicerieledetour.org` redirect to it permanently, keeping the path
  (`roles/wordpress/templates/wordpress.caddy.j2`)
- miniorange gets removed, `login-with-google` is the only Google login

### TLS with caddy

Caddy gets Let's Encrypt certificates by itself (HTTP-01/TLS-ALPN on ports 80
and 443, opened by `roles/caddy`, account email `contact@epicerieledetour.org`)
for every public name in `/etc/caddy/sites.d/*.caddy`: `epicerieledetour.org`,
`www.`, `wordpress.`, `vouchers.`, `membres.`, `grafana.`. `*.localhost` names get
caddy's internal CA. For this to work:

- Every name must resolve to vps2, **for A and AAAA**. Let's Encrypt prefers
  IPv6: today `epicerieledetour.org`, `www.`, `wordpress.`, `membres.` and
  `grafana.` have an AAAA record `2607:5300:205:200::245d`, which nothing
  answers (vps has no IPv6 configured). They must point at vps2's IPv6
  (`2607:5300:205:200::4553` before the reinstall, check it after) or be
  deleted
- No CAA record exists on the domain, so nothing restricts the CA
- Let's Encrypt allows 5 failed validations per hostname per account per hour:
  switch the DNS **before** the playbook installs caddy. If caddy started too
  early, it retries on its own with backoff; `sudo systemctl restart caddy`
  retries immediately

### Checklist

DNS is at GoDaddy (`ns11/ns12.domaincontrol.com`). vps2's public addresses:
IPv4 `148.113.197.26`, IPv6 `2607:5300:205:200::4553` (to confirm after the
reinstall).

Before (can be done now):

- [ ] Lower the TTL of `epicerieledetour.org` from 3600 to 300 (the subdomains
      are already at 600 or less)
- [ ] Tell wordpress editors not to edit tonight
- [ ] Remember `tmp_migration` holds production data **in clear**: never
      commit it (`git status` shows it as untracked, it isn't in `.gitignore`)

Freeze and copy the data, from the workstation, in the repo root:

- [ ] Stop vouchers on old vps2, the last write wins from here on:
      `ssh vps2 sudo systemctl stop ldt-vouchers.service ldt-vouchers-report.timer ldt-vouchers-backup.timer`
- [ ] Copy the data:

      cd tmp_migration
      rm -rf bundle bundle.tar.gz && mkdir -p bundle/vouchers bundle/wordpress
      cp restore.sh bundle/
      ssh vps2 'sqlite3 -readonly /srv/www/vouchers.epicerieledetour.org/ldt-vouchers.sqlite3 .dump' > bundle/vouchers/ldt-vouchers.sql
      ssh vps2 'tar -C /srv/www/vouchers.epicerieledetour.org -czf - files' > bundle/vouchers/files.tar.gz
      ssh vps 'sudo mysqldump --single-transaction --default-character-set=utf8mb4 wordpress | gzip' > bundle/wordpress/wordpress.sql.gz
      ssh vps 'sudo tar -C /srv/www/wordpress-5.7/wordpress -czf - wp-content' > bundle/wordpress/wp-content.tar.gz
      tar -czf bundle.tar.gz bundle

- [ ] Check the copy: `tail -1 bundle/vouchers/ldt-vouchers.sql` is `COMMIT;`,
      `zcat bundle/wordpress/wordpress.sql.gz | tail -1` is `-- Dump completed on ...`,
      and the counts match:

      sqlite3 /tmp/check.sqlite3 < bundle/vouchers/ldt-vouchers.sql && sqlite3 /tmp/check.sqlite3 'select count(*) from vouchers; select count(*) from actions' && rm /tmp/check.sqlite3
      ssh vps2 'sqlite3 -readonly /srv/www/vouchers.epicerieledetour.org/ldt-vouchers.sqlite3 "select count(*) from vouchers; select count(*) from actions"'

DNS, at GoDaddy:

- [ ] A `@` (epicerieledetour.org) → `148.113.197.26`
- [ ] A `wordpress` → `148.113.197.26`
- [ ] A `vouchers` → `148.113.197.26`
- [ ] A `membres` → `148.113.197.26`
- [ ] A `grafana` → `148.113.197.26`
- [ ] Delete the A record `vouchers2` (dropped)
- [ ] AAAA `@`, `wordpress`, `membres`, `grafana` → `2607:5300:205:200::4553`,
      or delete them if vps2 has no IPv6 after the reinstall
- [ ] `www` stays a CNAME to `@`: it follows the apex's A and AAAA, and caddy
      redirects it to the apex
- [ ] Leave `vps.epicerieledetour.org` alone until vps is destroyed

Reinstall vps2:

- [ ] OVH panel: reinstall vps2 with Debian 13, with my ssh key for `debian`
- [ ] Check `debian` has passwordless sudo: `ssh vps2 sudo -n true`. If not,
      follow README "First setup of a production machine"
- [ ] The reinstall generates a new ssh host key, so known_hosts no longer
      matches until the `sshd` role puts the vaulted one back. Run the ssh play
      alone without host key checking (public key auth still works):
      `ANSIBLE_HOST_KEY_CHECKING=False ansible-playbook playbook.yml --limit vps2 --tags ssh`
- [ ] `ssh vps2 true` works again without a host key warning
- [ ] `ssh vps2 ip -br addr`: confirm the IPv6, fix the AAAA records if it changed

Run the playbook:

- [ ] DNS has propagated: `dig +short A epicerieledetour.org @8.8.8.8` and
      `dig +short AAAA epicerieledetour.org @8.8.8.8` (same for the other names)
      return vps2's addresses
- [ ] `ansible-playbook playbook.yml --limit vps2`. vps2 first: srv1 is only
      reachable through vps2's Wireguard endpoint, which doesn't exist yet. This
      wasn't exercised by molecule, where the VMs reach each other directly.
- [ ] Wireguard is back: `ping 192.168.211.60` (srv1) from the workstation
- [ ] `ansible-playbook playbook.yml` (everything, srv1 included)

Restore:

- [ ] `scp tmp_migration/bundle.tar.gz vps2:`
- [ ] `ssh vps2 'tar xzf bundle.tar.gz && sudo ./bundle/restore.sh https://epicerieledetour.org'`
- [ ] `ansible-playbook playbook.yml --limit vps2 --tags wordpress`
- [ ] Take a first backup: `ssh vps2 'sudo borgmatic --config /etc/borgmatic.d/vouchers.yaml --config /etc/borgmatic.d/wordpress.yaml --config /etc/borgmatic.d/system.yaml create --stats'`
- [ ] `ssh vps2 rm -rf bundle bundle.tar.gz`

Verify:

- [ ] Certificates were issued: `ssh vps2 journalctl -u caddy | grep -i "certificate obtained"`, 6 names
- [ ] For each name, a Let's Encrypt certificate is served over IPv4 and IPv6:

      for n in epicerieledetour.org www.epicerieledetour.org wordpress.epicerieledetour.org vouchers.epicerieledetour.org membres.epicerieledetour.org grafana.epicerieledetour.org; do
        for ip in 4 6; do printf "%s v%s " $n $ip; curl -$ip -s -o /dev/null -w "%{http_code} %{ssl_verify_result}\n" https://$n/; done
      done
      echo | openssl s_client -connect epicerieledetour.org:443 -servername epicerieledetour.org 2>/dev/null | openssl x509 -noout -issuer -dates

- [ ] `http://` redirects to `https://`: `curl -sI http://epicerieledetour.org | grep -i location`
- [ ] Wordpress: https://epicerieledetour.org redirects to `/fr/`, the home page
      looks like before (title "Épicerie Le Détour – BienvenuEs dans votre épicerie !"),
      images load, English version works
- [ ] Wordpress: `www.` and `wordpress.` redirect to the apex, path included:

      for n in www.epicerieledetour.org wordpress.epicerieledetour.org; do curl -s -o /dev/null -w "$n %{http_code} %{redirect_url}\n" https://$n/fr/; done

      both print `301 https://epicerieledetour.org/fr/`
- [ ] Wordpress: log in with Google at https://epicerieledetour.org/wp-login.php
- [ ] Wordpress: once Google login works, remove miniorange:
      `ssh vps2 'cd /srv/www/wordpress.epicerieledetour.org && sudo -u www-data php8.5 /usr/local/bin/wp plugin deactivate miniorange-login-with-eve-online-google-facebook --uninstall'`,
      then the login page only shows "Login with Google"
- [ ] Vouchers: https://vouchers.epicerieledetour.org works, the data is there
      (check a recent cash-in), scanning a voucher works
- [ ] Vouchers: `ssh vps2 systemctl list-timers ldt-vouchers-report.timer` is
      scheduled for 20:30, and the next day's report email arrives
- [ ] Membres: `curl -s https://membres.epicerieledetour.org/members.json | jq length`,
      and with the client certificate it returns non-anonymized data
- [ ] Grafana: https://grafana.epicerieledetour.org, logs and metrics of the new
      vps2 show up
- [ ] Backups: `ssh vps2 sudo borgmatic --config /etc/borgmatic.d/vouchers.yaml repo-list`
      shows the archive, and `systemctl is-enabled borgmatic.timer` is enabled

After:

- [ ] Drop vouchers2: check the record `vouchers2` is gone from GoDaddy and
      `dig +short vouchers2.epicerieledetour.org` returns nothing. Tell anyone
      still using `vouchers2.` links to use `vouchers.`
- [ ] A few days later: destroy vps (OVH), delete the DNS record `vps`
- [ ] Remove `host_vars/vps` and the `vps` mention in README
- [ ] Fix the wordpress role's "Enable plugins auto-updates" `failed_when` for
      partially enabled plugins
- [ ] Delete `tmp_migration` (production data in clear) and destroy the molecule VMs
- [ ] Raise the `epicerieledetour.org` TTL back to 3600

## b72 migration

Reinstall `b72` (Bâtiment 7's chat.batiment7.org, OVH VPS `51.79.25.187`,
Debian 11, Mattermost 10.6.0 enterprise build unlicensed, PostgreSQL 13) with
Debian 13, configured by this playbook: Mattermost 12.0.0 Team Edition,
PostgreSQL 17, caddy instead of nginx + certbot.

### What gets migrated

| data | from | how |
|---|---|---|
| database `mattermost` (279 active users, 182391 posts on 2026-10-21) | PostgreSQL 13 | `pg_dump --format=custom`, loaded by `mattermost-import` |
| attachments, emojis, profile pictures (12 GB) | `/opt/mattermost/data` | rsync, copied by `mattermost-import` |
| settings (site name, SMTP relay, push server…) | `/opt/mattermost/current/config/config.json` | copied by `mattermost-import`; the settings in `/etc/mattermost.env` override it |

Not migrated, on purpose:

- the old local borg repository `/opt/mattermost/borg_backups` and
  `pg_dumps`: on the same disk as what they back up. borgmatic now backs up to
  srv1
- the ssh host key: the playbook reinstalls the same one (vault, `host_vars/b72`)
- the nginx config and the certbot certificates: caddy gets new ones
- `chat.test.batiment7.org`: only a stale certificate is left of it

### Decisions

- Team Edition instead of the enterprise build: since v11 an unlicensed
  enterprise build runs as "Entry", which only shows the last 10,000 messages
  of the whole server. Team Edition has no message limit. What it lacks is not
  used on b72: SSO (only email login is on) and plugins (`PluginSettings.Enable`
  is false, so Playbooks, Boards, Calls are already off)
- Since 11.7, any server without a license, Team Edition included, refuses to
  create or reactivate an account once 250 are active (`maxUsersHardLimit` in
  `server/channels/app/limits.go`; it was 5000 up to 10.11). Logins, history
  and everything else keep working: rehearsed on molecule with the production
  database on 2026-10-22. b72 has 279 active accounts (278 people, 1 bot):
  115 active in the last 6 months, 15 in 6-12 months, 25 in 1-2 years, 19
  more than 2 years ago, 104 never. Decided: the 123 accounts never active or
  inactive for more than 2 years get deactivated after the migration, which
  leaves about 155. Until then no new member can join
- The database goes 10.6 → 10.11.24 → 11.7.13 → 12.0.0, one Extended Support
  Release at a time, as Mattermost recommends (`mattermost_import_steps`)
- Only the keys in `authorized_keys_users` get a shell on the new b72: whoever
  else uses `root@b72` today loses it

### Checklist

Before:

- [ ] Rehearse on molecule with the production data (see below)
- [ ] Tell the users about the downtime (a few hours: copy, reinstall, migration)
- [ ] Lower the TTL of `chat.batiment7.org`. The IP should not change with an
      OVH reinstall, but just in case
- [ ] Take an OVH snapshot of b72: the way back if anything goes wrong

Copy the data, from the workstation, in the repo root. A first copy while the
server runs, then a second one, much shorter, once it's stopped:

      mkdir -p tmp_migration/b72/bundle
      rsync -a --info=progress2 b72:/opt/mattermost/data/ tmp_migration/b72/bundle/data/
      ssh b72 systemctl stop b7-mattermost
      rsync -a --delete b72:/opt/mattermost/data/ tmp_migration/b72/bundle/data/
      ssh b72 'cd /tmp && sudo -u postgres pg_dump --format=custom mattermost' > tmp_migration/b72/bundle/mattermost.pgdump
      scp b72:/opt/mattermost/current/config/config.json tmp_migration/b72/bundle/

- [ ] Check the copy: `pg_restore --list tmp_migration/b72/bundle/mattermost.pgdump | grep -c 'TABLE DATA'`
      is not 0, and `du -sh tmp_migration/b72/bundle/data` matches
      `ssh b72 du -sh /opt/mattermost/data`
- [ ] Note the counts to compare after:
      `ssh b72 "cd /tmp && sudo -u postgres psql -d mattermost -Atc 'select count(*) from users where deleteat = 0' -c 'select count(*) from posts' -c 'select count(*) from fileinfo'"`

Reinstall b72:

- [ ] OVH panel: reinstall b72 with Debian 13, with my ssh key for `debian`
- [ ] `ssh debian@51.79.25.187 sudo -n true` works. If not, follow README
      "First setup of a production machine"
- [ ] First run, over the public address since wireguard doesn't exist yet on
      b72. The host key changed with the reinstall until the `sshd` role puts
      the vaulted one back:
      `ANSIBLE_HOST_KEY_CHECKING=False ansible-playbook playbook.yml --limit b72 -e "ansible_host=51.79.25.187"`
      The backup tasks fail on this run: srv1 doesn't know b72 yet
- [ ] Add b72 as a wireguard peer of vps2: `ansible-playbook playbook.yml --limit vps2 --tags wireguard`
- [ ] `ping 192.168.211.72` from the workstation
- [ ] `ansible-playbook playbook.yml --limit b72,srv1` (backup destination
      folder on srv1, prometheus target on srv1)
- [ ] `https://chat.batiment7.org` answers with a Let's Encrypt certificate:
      `ssh debian@192.168.211.72 journalctl -u caddy | grep -i "certificate obtained"`

Import:

- [ ] `rsync -a tmp_migration/b72/bundle/ debian@192.168.211.72:bundle/`
- [ ] `ssh debian@192.168.211.72 sudo mattermost-import bundle`: the counts it
      prints match the ones noted before
- [ ] Take a first backup: `ssh debian@192.168.211.72 sudo borgmatic --config /etc/borgmatic.d/mattermost.yaml create --stats`
- [ ] `ssh debian@192.168.211.72 rm -rf bundle`

Verify:

- [ ] Log in at https://chat.batiment7.org with an existing account
- [ ] Old messages are there: scroll a channel back to 2023, search a word from
      an old message
- [ ] Attachments and pictures of old messages open, custom emojis show up
- [ ] The mobile app connects and gets push notifications
- [ ] An email notification arrives (`smtp-relay.gmail.com` must accept the
      new b72: same IP, but check the relay's allowed senders)
- [ ] System Console > About: Mattermost Team Edition 12.0.0
- [ ] Grafana: b72's logs and metrics show up
- [ ] Backups: `ssh debian@192.168.211.72 sudo borgmatic --config /etc/borgmatic.d/mattermost.yaml repo-list`

After:

- [ ] Deactivate the accounts never active or inactive for more than 2 years
      (123 on 2026-10-22), to get under the 250 active accounts limit. List
      them, check the list, then deactivate them:

      ssh debian@192.168.211.72 "cd /tmp && sudo -u postgres psql -d mattermost -Atc \"
          select u.username from users u left join status s on s.userid = u.id
          where u.deleteat = 0 and u.id not in (select userid from bots)
          and coalesce(s.lastactivityat, 0) < extract(epoch from now() - interval '2 years') * 1000
          order by 1\"" > tmp_migration/b72/inactive.txt
      wc -l tmp_migration/b72/inactive.txt
      ssh debian@192.168.211.72 "sudo -u mattermost /opt/mattermost/current/bin/mmctl --local user deactivate $(tr '\n' ' ' < tmp_migration/b72/inactive.txt)"

      Rehearsed on molecule: 122 of the 123 get deactivated, 157 accounts stay
      active, and creating an account works again. One account
      (`oabn8f4rnidq3xsxnxsyk1to3e`) has an email Mattermost now considers
      invalid, which blocks its deactivation: fix its email (System Console >
      Users), then deactivate it. Reactivating an account later works as long
      as less than 250 are active
- [ ] Remove the `b72` entry from `~/.ssh/config` (root, public IP): use
      `debian@192.168.211.72`
- [ ] Delete `tmp_migration/b72` (the whole chat history in clear) and destroy
      the molecule VMs
- [ ] Delete the OVH snapshot a few days later

### Molecule rehearsal

With the bundle copied as above (no need to stop the production server for a
rehearsal):

      uv run molecule converge
      virsh -c qemu:///system domifaddr b72    # the VM's <b72 ip>
      rsync -a -e "ssh -i ~/.ansible/tmp/molecule.*/id_ssh_rsa" tmp_migration/b72/bundle/ molecule@<b72 ip>:bundle/
      uv run molecule login --host b72    # then: sudo mattermost-import bundle
      uv run molecule verify
      uv run molecule converge -- --limit b72 --tags mattermost --tags borgmatic_create
      uv run molecule converge -- --limit b72 --tags mattermost --tags borgmatic_restore

`molecule verify` checks the version and the site URL, so it passes with the
imported config.json too. Log in through the tunnel (`https://chat.localhost`)
with a production account to check the history and the attachments.

Notes from the molecule rehearsal (2026-10-22, production database of
2026-10-21, attachments of the last 3 days only: the VM disk is 16 GB):

- `mattermost-import` went through 10.11.24 and 11.7.13 without an error, and
  12.0.0 starts on the result. Counts match production: 279 active users,
  182393 posts, 4279 files, 7 teams
- An existing user logs in, reads a channel back to its first posts (2019),
  and search finds a 2019 message: Team Edition hides nothing
- Backup and restore, through the playbook: `borgmatic_create` puts the
  database dump and `/srv/mattermost` on srv1 (119 MB). After wiping b72's
  mattermost (database, `/srv/mattermost`, `/opt/mattermost`) and converging a
  new empty one, `borgmatic_restore` brings back every count, `config.json`
  and every file byte for byte. A restore extracts over what's there: files
  written after the backup stay (here, one profile picture of the empty
  install's default user), mattermost just doesn't reference them
- In molecule, `--limit b72` alone fails in the wireguard role: vps2's
  `wireguard_endpoint_host` only exists once converge.yml ran on vps2. With
  `--tags mattermost` the wireguard role is skipped, so it doesn't matter there
