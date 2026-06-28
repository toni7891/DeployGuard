#!/usr/bin/env bash
# DeployGuard installer — macOS and Linux
# Usage: curl -fsSL https://raw.githubusercontent.com/toni7891/deployguard/main/scripts/install.sh | bash
set -euo pipefail

BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
DIM="\033[2m"
RESET="\033[0m"

DEPLOYGUARD_VERSION="${DEPLOYGUARD_VERSION:-latest}"

info()    { printf "  ${DIM}→${RESET} %s\n" "$*"; }
success() { printf "  ${GREEN}✓${RESET}  %s\n" "$*"; }
warn()    { printf "  ${YELLOW}!${RESET}  %s\n" "$*"; }
die()     { printf "\n${RED}Error:${RESET} %s\n\n" "$*" >&2; exit 1; }

header() {
  printf "\n${BOLD}%s${RESET}\n" "$*"
}

# ── detect OS ──────────────────────────────────────────────────────────────────

OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
  Darwin) PLATFORM="macos" ;;
  Linux)  PLATFORM="linux" ;;
  *)      die "Unsupported OS: $OS. DeployGuard supports macOS and Linux." ;;
esac

printf "\n"
printf "${BOLD}🛡️  DeployGuard Installer${RESET}\n"
printf "${DIM}Platform: %s (%s)${RESET}\n\n" "$PLATFORM" "$ARCH"

# ── helpers ────────────────────────────────────────────────────────────────────

has() { command -v "$1" &>/dev/null; }

require_one_of() {
  # require_one_of <label> <cmd1> <cmd2> ...
  local label="$1"; shift
  for cmd in "$@"; do
    if has "$cmd"; then return 0; fi
  done
  die "$label is required but not found. Install one of: $*"
}

brew_install() {
  local pkg="$1"
  local bin="${2:-$1}"
  if has "$bin"; then
    success "$bin already installed"
  else
    info "brew install $pkg"
    brew install "$pkg" 2>&1 | tail -3
    success "$bin installed"
  fi
}

apt_install() {
  local pkg="$1"
  local bin="${2:-$1}"
  if has "$bin"; then
    success "$bin already installed"
  else
    info "apt-get install $pkg"
    sudo apt-get install -y "$pkg" &>/dev/null
    success "$bin installed"
  fi
}

# ── check package manager ──────────────────────────────────────────────────────

header "Package manager"

if [ "$PLATFORM" = "macos" ]; then
  if ! has brew; then
    info "Homebrew not found — installing..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # add brew to PATH for Apple Silicon
    if [ "$ARCH" = "arm64" ] && [ -f "/opt/homebrew/bin/brew" ]; then
      eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
  fi
  success "Homebrew $(brew --version | head -1)"
elif [ "$PLATFORM" = "linux" ]; then
  if has apt-get; then
    PKG_MGR="apt"
    info "Updating apt..."
    sudo apt-get update -qq
    success "apt-get"
  elif has dnf; then
    PKG_MGR="dnf"
    success "dnf"
  elif has yum; then
    PKG_MGR="yum"
    success "yum"
  else
    die "No supported package manager found (apt/dnf/yum). Install dependencies manually and re-run."
  fi
fi

# ── Python ─────────────────────────────────────────────────────────────────────

header "Python"

if ! has python3; then
  if [ "$PLATFORM" = "macos" ]; then
    brew_install python@3.12 python3
  elif [ "$PKG_MGR" = "apt" ]; then
    apt_install python3
    apt_install python3-pip pip3
  else
    die "Python 3.11+ not found. Install it and re-run."
  fi
else
  PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  PY_MAJOR="${PY_VER%%.*}"
  PY_MINOR="${PY_VER##*.}"
  if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    die "Python ${PY_VER} found but DeployGuard requires Python 3.11+. Upgrade Python and re-run."
  fi
  success "Python ${PY_VER}"
fi

# ensure pip / pipx
if ! has pipx; then
  info "Installing pipx..."
  if [ "$PLATFORM" = "macos" ]; then
    brew install pipx
    pipx ensurepath
  else
    python3 -m pip install --user pipx --quiet
    python3 -m pipx ensurepath
  fi
  success "pipx installed"
else
  success "pipx $(pipx --version 2>/dev/null || echo 'found')"
fi

# ── Docker ─────────────────────────────────────────────────────────────────────

header "Docker"

if has docker && docker info &>/dev/null 2>&1; then
  success "Docker running"
elif has docker; then
  warn "Docker installed but daemon not running — start Docker Desktop and re-run \`dg doctor\`"
else
  if [ "$PLATFORM" = "macos" ]; then
    warn "Docker not found. Install Docker Desktop: https://docs.docker.com/desktop/install/mac-install/"
    warn "Or run: brew install --cask docker"
    warn "Skipping — re-run \`dg doctor\` after Docker is installed."
  else
    info "Installing Docker (Linux)..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "${USER}"
    success "Docker installed. Log out and back in for group membership to take effect."
  fi
fi

# ── Kubernetes toolchain ───────────────────────────────────────────────────────

header "Kubernetes toolchain"

if [ "$PLATFORM" = "macos" ]; then
  brew_install kubectl
  brew_install helm
  brew_install minikube
