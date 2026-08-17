# Incident checks — the landing site

Ordered outside-in, because the fastest way to lose time is to debug a cluster when the fault
is in DNS.

## 1. Is it actually down, and from where?

```sh
curl -sS -o /dev/null -w 'http=%{http_code} tls=%{ssl_verify_result} ip=%{remote_ip}\n' https://renvor.dev/
dig +short renvor.dev A
dig +short renvor.dev AAAA        # expect empty — an AAAA pointing elsewhere is a silent outage for v6 users
```

`ssl_verify_result=0` means the certificate is trusted. A non-zero value with a working HTTP
code usually means cert-manager issued from the **staging** directory, or the certificate
expired.

## 2. Is the request reaching Traefik?

```sh
kubectl -n kube-system get pods -l app.kubernetes.io/name=traefik
kubectl -n kube-system logs deploy/traefik --tail=50 | grep -i renvor
kubectl -n renvor-site get ingressroute,middleware
```

Traefik is **shared with unrelated workloads**. Do not restart, reconfigure, or upgrade it. If
Traefik itself is unhealthy the fault is bigger than Renvor and this runbook is the wrong one.

## 3. Are the pods serving?

```sh
kubectl -n renvor-site get pods -o wide
kubectl -n renvor-site describe pod | sed -n '/Events/,$p'
kubectl -n renvor-site logs -l app.kubernetes.io/name=renvor-site --tail=50
```

Expect two Running pods with zero restarts. **A restart count above zero is a finding**: this
server does nothing that should make it exit.

`OOMKilled` means real traffic exceeded the measured 20.6 MiB peak by more than 4x — raise the
limit in Git with the new measurement recorded, never with `kubectl edit`.

`Evicted` is a node-level event: this node has evicted 524 pods under `DiskPressure` before.
The Deployment declares an ephemeral-storage request specifically to rank above that tier, so
an eviction here means the node is in genuine trouble.

## 4. Is Flux reconciling?

```sh
kubectl -n flux-system get kustomization,gitrepository
kubectl -n flux-system describe kustomization renvor-site-production | sed -n '/Status/,$p'
kubectl -n flux-system logs deploy/kustomize-controller --tail=50
```

A `NotReady` Kustomization with a health-check failure means Flux applied the manifests and the
workload did not become healthy — that is the controller doing its job, not the fault.

### A reconciliation that fails with `forbidden`

Reconciliation runs as `flux-system/renvor-reconciler`, not as the controller's own identity.
That account is deliberately limited to ten resource types in the two Renvor namespaces, so a
manifest introducing a **new kind** fails closed:

```
Kustomization/renvor-site-production ... failed: ... is forbidden:
User "system:serviceaccount:flux-system:renvor-reconciler" cannot create
resource "cronjobs" in API group "batch" in the namespace "renvor-site"
```

**That is the boundary working, not a bug.** Confirm the exact permission with:

```sh
kubectl auth can-i create cronjobs -n renvor-site \
  --as=system:serviceaccount:flux-system:renvor-reconciler
```

The fix is to add the resource to **both** Roles in
`clusters/hostinger/flux-system/renvor-tenancy.yaml`, have it reviewed as the RBAC change it
is, and apply it by hand — the reconciler cannot widen its own permissions, which is the
point. `scripts/check-tenancy.py` fails until the grants and the manifests agree in *both*
directions, so a permission cannot be added without a manifest that needs it, and a manifest
cannot be added without the permission.

**Never** work around this by deleting `serviceAccountName` from the Kustomization or pointing
it at another account. Either silently restores cluster-admin reconciliation of a public
repository onto a cluster shared with unrelated production workloads.

## 5. Is the right image running?

```sh
kubectl -n renvor-site get pod -o jsonpath='{range .items[*]}{.status.containerStatuses[*].imageID}{"\n"}{end}'
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
- **no metrics or dashboards** for this workload;
- **no log aggregation** — `kubectl logs` is the whole story, and it is lost when a pod is
  replaced;
- **no uptime monitoring** from outside the cluster.

An incident is currently found by a human looking, and that is a real gap rather than an
oversight.
