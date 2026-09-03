<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/assets/renvor-infra-readme-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset=".github/assets/renvor-infra-readme-light.svg">
  <img alt="Renvor Infra" src=".github/assets/renvor-infra-readme-light.svg" width="100%">
</picture>

<h1 align="center">Renvor — infra</h1>

<p align="center">
  Kubernetes deployment configuration and public operational documentation for Renvor.
</p>

<p align="center">
  <a href="https://github.com/renvor-rs/renvor-infra/actions/workflows/infra-ci.yml"><img alt="Infrastructure CI" src="https://github.com/renvor-rs/renvor-infra/actions/workflows/infra-ci.yml/badge.svg?branch=main"></a>
  <a href="clusters/hostinger/flux-system/README.md"><img alt="Flux 2.9.4" src="https://img.shields.io/badge/Flux-2.9.4-5468FF.svg?logo=flux&amp;logoColor=white"></a>
  <a href=".github/workflows/infra-ci.yml"><img alt="Kubernetes 1.35.5" src="https://img.shields.io/badge/Kubernetes-1.35.5-326CE5.svg?logo=kubernetes&amp;logoColor=white"></a>
</p>

---

> ## Status
>
> **Deployed and reconciling.** `https://renvor.dev` serves the Renvor landing site, over a
> Let's Encrypt certificate, from this repository.
>
> | | |
> |---|---|
> | Image | `ghcr.io/renvor-rs/renvor-site@sha256:649394aaef1ea1334417f86e471cc0f660eb178d417022bbdca01f47cddff11b` |
> | Certificate | Let's Encrypt `CN=YE1`, `renvor.dev` + `www.renvor.dev`, expires 2026-11-15 |
> | Documentation image | `ghcr.io/renvor-rs/renvor-docs@sha256:9240f8621a7bbfe735cb895298cc9fe6a75572e2e011a68d4405e11ee69ebfcd` |
> | Documentation certificate | Let's Encrypt `CN=YE1`, `docs.renvor.dev`, expires 2026-12-02 |
> | Reconciled by | Flux v2.9.4 as `flux-system/renvor-reconciler` — never as cluster-admin |
>
> Verified from outside the cluster: TLS chain `Verify return code: 0 (ok)` over TLSv1.3;
> `http://` and `https://www.` both 301 to `https://renvor.dev` preserving path and query;
> the `Content-Security-Policy` header byte-identical to the published `/_csp/policy.txt`;
> the digest running in both namespaces identical to the one whose provenance was verified.
> `https://docs.renvor.dev` also returns 200 with a trusted production certificate; its staging
> and production pods run the same reviewed digest with zero restarts.
>
> **HSTS is not enabled yet.** It is close to irreversible once a browser caches it, so it
> waits until a renewal has been observed to succeed. See `overlays/production/middleware.yaml`.

## Layout

```text
apps/renvor-site/                RECONCILED FROM GIT — namespaced resources only
  base/                    ServiceAccount, Deployment, Service, NetworkPolicy,
                           ResourceQuota, LimitRange — no namespace, no image
  overlays/staging/        1 replica, NO ingress — no Namespace resource
  overlays/production/     2 replicas, Issuer, Certificate, IngressRoutes,
                           Middlewares — no Namespace resource
apps/renvor-docs/                RECONCILED FROM GIT — an isolated documentation workload
  base/                    ServiceAccount, Deployment, Service, NetworkPolicy,
                           ResourceQuota, LimitRange — no namespace, no image
  overlays/staging/        1 replica, NO ingress — no Namespace resource
  overlays/production/     2 replicas, Issuer, Certificate, IngressRoutes,
                           Middlewares — no Namespace resource
clusters/hostinger/              APPLIED BY HAND — never reconciled, and excluded
                                 from the artifact Flux fetches
  gitrepository.yaml       the one source Flux reads — public, no credential
  staging.yaml             landing-site staging Kustomization
  production.yaml          landing-site production, dependsOn its staging
  docs-staging.yaml        documentation staging Kustomization
  docs-production.yaml     documentation production, dependsOn its staging
  flux-system/             kustomization.yaml applies the two below:
    flux-system.yaml         upstream Flux v2.9.4, byte-identical, signature verified
    renvor-tenancy.yaml      the 4 namespaces, the reconciler ServiceAccount,
                             and its 4 Roles + 4 RoleBindings
scripts/                   check-tenancy.py and its negative controls
docs/runbooks/             promote, rollback, certificates, incident checks
```

