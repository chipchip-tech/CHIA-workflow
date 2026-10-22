#!/usr/bin/env bash
# Run ON THE REMOTE machine (not the head) to prepare it as a CHIA Docker worker:
# sshd on, Docker usable without sudo, rsync present, head allowed through the firewall.
#
#   bash prepare-remote-worker.sh <HEAD_IP>          # e.g. 192.168.1.3
#
# Supports Ubuntu/Debian, Fedora Workstation, and Fedora Atomic (Bazzite/Bluefin/...).
# Prints each step and asks before changing anything. See docs/usage-remote.md.

set -euo pipefail

HEAD_IP=${1:-}
if [ -z "$HEAD_IP" ]; then
    echo "usage: $0 <HEAD_IP>" >&2
    exit 2
fi

confirm() { read -r -p "$1 [y/N] " a; [[ $a == [yY]* ]]; }
step() { printf '\n\e[1m== %s\e[0m\n' "$*"; }

. /etc/os-release
atomic=false
[ -e /run/ostree-booted ] && atomic=true
echo "Detected: $PRETTY_NAME (atomic=$atomic), user=$USER"

step "1. SSH server"
if systemctl is-active --quiet sshd 2>/dev/null || systemctl is-active --quiet ssh 2>/dev/null; then
    echo "sshd already running"
elif confirm "Enable and start sshd?"; then
    if [[ $ID == ubuntu || $ID == debian || ${ID_LIKE:-} == *debian* ]]; then
        sudo apt-get update && sudo apt-get install -y openssh-server
        sudo systemctl enable --now ssh
    else
        sudo systemctl enable --now sshd
    fi
fi
echo "Next, on the HEAD run:  ssh-copy-id $USER@<this-machine-ip>"

step "2. Docker"
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
    echo "docker works without sudo"
elif $atomic; then
    if command -v docker >/dev/null; then
        echo "docker is installed; add yourself to the docker group:"
        if command -v ujust >/dev/null && confirm "Run 'ujust dx-group' (then log out/in)?"; then
            ujust dx-group
        fi
    else
        echo "This atomic image has no docker. On Bazzite, rebase to bazzite-dx (docs/setup-bazzite.md step 1):"
        echo "    brh rebase bazzite-dx:stable   # pick the variant matching your desktop/GPU"
        echo "then rerun this script."
        exit 1
    fi
elif [[ $ID == ubuntu || $ID == debian || ${ID_LIKE:-} == *debian* ]]; then
    if confirm "Install docker.io from the distro and add $USER to the docker group?"; then
        sudo apt-get update && sudo apt-get install -y docker.io
        sudo systemctl enable --now docker
        sudo usermod -aG docker "$USER"
        echo "Log out and back in (or reboot) so the docker group applies."
    fi
elif [[ $ID == fedora ]]; then
    if confirm "Install moby-engine and add $USER to the docker group?"; then
        sudo dnf install -y moby-engine
        sudo systemctl enable --now docker
        sudo usermod -aG docker "$USER"
        echo "Log out and back in (or reboot) so the docker group applies."
    fi
else
    echo "Unknown distro: install Docker Engine yourself (https://docs.docker.com/engine/install/)."
fi

step "3. rsync"
if command -v rsync >/dev/null; then
    echo "rsync present"
elif [[ $ID == ubuntu || $ID == debian || ${ID_LIKE:-} == *debian* ]] && confirm "Install rsync?"; then
    sudo apt-get install -y rsync
elif ! $atomic && [[ $ID == fedora ]] && confirm "Install rsync?"; then
    sudo dnf install -y rsync
else
    echo "Install rsync manually."
fi

step "4. Firewall: trust the head ($HEAD_IP)"
if systemctl is-active --quiet firewalld 2>/dev/null; then
    if confirm "Add $HEAD_IP to firewalld zone 'trusted'?"; then
        sudo firewall-cmd --permanent --zone=trusted --add-source="$HEAD_IP"
        sudo firewall-cmd --reload
    fi
elif command -v ufw >/dev/null && sudo ufw status | grep -q "Status: active"; then
    if confirm "Allow all traffic from $HEAD_IP in ufw?"; then
        sudo ufw allow from "$HEAD_IP"
    fi
else
    echo "No active firewalld/ufw detected; nothing to open."
fi

step "5. Pre-pull CHIA images (optional, large)"
if confirm "Pull chia-chisel-build and chia-verilator-run now?"; then
    if docker info >/dev/null 2>&1; then
        docker pull ghcr.io/ucb-bar/chia-chisel-build:latest
        docker pull ghcr.io/ucb-bar/chia-verilator-run:latest
    else
        echo "docker is not usable yet (log out/in for the docker group), skipping; chia up will pull them."
    fi
fi

echo
echo "Done. On the head, run:  bash scripts/check-remote-worker.sh $USER@<this-machine-ip> $HEAD_IP"
