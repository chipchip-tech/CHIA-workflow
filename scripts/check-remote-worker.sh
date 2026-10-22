#!/usr/bin/env bash
# Run on the HEAD machine to check that a remote machine is ready to host CHIA
# Docker workers. Read-only on both machines.
#
#   bash scripts/check-remote-worker.sh user@192.168.1.20 [HEAD_IP]
#
# See docs/usage-remote.md.

if [ $# -lt 1 ]; then
    echo "usage: $0 user@remote-host [head-ip]" >&2
    exit 2
fi
TARGET=$1
HEAD=${2:-${HEAD_IP:-$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')}}
SSH=(ssh -A -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 "$TARGET")
fails=0

ok()   { printf '  \e[32m[OK]\e[0m   %s\n' "$*"; }
bad()  { printf '  \e[31m[FAIL]\e[0m %s\n' "$*"; fails=$((fails + 1)); }
warn() { printf '  \e[33m[WARN]\e[0m %s\n' "$*"; }
remote() { "${SSH[@]}" "bash --login -c $(printf '%q' "$1")" 2>/dev/null; }

echo "== SSH (head -> $TARGET)"
if "${SSH[@]}" true 2>/dev/null; then
    ok "passwordless ssh"
else
    bad "passwordless ssh failed: run  ssh-copy-id $TARGET  (and ssh-add on the head)"
    echo; echo "$fails required check(s) failed."; exit "$fails"
fi

echo "== Remote system"
remote 'cat /etc/os-release 2>/dev/null | grep -m1 PRETTY_NAME' | sed 's/^/         /'
arch=$(remote 'uname -m')
[ "$arch" = "x86_64" ] && ok "arch x86_64" || bad "arch '$arch' (CHIA images are x86_64)"

echo "== Docker on remote"
if remote 'command -v docker' >/dev/null; then
    ok "docker in login-shell PATH"
    if remote 'docker info' >/dev/null; then
        ok "docker usable without sudo"
    else
        bad "docker info failed as $TARGET (add the user to the docker group, then log in again)"
    fi
else
    bad "'docker' not found in 'bash --login' PATH on remote"
fi

echo "== Tools on remote"
for tool in rsync bash; do
    remote "command -v $tool" >/dev/null && ok "$tool present" || bad "$tool missing on remote"
done

echo "== Resources on remote"
mem_gb=$(remote "awk '/MemTotal/ {print int(\$2/1024/1024)}' /proc/meminfo")
cpus=$(remote 'nproc')
docker_root=$(remote "docker info -f '{{.DockerRootDir}}'")
free_gb=$(remote "df -BG --output=avail ${docker_root:-/var} | tail -1 | tr -dc '0-9'")
echo "         CPUs: ${cpus:-?}  RAM: ${mem_gb:-?}G  free at ${docker_root:-/var}: ${free_gb:-?}G"
[ "${mem_gb:-0}" -ge 16 ] && ok "RAM >= 16G" || warn "RAM ${mem_gb:-?}G (a Chipyard worker needs ~16G to build RocketConfig)"
[ "${free_gb:-0}" -ge 120 ] && ok "disk >= 120G free" || warn "disk ${free_gb:-?}G free (chia-chisel-build is large)"

echo "== Network"
if [ -z "$HEAD" ]; then
    warn "head IP unknown; pass it as the 2nd argument"
else
    # The Ray head listens on 6379 only while a cluster is up.
    if (exec 3<>"/dev/tcp/$HEAD/6379") 2>/dev/null; then
        if remote "timeout 5 bash -c '</dev/tcp/$HEAD/6379'"; then
            ok "remote reaches Ray head at $HEAD:6379"
        else
            bad "remote cannot reach $HEAD:6379 (firewall on the head? see docs/usage-remote.md section 3)"
        fi
    else
        warn "Ray head not running on $HEAD:6379; start a cluster and rerun to test head<-remote reachability"
    fi
fi
fw=$(remote 'systemctl is-active firewalld 2>/dev/null; command -v ufw >/dev/null && ufw status 2>/dev/null | head -1')
echo "         remote firewall: ${fw:-unknown}"
warn "Ray also needs head->remote traffic on random ports; allow the head's IP in the remote firewall"

echo
if [ "$fails" -eq 0 ]; then echo "All required checks passed."; else echo "$fails required check(s) failed."; fi
exit "$fails"