The **base carries no namespace and no image digest**. Each overlay supplies the digest, so an
overlay that forgets it fails to build rather than deploying from an unpinned reference.

**Neither overlay declares a Namespace**, and that is a boundary rather than an omission — see
[Soft multi-tenancy](#soft-multi-tenancy--stated-precisely) below. The namespaces are created
once by the hand-applied bootstrap, and reconciliation has no authority to create, relabel, or
prune them.

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

Flux reconciles **four paths** under `apps/`: landing-site and documentation overlays for staging
and production. They target `renvor-site-staging`, `renvor-site`, `renvor-docs-staging`, and
`renvor-docs`.

### Soft multi-tenancy — stated precisely

An earlier version of this README claimed the unrelated workloads on this cluster were
"invisible" to Flux. **That was wrong, and the distinction matters.**

Upstream Flux binds `kustomize-controller` to **`cluster-admin`**, and that binding is retained
here. It has to be: Kubernetes impersonation requires the impersonator to already hold the
rights it delegates, so the controller cannot grant `renvor-reconciler` its permissions without
holding them itself. The Flux controller can therefore see and act on this entire cluster.

What is constrained is **what the public repository can cause it to do**:

| | |
|---|---|
| Repository-driven applies run as | `flux-system/renvor-reconciler` — never the controller's own identity |
| That identity may write | 10 resource types, in 4 namespaces |
| That identity may read Secrets | **No** — in no namespace, in no verb |
| Namespaces, nodes, CRDs, RBAC, every cluster-scoped resource | **Denied** |
| `--default-service-account=renvor-reconciler` | so omitting `serviceAccountName` falls back to the *restricted* identity rather than to cluster-admin |
| `--no-cross-namespace-refs=true`, `--no-remote-bases=true` | no reaching sideways, no fetching bases from outside the reviewed artifact |
| `/clusters` in the fetched artifact | **Absent.** The control plane is not merely unreconciled — it is not in the bytes Flux downloads, so a commit cannot edit the objects that constrain commits |

This is **soft multi-tenancy**. A malicious or mistaken commit to the public repository is
contained by it. A compromise of the `kustomize-controller` process itself is **not** — that
process still holds cluster-admin. Hard multi-tenancy would need a separate cluster, and this
project does not claim to have one.

The tenancy boundary — the four namespaces, the ServiceAccount, and the four Roles and
RoleBindings — is created by the hand-applied bootstrap in
[`clusters/hostinger/flux-system/renvor-tenancy.yaml`](clusters/hostinger/flux-system/renvor-tenancy.yaml),
deliberately **not** by the overlays. A reconciler that could create the namespace it runs in
would need cluster-wide namespace authority, which is exactly what this design removes. The
overlays reconcile strictly inside a boundary they have no power to move.

Every one of these properties is asserted by `scripts/check-tenancy.py` in CI, and every one of
those assertions is proven to fail against a deliberately broken tree by
`scripts/check-tenancy-controls.py`.

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
| `docs.renvor.dev` | Yes — a dedicated documentation workload, certificate, redirect, and HTTPS route owned by `apps/renvor-docs/overlays/production` |

## Secrets

**No plaintext secret may ever be committed to this repository.** No credential, token, key,
kubeconfig, or `.env` value. No privileged workload, and no host filesystem mount without
independent justification. See `.gitignore` for the enforced patterns.

The ACME account keys and TLS certificates that exist at runtime are created and managed by
cert-manager inside the production namespaces. **Their contents are never read, printed, logged,
or committed.** Verification looks at each Certificate's `Ready` condition and at what the server
presents on the wire.

## Related repositories

| Repository | Contents |
|---|---|
| [`renvor-rs/renvor`](https://github.com/renvor-rs/renvor) | Framework source, governance, and decision records |
| [`renvor-rs/renvor-site`](https://github.com/renvor-rs/renvor-site) | The landing page, and the workflow that publishes its image |
| [`renvor-rs/renvor-docs`](https://github.com/renvor-rs/renvor-docs) | Docusaurus documentation source and immutable image-publishing workflow |

## Licence

**Manifest and runbook licence: undecided.** The framework's `MIT OR Apache-2.0` grant covers
[`renvor-rs/renvor`](https://github.com/renvor-rs/renvor) and does **not** extend to this
repository. Until a licence file is added here, **no permission to reuse these manifests or
runbooks is granted.**

**Brand assets: all rights reserved.** The lockup above, the Renvor name, wordmarks, and visual
identity are not covered by any code licence. Brand usage terms are held in
[`renvor-rs/renvor-site`](https://github.com/renvor-rs/renvor-site).
