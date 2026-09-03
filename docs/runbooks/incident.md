# Incident checks — Renvor web properties

Ordered outside-in, because the fastest way to lose time is to debug a cluster when the fault is
in DNS.

| Target | Application | Namespace | Flux Kustomization | Host |
|---|---|---|---|---|
| Landing site | `renvor-site` | `renvor-site` | `renvor-site-production` | `renvor.dev` |
| Documentation | `renvor-docs` | `renvor-docs` | `renvor-docs-production` | `docs.renvor.dev` |

Choose the affected target:

```sh
APP=renvor-docs                       # or renvor-site
NAMESPACE=renvor-docs                 # or renvor-site
KUSTOMIZATION=renvor-docs-production  # or renvor-site-production
HOST=docs.renvor.dev                  # or renvor.dev
```

## 1. Is it actually down, and from where?

```sh
curl -sS -o /dev/null -w 'http=%{http_code} tls=%{ssl_verify_result} ip=%{remote_ip}\n' "https://$HOST/"
dig +short "$HOST" A
dig +short "$HOST" AAAA
```

An unexpected AAAA record can be a silent outage for IPv6 users. `ssl_verify_result=0` means the
certificate is trusted. A non-zero value with a working HTTP code usually means cert-manager
issued from the **staging** directory, or the certificate expired.

## 2. Is the request reaching Traefik?

```sh
kubectl -n kube-system get pods -l app.kubernetes.io/name=traefik
kubectl -n kube-system logs deploy/traefik --tail=50 | grep -i "$APP"
kubectl -n "$NAMESPACE" get ingressroute,middleware
```

Traefik is **shared with unrelated workloads**. Do not restart, reconfigure, or upgrade it. If
Traefik itself is unhealthy the fault is bigger than Renvor and this runbook is the wrong one.

## 3. Are the pods serving?

```sh
kubectl -n "$NAMESPACE" get pods -o wide
kubectl -n "$NAMESPACE" describe pod | sed -n '/Events/,$p'
kubectl -n "$NAMESPACE" logs -l "app.kubernetes.io/name=$APP" --tail=50
```

Expect two Running pods with zero restarts. **A restart count above zero is a finding**: these
static servers do nothing that should make them exit.

`OOMKilled` means the container exceeded its declared memory limit. Reproduce and measure the
load, then raise the limit in Git with that measurement recorded; never use `kubectl edit`.

`Evicted` is a node-level event: this node has previously evicted pods under `DiskPressure`.
Both Deployments declare an ephemeral-storage request to rank above the best-effort tier, so an
eviction here means the node is in genuine trouble.

## 4. Is Flux reconciling?

```sh
kubectl -n flux-system get kustomization,gitrepository
kubectl -n flux-system describe kustomization "$KUSTOMIZATION" | sed -n '/Status/,$p'
kubectl -n flux-system logs deploy/kustomize-controller --tail=50
```

A `NotReady` Kustomization with a health-check failure means Flux applied the manifests and the
workload did not become healthy — that is the controller doing its job, not the fault.

### A reconciliation that fails with `forbidden`

Reconciliation runs as `flux-system/renvor-reconciler`, not as the controller's own identity.
That account is deliberately limited to the resource types used in four Renvor namespaces, so a
manifest introducing a **new kind** fails closed.

Confirm the exact missing permission with:

```sh
kubectl auth can-i create cronjobs -n "$NAMESPACE" \
  --as=system:serviceaccount:flux-system:renvor-reconciler
```

The fix is to add the resource to the relevant Roles in
`clusters/hostinger/flux-system/renvor-tenancy.yaml`, have it reviewed as the RBAC change it is,
and apply it by hand — the reconciler cannot widen its own permissions, which is the point.
`scripts/check-tenancy.py` fails until the grants and manifests agree in both directions.

**Never** work around this by deleting `serviceAccountName` from a Kustomization or pointing it
at another account. Either silently restores cluster-admin reconciliation of a public repository
onto a cluster shared with unrelated production workloads.

## 5. Is the right image running?

```sh
kubectl -n "$NAMESPACE" get pod \
  -o jsonpath='{range .items[*]}{.status.containerStatuses[*].imageID}{"\n"}{end}'
```

This must be a `@sha256:` digest matching the promoted one. **If it is a tag, something applied
outside GitOps** — find out what, because the running content can then change without a commit.

## Escalation

| Symptom | Runbook |
|---|---|
| Certificate untrusted, expired, or failing renewal | `certificates.md` |
| A bad deploy is live | `rollback.md` |
| Wrong or unexpected digest | `promote.md` |
| Traefik, cert-manager, or the node itself unhealthy | Out of scope. Shared infrastructure; do not restart or reconfigure it to fix Renvor |

## What does not exist yet

Stated so nobody looks for it during an incident:

- **no alerting** — notification-controller is not installed;
- **no metrics or dashboards** for these workloads;
- **no log aggregation** — `kubectl logs` is the whole story, and it is lost when a pod is
  replaced;
- **no uptime monitoring** from outside the cluster.

An incident is currently found by a human looking, and that is a real gap rather than an
oversight.
