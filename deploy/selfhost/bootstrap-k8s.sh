#!/usr/bin/env bash
set -Eeuo pipefail

VERSIONS_FILE="${1:-}"
[[ -n "$VERSIONS_FILE" && -f "$VERSIONS_FILE" ]] || {
  echo "Usage: $0 /secure/reviewed-platform-versions.env" >&2
  exit 64
}

# This bootstrap is intentionally for a new/dedicated ESP-controlled Kubernetes cluster.
# Installing or replacing a CNI/load-balancer on an arbitrary existing cluster can break it.
[[ "${ESP_BOOTSTRAP_NEW_DEDICATED_CLUSTER:-}" == "true" ]] || {
  echo "Refusing cluster mutation. Set ESP_BOOTSTRAP_NEW_DEDICATED_CLUSTER=true only for the reviewed ESP cluster." >&2
  exit 78
}
[[ "${ESP_CONFIRM_CILIUM_PRIMARY_CNI:-}" == "true" ]] || {
  echo "Cilium must be explicitly approved as the primary CNI." >&2
  exit 78
}
[[ "${ESP_CONFIRM_BARE_METAL_LOAD_BALANCER:-}" == "true" ]] || {
  echo "MetalLB/bare-metal load-balancing must be explicitly approved for the target network." >&2
  exit 78
}

for bin in kubectl helm; do
  command -v "$bin" >/dev/null || { echo "Required binary missing: $bin" >&2; exit 69; }
done

# shellcheck disable=SC1090
set -a
source "$VERSIONS_FILE"
set +a

required=(
  CILIUM_VERSION METALLB_VERSION CERT_MANAGER_VERSION GPU_OPERATOR_VERSION
  ARGOCD_VERSION CLOUDNATIVEPG_VERSION KYVERNO_VERSION KEDA_VERSION
  OTEL_COLLECTOR_VERSION PROMETHEUS_STACK_VERSION ROOK_CEPH_VERSION
)
for key in "${required[@]}"; do
  value="${!key:-}"
  [[ -n "$value" ]] || { echo "Version pin missing: $key" >&2; exit 78; }
  [[ "$value" != "latest" && "$value" != "stable" && "$value" != "main" ]] || {
    echo "Mutable version channel forbidden for $key: $value" >&2
    exit 78
  }
done

kubectl cluster-info >/dev/null

helm repo add cilium https://helm.cilium.io/ --force-update
helm repo add metallb https://metallb.github.io/metallb --force-update
helm repo add jetstack https://charts.jetstack.io --force-update
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia --force-update
helm repo add argo https://argoproj.github.io/argo-helm --force-update
helm repo add cnpg https://cloudnative-pg.github.io/charts --force-update
helm repo add kyverno https://kyverno.github.io/kyverno/ --force-update
helm repo add kedacore https://kedacore.github.io/charts --force-update
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts --force-update
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
helm repo add rook-release https://charts.rook.io/release --force-update
helm repo update

# Cilium is the authoritative cluster CNI. Exact host routing/IPAM settings are site-specific
# and must be reviewed for the physical network before production traffic is admitted.
# Hubble relay + network/security metrics are enabled from the start so policy decisions are observable.
helm upgrade --install cilium cilium/cilium \
  --namespace kube-system \
  --version "$CILIUM_VERSION" \
  --set operator.replicas=2 \
  --set prometheus.enabled=true \
  --set operator.prometheus.enabled=true \
  --set hubble.relay.enabled=true \
  --set hubble.metrics.enableOpenMetrics=true \
  --set 'hubble.metrics.enabled={dns,drop,tcp,flow,port-distribution,icmp}'

helm upgrade --install metallb metallb/metallb \
  --namespace metallb-system --create-namespace \
  --version "$METALLB_VERSION"

helm upgrade --install cert-manager jetstack/cert-manager \
  --namespace cert-manager --create-namespace \
  --version "$CERT_MANAGER_VERSION" \
  --set crds.enabled=true

helm upgrade --install gpu-operator nvidia/gpu-operator \
  --namespace gpu-operator --create-namespace \
  --version "$GPU_OPERATOR_VERSION"

helm upgrade --install argocd argo/argo-cd \
  --namespace argocd --create-namespace \
  --version "$ARGOCD_VERSION" \
  --set controller.replicas=2 \
  --set server.replicas=2 \
  --set repoServer.replicas=2

helm upgrade --install cloudnative-pg cnpg/cloudnative-pg \
  --namespace cnpg-system --create-namespace \
  --version "$CLOUDNATIVEPG_VERSION" \
  --set replicaCount=2

helm upgrade --install kyverno kyverno/kyverno \
  --namespace kyverno --create-namespace \
  --version "$KYVERNO_VERSION" \
  --set admissionController.replicas=3 \
  --set backgroundController.replicas=2

# KEDA provides queue/metric-driven scaling once the application has completed the durable
# distributed queue migration gate. Installing the operator does not enable unsafe scaling by itself.
helm upgrade --install keda kedacore/keda \
  --namespace keda --create-namespace \
  --version "$KEDA_VERSION"

helm upgrade --install otel-collector open-telemetry/opentelemetry-collector \
  --namespace observability --create-namespace \
  --version "$OTEL_COLLECTOR_VERSION" \
  --set mode=daemonset \
  --set image.repository=otel/opentelemetry-collector-k8s

helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --namespace observability --create-namespace \
  --version "$PROMETHEUS_STACK_VERSION"

# Install the Rook operator only. Creating a CephCluster is intentionally separate because
# it consumes physical disks and therefore requires explicit device inventory/owner review.
helm upgrade --install rook-ceph rook-release/rook-ceph \
  --namespace rook-ceph --create-namespace \
  --version "$ROOK_CEPH_VERSION"

kubectl wait --for=condition=Available deployment/cilium-operator -n kube-system --timeout=300s
kubectl get nodes -o wide
kubectl get pods -A

echo "ESP Kubernetes foundation installed with reviewed version pins."
echo "Application multi-replica promotion is still blocked by deploy/selfhost/control-plane.json gates."
