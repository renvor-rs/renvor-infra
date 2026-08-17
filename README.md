<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/renvor-lockup-v21-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="assets/renvor-lockup-v21-light.svg">
    <img alt="Renvor" src="assets/renvor-lockup-v21-light.svg" width="360">
  </picture>
</p>

<h1 align="center">Renvor — infra</h1>

<p align="center">
  Kubernetes deployment configuration and public operational documentation for Renvor.
</p>

---

> ## Status
>
> **Declared, not yet reconciled.** This repository now contains the complete Kustomize
> definition for the landing site — base, staging overlay, production overlay, and the Flux
> bootstrap — but **nothing has been applied to any cluster from it yet**, and
> `https://renvor.dev` does not serve Renvor content.
>
> This notice is updated only after a deployment has been verified from outside the cluster.
> Until then, treat every statement below as a description of intent.

## Layout

```text
apps/renvor-site/
  base/                    ServiceAccount, Deployment, Service, NetworkPolicy,
                           ResourceQuota, LimitRange — no namespace, no image
  overlays/staging/        namespace renvor-site-staging, 1 replica, NO ingress
  overlays/production/     namespace renvor-site, 2 replicas, Issuer, Certificate,
                           IngressRoutes, Middlewares
clusters/hostinger/
  gitrepository.yaml       the one source Flux reads — public, no credential
  staging.yaml             Kustomization, prune + wait + health checks
  production.yaml          Kustomization, dependsOn staging
  flux-system/             the bootstrap manifest, applied by hand once
docs/runbooks/             promote, rollback, certificates, incident checks
```

The **base carries no namespace and no image digest**. Each overlay supplies both, so an
overlay that forgets either fails to build rather than deploying into the wrong place or from
an unpinned reference.

## Rules every workload here satisfies

Not aspirations — each is in the manifests and checked in review:

- runs **non-root** as UID/GID 65532, `allowPrivilegeEscalation: false`, all capabilities
  dropped, `seccompProfile: RuntimeDefault`;
- `readOnlyRootFilesystem: true` with **no** writable volume, because the server writes
  nothing;
- explicit CPU, memory, **and ephemeral-storage** requests and limits — the last of these
  because this node has evicted 524 pods under `DiskPressure`, and every eviction message named
  the same cause: `request is 0`;
- **startup, readiness, and liveness probes**, with different thresholds because they answer
  different questions;
- **digest-pinned images**. No tag reaches a Deployment, ever;
- **default-deny NetworkPolicy** in both directions, with exactly one ingress allowance for
  Traefik and **no egress at all**;
- a dedicated ServiceAccount with `automountServiceAccountToken: false`;
- namespaces labelled for **Pod Security Admission `restricted`, enforced**;
- no PersistentVolumeClaim, no Secret carrying application data, no ConfigMap of application
  configuration — the server's configuration is baked into the image by the build that produced
  its content.

### No PodDisruptionBudget, and that is deliberate

The cluster has one node. Any PDB over a two-replica Deployment on a single node blocks
`kubectl drain` permanently — evicting the second pod always violates the budget, because
there is nowhere for the first to reschedule. It would not protect availability; it would
prevent node maintenance. Added when a second node makes it mean something.

## GitOps

**Flux v2.9.4, two controllers only:** `source-controller` and `kustomize-controller`. No
helm-controller, no notification-controller, and specifically no image-automation-controller —
that one writes commits back to Git and would need a credential this deployment does not have
and must not acquire.

The source is the **public** `https://github.com/renvor-rs/renvor-infra` over HTTPS with **no
credential**: no deploy key, no PAT, no `secretRef`. A credential that does not exist cannot
leak and needs no rotation schedule.

Flux reconciles **one path**, `clusters/hostinger`, into **two namespaces**,
`renvor-site-staging` and `renvor-site`. Pruning is enabled and is scoped by Flux's own
ownership labels, so it can only delete objects it created — the unrelated workloads sharing
this cluster are invisible to it.

Provenance for the bootstrap manifest, including the verified signature, is in
[`clusters/hostinger/flux-system/README.md`](clusters/hostinger/flux-system/README.md).

## Shared cluster, additive changes

Renvor is a guest here. It installs no distribution, adds no second ingress controller, and
upgrades nothing. It uses the existing **Traefik 3.6.13** and **cert-manager v1.20.2** through
their public APIs, and creates only namespaced objects — plus the two Flux controllers, which
live in their own namespace.

TLS is issued by a **namespace-scoped Issuer**, not a new ClusterIssuer. A ClusterIssuer is a
cluster-wide object, and editing one to suit Renvor would put every other certificate on this
shared cluster at risk of a Renvor mistake.

### DNS is resolution only

DNS for the Renvor hostnames is served by Cloudflare in **DNS-only mode**. Cloudflare is not in
the HTTP request path: no proxy, no Tunnel, no edge termination. TLS is terminated at the origin
by cert-manager.

The consequence is worth stating plainly: **every protection in the request path is this
repository's responsibility**, expressed as Traefik configuration and workload behaviour rather
than vendor settings. There is no WAF, no edge rate limiting, no bot management, and no DDoS
absorption. That is the trade — the request path stays in version control.

### The Content-Security-Policy is not set here

It is generated from the built site — it contains SHA-256 hashes of that build's inline
scripts — and is served by the origin from configuration baked into the image. Setting it in a
Traefik middleware as well would create a second copy that drifts the moment the site changes,
and the failure mode of a stale hash-based CSP is a blank page. One canonical policy, generated
from the bytes it protects. The image publishes it at `/_csp/policy.txt`.

## Domains in scope

| Hostname | Handled here |
|---|---|
| `renvor.dev` | Yes — the landing site |
| `www.renvor.dev` | Yes — permanent 301 to `https://renvor.dev`, path and query preserved |
| `docs.renvor.dev` | **No.** `renvor-rs/renvor-docs` is commit-empty and its migration is gated on T108. No route is created for it, and claiming the hostname would make an open gate look closed |

## Secrets

**No plaintext secret may ever be committed to this repository.** No credential, token, key,
kubeconfig, or `.env` value. No privileged workload, and no host filesystem mount without
independent justification. See `.gitignore` for the enforced patterns.

The two Secrets that exist at runtime — the ACME account key and the TLS certificate — are
created and managed by cert-manager inside the cluster. **Their contents are never read,
printed, logged, or committed.** Verification looks at a Certificate's `Ready` condition and at
what the server presents on the wire.

## Related repositories

| Repository | Contents |
|---|---|
| [`renvor-rs/renvor`](https://github.com/renvor-rs/renvor) | Framework source, governance, and decision records |
| [`renvor-rs/renvor-site`](https://github.com/renvor-rs/renvor-site) | The landing page, and the workflow that publishes its image |
| [`renvor-rs/renvor-docs`](https://github.com/renvor-rs/renvor-docs) | Reserved for the documentation site. Commit-empty |

## Licence

**Manifest and runbook licence: undecided.** The framework's `MIT OR Apache-2.0` grant covers
[`renvor-rs/renvor`](https://github.com/renvor-rs/renvor) and does **not** extend to this
repository. Until a licence file is added here, **no permission to reuse these manifests or
runbooks is granted.**

**Brand assets: all rights reserved.** The lockup above, the Renvor name, wordmarks, and visual
identity are not covered by any code licence. Brand usage terms are held in
[`renvor-rs/renvor-site`](https://github.com/renvor-rs/renvor-site).