elif [ "$PKG_MGR" = "apt" ]; then
  # kubectl
  if ! has kubectl; then
    info "Installing kubectl..."
    curl -fsSLo /usr/local/bin/kubectl "https://dl.k8s.io/release/$(curl -fsSL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
    chmod +x /usr/local/bin/kubectl
    success "kubectl installed"
  else
    success "kubectl already installed"
  fi
  # helm
  if ! has helm; then
    info "Installing helm..."
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash &>/dev/null
    success "helm installed"
  else
    success "helm already installed"
  fi
  # minikube
  if ! has minikube; then
    info "Installing minikube..."
    curl -fsSLo /usr/local/bin/minikube "https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64"
    chmod +x /usr/local/bin/minikube
    success "minikube installed"
  else
    success "minikube already installed"
  fi
fi

# ── Validation tools ───────────────────────────────────────────────────────────

header "Validation tools"

if [ "$PLATFORM" = "macos" ]; then
  brew_install kubeconform
  brew_install aquasecurity/trivy/trivy trivy
elif [ "$PKG_MGR" = "apt" ]; then
  if ! has kubeconform; then
    info "Installing kubeconform..."
    KCONF_VERSION="$(curl -fsSL https://api.github.com/repos/yannh/kubeconform/releases/latest | grep tag_name | cut -d '"' -f4)"
    curl -fsSLo /tmp/kubeconform.tar.gz "https://github.com/yannh/kubeconform/releases/download/${KCONF_VERSION}/kubeconform-linux-amd64.tar.gz"
    tar -xzf /tmp/kubeconform.tar.gz -C /usr/local/bin/ kubeconform
    success "kubeconform ${KCONF_VERSION}"
  else
    success "kubeconform already installed"
  fi
  if ! has trivy; then
    info "Installing trivy..."
    curl -fsSL https://aquasecurity.github.io/trivy-repo/deb/public.key | sudo gpg --dearmor -o /usr/share/keyrings/trivy.gpg
    echo "deb [signed-by=/usr/share/keyrings/trivy.gpg] https://aquasecurity.github.io/trivy-repo/deb generic main" | sudo tee /etc/apt/sources.list.d/trivy.list
    sudo apt-get update -qq && sudo apt-get install -y trivy &>/dev/null
    success "trivy installed"
  else
    success "trivy already installed"
  fi
fi

# ── Infrastructure + cost tools ────────────────────────────────────────────────

header "Infrastructure + cost tools"

if [ "$PLATFORM" = "macos" ]; then
  brew install hashicorp/tap/terraform 2>&1 | grep -E "(already|installed|Error)" || true
  if has terraform; then success "terraform $(terraform version -json 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin)["terraform_version"])' 2>/dev/null || echo 'installed')"; fi
  brew_install infracost
elif [ "$PKG_MGR" = "apt" ]; then
  if ! has terraform; then
    info "Installing terraform..."
    wget -qO /tmp/terraform.zip "https://releases.hashicorp.com/terraform/$(curl -fsSL https://checkpoint-api.hashicorp.com/v1/check/terraform | python3 -c 'import sys,json; print(json.load(sys.stdin)["current_version"])')/terraform_$(curl -fsSL https://checkpoint-api.hashicorp.com/v1/check/terraform | python3 -c 'import sys,json; print(json.load(sys.stdin)["current_version"])')_linux_amd64.zip"
    unzip -q /tmp/terraform.zip -d /usr/local/bin/
    success "terraform installed"
  else
    success "terraform already installed"
  fi
  if ! has infracost; then
    info "Installing infracost..."
    curl -fsSL https://raw.githubusercontent.com/infracost/infracost/master/scripts/install.sh | sh &>/dev/null
    success "infracost installed"
  else
    success "infracost already installed"
  fi
fi

# ── DeployGuard ────────────────────────────────────────────────────────────────

header "DeployGuard"

if [ "$DEPLOYGUARD_VERSION" = "latest" ]; then
  info "pipx install deployguard"
  pipx install deployguard --quiet 2>/dev/null || pipx upgrade deployguard --quiet 2>/dev/null || true
else
  info "pipx install deployguard==${DEPLOYGUARD_VERSION}"
  pipx install "deployguard==${DEPLOYGUARD_VERSION}" --quiet 2>/dev/null || pipx upgrade deployguard --quiet 2>/dev/null || true
fi

# verify
if has dg; then
  success "dg installed ($(dg --version 2>/dev/null || echo 'ok'))"
else
  # pipx path may not be in current shell — add it
  export PATH="${PATH}:${HOME}/.local/bin"
  if has dg; then
    success "dg installed"
    warn "Add \$HOME/.local/bin to your PATH to use \`dg\` in future shells:"
    warn "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc  # or ~/.bashrc"
  else
    die "Installation succeeded but \`dg\` not found in PATH. Run: pipx ensurepath, then restart your shell."
  fi
fi

# ── Done ───────────────────────────────────────────────────────────────────────

printf "\n"
printf "${GREEN}${BOLD}✅  DeployGuard installed.${RESET}\n\n"
printf "Run ${BOLD}dg doctor${RESET} to confirm everything is ready.\n"
printf "Then:\n\n"
printf "  ${BOLD}dg init payments-api${RESET}\n"
printf "  ${BOLD}dg cost${RESET}\n"
printf "  ${BOLD}dg provision${RESET}\n"
printf "  ${BOLD}dg deploy${RESET}\n\n"
