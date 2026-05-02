# Known Issues

## VM provisioning fails: libvirt default network not found

**Status:** Open

**Symptom:**
`virt-install` fails with `ERROR Network not found: no network with matching name 'default'` even after the playbook defines and starts the network via `virsh -c qemu:///system`.

**Root cause:**
libvirt URI mismatch between the network definition (`qemu:///system`) and `virt-install`'s default connection (`qemu:///session`). When run as a non-root user, `virt-install` connects to `qemu:///session` by default, which has a separate network namespace where the `default` network does not exist — even if `--connect qemu:///system` is passed, the user may lack permissions to access the system libvirt instance.

**Attempted fixes:**
1. Added tasks to define/start/autostart the `default` network from `/usr/share/libvirt/networks/default.xml` using `virsh -c qemu:///system` — network was defined but `virt-install` still couldn't find it (session vs system mismatch).
2. Added `--connect {{ libvirt_uri }}` to `virt-install` command — still failing, likely due to user permissions on `qemu:///system` or polkit auth issues.

**Possible next steps:**
- Ensure user is in the `libvirt` group (`sudo usermod -aG libvirt $(whoami)`, then re-login).
- Or run the playbook with `become: yes` / `sudo` so all virsh/virt-install commands hit `qemu:///system` with root privs.
- Or define the network in `qemu:///session` instead (copy the default.xml, modify the bridge name to avoid conflict, and define via `virsh -c qemu:///session net-define`).
- Check polkit rules: `pkcheck` or `polkit` may be blocking non-root access to `qemu:///system`.
- Verify with: `virsh -c qemu:///system net-list --all` and `virsh -c qemu:///session net-list --all` to confirm which URI sees the default network.
