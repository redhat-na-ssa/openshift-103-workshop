#!/usr/bin/env bash
# Installs the cloud CLIs the ROSA and ARO workshop uses into its tools image, on top of
# the shared ones (oc, kubectl, helm, jq, yq, git, curl, wget, skopeo, docker shim).
# The image build runs it as root, with TARGETARCH set to amd64 or arm64.
set -euo pipefail

case "$TARGETARCH" in
  amd64) rosa_suffix= aws_arch=x86_64 ;;
  arm64) rosa_suffix=-arm64 aws_arch=aarch64 ;;
  *) echo "unsupported TARGETARCH=$TARGETARCH" >&2; exit 1 ;;
esac

# rosa, for ROSA
curl -sSL "https://mirror.openshift.com/pub/openshift-v4/clients/rosa/latest/rosa-linux${rosa_suffix}.tar.gz" \
  | tar -xz -C /usr/local/bin rosa
chmod 0755 /usr/local/bin/rosa

# aws (v2), for ROSA
curl -sSL -o /tmp/awscliv2.zip "https://awscli.amazonaws.com/awscli-exe-linux-${aws_arch}.zip"
unzip -q /tmp/awscliv2.zip -d /tmp
/tmp/aws/install
rm -rf /tmp/aws /tmp/awscliv2.zip

# az, for ARO
rpm --import https://packages.microsoft.com/keys/microsoft.asc
dnf install -y https://packages.microsoft.com/config/rhel/9.0/packages-microsoft-prod.rpm
dnf install -y azure-cli
dnf clean all
