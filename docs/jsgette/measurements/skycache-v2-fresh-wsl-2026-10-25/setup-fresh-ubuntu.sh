set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git gh curl wget ca-certificates build-essential python3 python3-venv python3-pip zip unzip rsync pkg-config libssl-dev zlib1g-dev locales time openssh-client
if ! id jgett >/dev/null 2>&1; then useradd --create-home --shell /bin/bash --uid 1000 jgett; fi
usermod -aG sudo jgett
printf '[boot]\nsystemd=true\n\n[user]\ndefault=jgett\n' > /etc/wsl.conf
mkdir -p /home/jgett/REPOS/.tools /home/jgett/REPOS/.work
chown -R jgett:jgett /home/jgett
uname -a
cat /etc/os-release
df -h /
