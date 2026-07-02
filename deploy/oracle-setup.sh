#!/usr/bin/env bash
#
# oracle-setup.sh — prepare a fresh Oracle Cloud "Always Free" Ampere A1
# (aarch64) VM running Ubuntu 22.04/24.04 to host QuantumLedger.
#
# Installs Docker Engine + the compose plugin, enables the service, adds the
# invoking user to the docker group, and opens the in-VM firewall for HTTP/HTTPS
# (Oracle's Ubuntu images ship a restrictive iptables ruleset that blocks 80/443).
#
# Idempotent: safe to re-run. Run it as a normal sudo-capable user:
#   bash deploy/oracle-setup.sh
#
set -euo pipefail

# Resolve the human user even when invoked via sudo.
TARGET_USER="${SUDO_USER:-$USER}"

# sudo helper — no-op prefix if we're already root.
if [ "$(id -u)" -eq 0 ]; then
	SUDO=""
else
	SUDO="sudo"
fi

echo "==> QuantumLedger Oracle VM setup"
echo "    target user: ${TARGET_USER}"
echo "    arch:        $(uname -m)"

# --- 1. Docker Engine + compose plugin -------------------------------------
if command -v docker >/dev/null 2>&1; then
	echo "==> Docker already installed: $(docker --version)"
else
	echo "==> Installing Docker Engine via get.docker.com ..."
	curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
	$SUDO sh /tmp/get-docker.sh
	rm -f /tmp/get-docker.sh
fi

echo "==> Enabling and starting docker service ..."
$SUDO systemctl enable --now docker

# --- 2. docker group membership --------------------------------------------
if id -nG "${TARGET_USER}" | tr ' ' '\n' | grep -qx docker; then
	echo "==> User ${TARGET_USER} already in docker group"
else
	echo "==> Adding ${TARGET_USER} to docker group (re-login required to take effect) ..."
	$SUDO usermod -aG docker "${TARGET_USER}"
fi

# --- 3. Firewall: open tcp 80 and 443 --------------------------------------
# Oracle's Ubuntu images preload an iptables chain that REJECTs most inbound
# traffic. Insert ACCEPT rules for 80/443 *before* the first REJECT in INPUT,
# then persist them. (You must ALSO open 80/443 in the OCI Security List /
# Network Security Group in the Oracle web console — that's outside this VM.)
open_port() {
	local port="$1"
	if $SUDO iptables -C INPUT -p tcp --dport "${port}" -j ACCEPT >/dev/null 2>&1; then
		echo "==> iptables: port ${port}/tcp already allowed"
		return
	fi
	# Find the rule number of the first REJECT in INPUT, insert just before it.
	local reject_line
	reject_line="$($SUDO iptables -L INPUT --line-numbers -n | awk '/REJECT/ {print $1; exit}')"
	if [ -n "${reject_line}" ]; then
		echo "==> iptables: inserting ACCEPT for ${port}/tcp at position ${reject_line}"
		$SUDO iptables -I INPUT "${reject_line}" -p tcp --dport "${port}" -j ACCEPT
	else
		echo "==> iptables: appending ACCEPT for ${port}/tcp (no REJECT rule found)"
		$SUDO iptables -A INPUT -p tcp --dport "${port}" -j ACCEPT
	fi
}

echo "==> Opening in-VM firewall for HTTP/HTTPS ..."
open_port 80
open_port 443

# Persist iptables across reboots. Install iptables-persistent noninteractively
# if the tooling isn't present.
if ! command -v netfilter-persistent >/dev/null 2>&1; then
	echo "==> Installing iptables-persistent (noninteractive) ..."
	echo "iptables-persistent iptables-persistent/autosave_v4 boolean true" | $SUDO debconf-set-selections
	echo "iptables-persistent iptables-persistent/autosave_v6 boolean true" | $SUDO debconf-set-selections
	$SUDO DEBIAN_FRONTEND=noninteractive apt-get update -y
	$SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent
fi

echo "==> Persisting iptables rules ..."
if command -v netfilter-persistent >/dev/null 2>&1; then
	$SUDO netfilter-persistent save
else
	$SUDO mkdir -p /etc/iptables
	$SUDO sh -c 'iptables-save > /etc/iptables/rules.v4'
fi

# --- Done -------------------------------------------------------------------
cat <<EOF

==> Setup complete.

Next steps:
  1. Log out and back in (or run \`newgrp docker\`) so docker group membership
     takes effect for ${TARGET_USER}.
  2. In the Oracle Cloud console, open ingress for tcp/80 and tcp/443 on this
     VM's subnet Security List / Network Security Group (this script only
     handles the in-VM iptables firewall).
  3. Point an A record for your domain at this VM's public IP.
  4. From the repo:
       cp deploy/.env.example deploy/.env
       # edit deploy/.env — set QL_DOMAIN, secrets, etc.
       docker compose -f deploy/docker-compose.yml up -d --build

EOF
