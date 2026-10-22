#!/usr/bin/env bash
# Check that a Bazzite machine is ready to be a CHIA head/worker host.
# See docs/setup-bazzite.md. Read-only: changes nothing on the system.

CHIA_ENV=${CHIA_ENV:-chia_env}
HOST=${THIS_MACHINE:-$(hostname -I 2>/dev/null | awk '{print $1}')}
USER=${USER:-$(id -un)}
fails=0

ok()   { printf '  \e[32m[OK]\e[0m   %s\n' "$*"; }
bad()  { printf '  \e[31m[FAIL]\e[0m %s\n' "$*"; fails=$((fails + 1)); }
warn() { printf '  \e[33m[WARN]\e[0m %s\n' "$*"; }

echo "== Image"
if command -v rpm-ostree >/dev/null; then
    img=$(rpm-ostree status --booted 2>/dev/null | grep -m1 -oE 'ghcr.io/[^ ]+')
    case "$img" in
        *bazzite-dx*) ok "booted image: $img" ;;
        "")           warn "could not read booted image" ;;
        *)            warn "booted image: $img (not bazzite-dx; docker may be missing)" ;;
    esac
fi

echo "== Docker"
if bash --login -c 'command -v docker' >/dev/null 2>&1; then
    ok "docker in login-shell PATH: $(bash --login -c 'command -v docker')"
    if docker info >/dev/null 2>&1; then
        ok "docker daemon reachable as $USER"
    else
        bad "docker info failed (run 'ujust dx-group' and log in again)"
    fi
else
    bad "'docker' not found in 'bash --login' PATH"
fi

echo "== SSH"
if systemctl is-active --quiet sshd 2>/dev/null; then ok "sshd active"; else bad "sshd not active (sudo systemctl enable --now sshd)"; fi
if [ -z "$HOST" ]; then
    bad "could not determine host IP; export THIS_MACHINE"
elif ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=5 "$USER@$HOST" true 2>/dev/null; then
    ok "passwordless ssh to $USER@$HOST"
else
    bad "passwordless ssh to $USER@$HOST failed (authorized_keys / ssh-agent)"
fi
if loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q 'Linger=yes'; then
    ok "linger enabled (Ray head survives the SSH session that started it)"
else
    bad "linger off: run  loginctl enable-linger $USER  (see docs/setup-bazzite.md section 5)"
fi

echo "== Conda / CHIA"
if grep -q 'case \$- in' ~/.bashrc 2>/dev/null; then
    warn "~/.bashrc has an interactive-only guard; non-interactive CHIA commands may skip conda"
fi
versions=$(bash --login -c "source ~/.bashrc >/dev/null 2>&1; conda activate $CHIA_ENV && python -V && python -c 'import ray; print(\"ray\", ray.__version__)' && command -v chia" 2>/dev/null)
if [ -n "$versions" ]; then
    echo "$versions" | sed 's/^/         /'
    echo "$versions" | grep -q '^Python 3\.10\.' && ok "Python 3.10.x" || bad "Python must be 3.10.x (3.10.19 recommended)"
    echo "$versions" | grep -q '^ray 2\.54\.0' && ok "ray 2.54.0" || bad "ray must be 2.54.0"
else
    bad "cannot activate conda env '$CHIA_ENV' from a login shell"
fi

echo "== Misc"
for tool in rsync git; do
    command -v "$tool" >/dev/null && ok "$tool present" || bad "$tool missing"
done
free_gb=$(df -BG --output=avail /var 2>/dev/null | tail -1 | tr -dc '0-9')
if [ -n "$free_gb" ]; then
    [ "$free_gb" -ge 150 ] && ok "/var free: ${free_gb}G" || warn "/var free: ${free_gb}G (chia-chisel-build is large; 150G+ recommended)"
fi
mem_gb=$(awk '/MemTotal/ {print int($2/1024/1024)}' /proc/meminfo)
[ "$mem_gb" -ge 32 ] && ok "RAM: ${mem_gb}G" || warn "RAM: ${mem_gb}G (32G+ recommended for Chipyard builds)"

echo
if [ "$fails" -eq 0 ]; then echo "All required checks passed."; else echo "$fails required check(s) failed."; fi
exit "$fails"
