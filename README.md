<p align="center">
  <img src="assets/renvor-mark-v7.svg" alt="Renvor" width="120">
</p>

<h1 align="center">Renvor — infra</h1>

<p align="center">
  Kubernetes deployment configuration and public operational documentation for Renvor.
</p>

---

> ## Nothing is deployed by this repository
>
> This repository currently contains **no Kubernetes manifest, no deployment workflow, and no
> server configuration**. It defines the intended shape of Renvor's deployment and the rules
> that shape must satisfy. Nothing here is applied to any cluster.

## Purpose

`renvor-infra` holds the Kubernetes deployment configuration and the public operational
documentation for the Renvor project. It is the place where the deployed request path is
described in version control rather than in a vendor console.

It deliberately does **not** hold application source. The framework, landing site, and
documentation each live in their own repository.

## Intended architecture

```text
Browser
  → public DNS
  → origin
  → Traefik ingress on the existing Kubernetes cluster
  ├── renvor.dev       → landing service
  ├── docs.renvor.dev  → documentation service
  └── www.renvor.dev   → permanent redirect to renvor.dev

cert-manager
  → publicly trusted certificates for every deployed hostname
```

- **Source** — public GitHub repositories.
- **Images** — public static-site images published to the GitHub Container Registry (`ghcr.io`).
  Because the images are public, the cluster needs no registry pull credential.
- **Kubernetes** — an existing cluster. Renvor installs no distribution and adds no second
  ingress controller.
- **Ingress** — Traefik, already present and serving.
- **TLS** — cert-manager, issuing publicly trusted certificates at the origin.

### DNS is resolution only

DNS for the Renvor hostnames is served by Cloudflare in **DNS-only mode**. Cloudflare is not
in the HTTP request path: there is no proxy, no Tunnel, and no edge termination. TLS is
terminated at the origin by cert-manager.

The consequence is deliberate and worth stating plainly: **every protection in the request
path is this repository's responsibility**, expressed as Traefik configuration and workload
behaviour rather than vendor settings. That is the trade — the request path stays in version
control.

### Additive and reversible

Renvor's deployment is **additive** to a cluster it does not own, and every change must be
reversible. It reuses what is already proven rather than replacing it. Where a choice exists
between the ideal design and one that can be rolled back cleanly, the reversible one wins.

## Requirements for manifests, when they land here

Any workload added to this repository must satisfy all of the following:

- runs **non-root**, with `allowPrivilegeEscalation: false` and dropped capabilities;
- `readOnlyRootFilesystem` wherever the workload permits it;
- `RuntimeDefault` seccomp profile;
- explicit CPU and memory **requests and limits**;
- **readiness, liveness, and startup probes**;
- **digest-pinned images** — a tag may accompany the digest, but the digest is what deploys;
- minimal static-content images;
- **NetworkPolicy** governing ingress and egress;
- separate namespaces and service accounts, with no default service-account token mount
  unless the workload genuinely requires one;
- **SBOM, build provenance, and vulnerability scanning** for every published image;
- a tested rollback path.

### Secrets

**No plaintext secret may ever be committed to this repository.** No credential, token, key,
kubeconfig, or `.env` value. No privileged workload, and no host filesystem mount without
independent justification. See `.gitignore` for the enforced patterns.

## Related repositories

| Repository | Contents |
|---|---|
| [`renvor-rs/renvor`](https://github.com/renvor-rs/renvor) | Framework source, governance, and decision records |
| [`renvor-rs/renvor-site`](https://github.com/renvor-rs/renvor-site) | Landing page served at `renvor.dev` |
| [`renvor-rs/renvor-docs`](https://github.com/renvor-rs/renvor-docs) | Documentation site served at `docs.renvor.dev` |

## Licence

**Manifest and runbook licence: undecided.** The framework's `MIT OR Apache-2.0` grant covers
[`renvor-rs/renvor`](https://github.com/renvor-rs/renvor) and does **not** extend to this
repository. Until a licence file is added here, **no permission to reuse these manifests or
runbooks is granted.**

**Brand assets: all rights reserved.** The mark above, the Renvor name, wordmarks, and visual
identity are not covered by any code licence. Brand usage terms are held in
[`renvor-rs/renvor-site`](https://github.com/renvor-rs/renvor-site).
