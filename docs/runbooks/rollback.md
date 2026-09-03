# Roll back a Renvor web property

Rollback is **redeploying the previous known-good digest**. It is not a rebuild, not a revert
awaiting a build, and not an edit on the server. If the previous digest is known, rollback is a
one-line change.

| Target | Application | Production namespace | Flux Kustomization | Host |
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

## Fastest correct path — revert the promotion commit

```sh
git fetch origin main
git switch -c "rollback-$APP" origin/main
git revert --no-edit <promotion-commit>
git push -u origin "rollback-$APP"
gh pr create --base main --title "revert: roll back $APP" --fill
```

Merge it through the same protections as any other change. Flux reconciles within its interval;
force it if the wait matters:

```sh
kubectl -n flux-system annotate kustomization "$KUSTOMIZATION" \
  reconcile.fluxcd.io/requestedAt="$(date -u +%FT%TZ)" --overwrite
kubectl -n flux-system get kustomization "$KUSTOMIZATION" -w
```

**Do not** `kubectl set image`, `kubectl edit`, or `kubectl rollout undo`. Flux will reconcile
the change away at its next pass — so the fix appears to work, then silently reverts, usually
while nobody is looking. The change has to happen in Git.

## Removing the deployment entirely

To stop serving the selected host — returning it to HTTP 404 from Traefik's default backend:

```sh
# 1. Delete the Kustomization. Pruning removes the objects it created, and only those.
kubectl -n flux-system delete kustomization "$KUSTOMIZATION"

# 2. Confirm the namespace emptied of application objects.
kubectl -n "$NAMESPACE" get deployment,replicaset,pod,service,serviceaccount,networkpolicy,\
resourcequota,limitrange,ingressroute,middleware,certificate,issuer

# 3. Confirm the external state returned.
curl -s -o /dev/null -w '%{http_code}\n' "http://$HOST/"     # expect 404
```

Deleting the Kustomization is what makes pruning safe: Flux removes only objects carrying its
ownership label for that Kustomization. Workloads outside Renvor carry no such label — and, more
decisively, `renvor-reconciler` holds no authority in their namespaces at all.

### The namespace does NOT drain, and should not

The four Renvor namespaces, the `renvor-reconciler` ServiceAccount, and its Roles and RoleBindings
come from the **hand-applied bootstrap**, not from any Kustomization. Nothing prunes them, so after
step 1 the selected namespace still exists — emptied of application objects, with its Pod Security
labels and RBAC intact.

That is correct rather than leftover. The namespace is the boundary reconciliation runs inside;
if rollback removed it, the next deployment would have to recreate it, which needs the
cluster-wide namespace authority this design deliberately removes.

**To remove the tenancy boundary as well** — only when retiring all Renvor properties from this
cluster entirely, and only once all four Renvor Kustomizations are gone:

```sh
kubectl delete -f clusters/hostinger/flux-system/renvor-tenancy.yaml
```

Deleting a namespace deletes everything still inside it, including TLS Secrets cert-manager
issued. Do not reach for this to fix a bad deploy: a fresh certificate then has to be issued, and
Let's Encrypt's rate limits are counted per registered domain per week.

**Staging is kept unless it is itself the problem.** If production failed and staging is healthy,
staging is the best available evidence of what went wrong.

## What rollback does not fix

- **A bad certificate.** Rolling back the image does not reissue TLS. See `certificates.md`.
- **A DNS fault.** DNS is outside this repository and outside this cluster.
- **A cached HSTS policy.** This is why HSTS is not enabled until a trusted certificate has been
  serving and a renewal has been observed: once a browser has cached it, there is no way to reach
  that browser to say otherwise.
