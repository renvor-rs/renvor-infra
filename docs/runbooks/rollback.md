# Roll back the landing site

Rollback is **redeploying the previous known-good digest**. It is not a rebuild, not a revert
awaiting a build, and not an edit on the server. If the previous digest is known, rollback is a
one-line change.

## Fastest correct path — revert the promotion commit

```sh
git revert --no-edit <promotion-commit>
git push origin HEAD:refs/heads/rollback-renvor-site
gh pr create --base main --title 'revert: roll back the landing image' --fill
```

Merge it through the same protections as any other change. Flux reconciles within its interval;
force it if the wait matters:

```sh
kubectl -n flux-system annotate kustomization renvor-site-production \
  reconcile.fluxcd.io/requestedAt="$(date -u +%FT%TZ)" --overwrite
kubectl -n flux-system get kustomization renvor-site-production -w
```

**Do not** `kubectl set image`, `kubectl edit`, or `kubectl rollout undo`. Flux will reconcile
the change away at its next pass — so the fix appears to work, then silently reverts, usually
while nobody is looking. The change has to happen in Git.

## Removing the deployment entirely

To return the cluster to its pre-deployment state — `renvor.dev` answering HTTP 404 from
Traefik's default backend:

```sh
# 1. Delete the Kustomization. Pruning removes the objects it created, and only those.
kubectl -n flux-system delete kustomization renvor-site-production

# 2. Confirm the namespace drained.
kubectl get ns renvor-site
kubectl -n renvor-site get all,ingressroute,middleware,certificate,issuer

# 3. Confirm the external state returned.
curl -s -o /dev/null -w '%{http_code}\n' http://renvor.dev/     # expect 404
```

Deleting the Kustomization is what makes pruning safe: Flux removes only objects carrying its
ownership label for that Kustomization. The workloads in `gitlab`, `attaa`, `codexhub`, and
`portfolio` carry no such label and are untouched.

**Staging is kept unless it is itself the problem.** If production failed and staging is
healthy, staging is the best available evidence of what went wrong.

## What rollback does not fix

- **A bad certificate.** Rolling back the image does not reissue TLS. See `certificates.md`.
- **A DNS fault.** DNS is outside this repository and outside this cluster.
- **A cached HSTS policy.** This is why HSTS is not enabled until a trusted certificate has
  been serving and a renewal has been observed: once a browser has cached it, there is no way
  to reach that browser to say otherwise.
