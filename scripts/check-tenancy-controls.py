#!/usr/bin/env python3
"""Negative controls for `scripts/check-tenancy.py`.

A gate nobody has watched fail is not a gate. This script copies the repository into a
temporary directory, breaks one property of the tenancy boundary at a time, and asserts that
the checker rejects each break. If any tampered tree still passes, that check is dead and this
script fails the build.

It also re-runs the checker on an untampered copy, so a checker that rejected *everything*
would be caught too.

    python3 scripts/check-tenancy-controls.py
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
TENANCY = "clusters/hostinger/flux-system/renvor-tenancy.yaml"
STAGING_KUST = "clusters/hostinger/staging.yaml"
GITREPO = "clusters/hostinger/gitrepository.yaml"
BOOTSTRAP = "clusters/hostinger/flux-system/kustomization.yaml"

# The rendered overlays to hand the checker, taken from argv so CI and a local run can each use
# whatever filenames their render step produced. The first is the one the two "something
# forbidden reappears in a reconciled overlay" controls append to.
# Arguments are `namespace=path`, matching check-tenancy.py — staging and production Roles are
# deliberately different, so the checker needs to know which render belongs to which namespace.
ARGS = sys.argv[1:] or [
    "renvor-site-staging=rendered/site-staging.yaml",
    "renvor-site=rendered/site-production.yaml",
    "renvor-docs-staging=rendered/docs-staging.yaml",
    "renvor-docs=rendered/docs-production.yaml",
]
OVERLAY_FOR = dict(a.split("=", 1) for a in ARGS)
RENDERS = list(OVERLAY_FOR.values())
STAGING_RENDER = OVERLAY_FOR["renvor-site-staging"]
# The two production renders carry the public routes and certificate requests.
PROD_RENDER = OVERLAY_FOR["renvor-site"]
DOCS_PROD_RENDER = OVERLAY_FOR["renvor-docs"]


def edit(root: pathlib.Path, rel: str, fn) -> None:
    p = root / rel
    p.write_text(fn(p.read_text()))


# Each control is (name, mutation). The mutation must break exactly one property.
CONTROLS = [
    (
        "a Kustomization omits serviceAccountName (the fail-open case)",
        lambda r: edit(r, STAGING_KUST,
                       lambda t: t.replace("  serviceAccountName: renvor-reconciler\n", "")),
    ),
    (
        "a Kustomization names a different identity",
        lambda r: edit(r, STAGING_KUST,
                       lambda t: t.replace("serviceAccountName: renvor-reconciler",
                                           "serviceAccountName: default")),
    ),
    (
        "the Role grants access to Secrets",
        lambda r: edit(r, TENANCY,
                       lambda t: t.replace(
                           "resources: ['services', 'serviceaccounts', 'limitranges', 'resourcequotas']",
                           "resources: ['services', 'serviceaccounts', 'limitranges', 'resourcequotas', 'secrets']",
                           1)),
    ),
    (
        "the Role grants a resource no overlay uses",
        lambda r: edit(r, TENANCY,
                       lambda t: t.replace(
                           "resources: ['services', 'serviceaccounts', 'limitranges', 'resourcequotas']",
                           "resources: ['services', 'serviceaccounts', 'limitranges', 'resourcequotas', 'configmaps']",
                           1)),
    ),
    (
        "the Role uses a wildcard verb set",
        lambda r: edit(r, TENANCY,
                       lambda t: t.replace(
                           "verbs: ['get', 'list', 'watch', 'create', 'update', 'patch', 'delete']",
                           "verbs: ['*']", 1)),
    ),
    (
        "the Role drops a verb reconciliation needs",
        lambda r: edit(r, TENANCY,
                       lambda t: t.replace(
                           "verbs: ['get', 'list', 'watch', 'create', 'update', 'patch', 'delete']",
                           "verbs: ['get', 'list', 'watch']", 1)),
    ),
    (
        "a RoleBinding is promoted to a ClusterRoleBinding",
        lambda r: edit(r, TENANCY,
                       lambda t: t.replace("kind: RoleBinding", "kind: ClusterRoleBinding", 1)),
    ),
    (
        "a RoleBinding points at a different ServiceAccount",
        lambda r: edit(r, TENANCY,
                       lambda t: re.sub(r"(subjects:\n  - kind: ServiceAccount\n    name: )renvor-reconciler",
                                        r"\1kustomize-controller", t, count=1)),
    ),
    (
        "a roleRef is switched to a ClusterRole",
        lambda r: edit(r, TENANCY,
                       lambda t: t.replace("  kind: Role\n  name: renvor-reconciler",
                                           "  kind: ClusterRole\n  name: cluster-admin", 1)),
    ),
    (
        "a namespace stops enforcing Pod Security restricted",
        lambda r: edit(r, TENANCY,
                       lambda t: t.replace("pod-security.kubernetes.io/enforce: restricted",
                                           "pod-security.kubernetes.io/enforce: baseline", 1)),
    ),
    (
        "the controller loses its --default-service-account fallback",
        lambda r: edit(r, BOOTSTRAP,
                       lambda t: t.replace("--default-service-account=renvor-reconciler", "")),
    ),
    (
        "the controller loses --no-remote-bases",
        lambda r: edit(r, BOOTSTRAP,
                       lambda t: t.replace("--no-remote-bases=true", "")),
    ),
    (
        "the controller loses --no-cross-namespace-refs",
        lambda r: edit(r, BOOTSTRAP,
                       lambda t: t.replace("--no-cross-namespace-refs=true", "")),
    ),
    (
        # THE FAIL-OPEN CASE A TEXT SEARCH CANNOT SEE.
        # The whole `patches:` block is deleted, so the built controller carries none of the
        # three flags — but every flag string survives inside the surrounding comments, so a
        # substring search over the file still finds all three and reports success. The built
        # cluster would silently fall back to cluster-admin reconciliation.
        "the patches block is deleted but the flags survive in comments",
        lambda r: edit(r, BOOTSTRAP, lambda t: t[:t.index("patches:")] + "patches: []\n"
                       # The flags MUST survive as comments, or this control degenerates into
                       # "the flags were deleted" — which the old substring check also caught,
                       # and which therefore proves nothing about the new structural one.
                       + "# --default-service-account=renvor-reconciler\n"
                       + "# --no-cross-namespace-refs=true\n"
                       + "# --no-remote-bases=true\n"),
    ),
    (
        # The staging and production Roles are intentionally different. If staging regained
        # traefik.io, a staging manifest could create an IngressRoute claiming
        # `Host(`renvor.dev`)` — an allow-listed
        # hostname, so the route gate would pass it — and contend with production for real
        # traffic through the shared Traefik.
        "staging regains a grant only production needs",
        lambda r: edit(r, TENANCY,
                       lambda t: t.replace(
                           "  # NO cert-manager AND NO traefik.io HERE, DELIBERATELY.",
                           "  - apiGroups: ['traefik.io']\n"
                           "    resources: ['ingressroutes', 'middlewares']\n"
                           "    verbs: ['get', 'list', 'watch', 'create', 'update', 'patch', 'delete']\n"
                           "  # NO cert-manager AND NO traefik.io HERE, DELIBERATELY.", 1)),
    ),
    (
        "the health-check read on pods is removed (wait: true would fail closed)",
        lambda r: edit(r, TENANCY,
                       lambda t: t.replace(
                           "  - apiGroups: ['']\n    resources: ['pods']\n"
                           "    verbs: ['get', 'list', 'watch']\n", "", 1)),
    ),
    (
        "the health-check read on replicasets is removed",
        lambda r: edit(r, TENANCY,
                       lambda t: t.replace(
                           "  - apiGroups: ['apps']\n    resources: ['replicasets']\n"
                           "    verbs: ['get', 'list', 'watch']\n", "", 1)),
    ),
    (
        "a health-check read is widened to a write grant",
        lambda r: edit(r, TENANCY,
                       lambda t: t.replace(
                           "    resources: ['pods']\n    verbs: ['get', 'list', 'watch']",
                           "    resources: ['pods']\n"
                           "    verbs: ['get', 'list', 'watch', 'create', 'delete']", 1)),
    ),
    (
        "the source artifact re-includes the control plane",
        lambda r: edit(r, GITREPO, lambda t: t.replace("    !/apps\n", "    !/apps\n    !/clusters\n")),
    ),
    (
        "a Namespace reappears in a reconciled overlay",
        lambda r: (r / STAGING_RENDER).write_text(
            (r / STAGING_RENDER).read_text()
            + "\n---\napiVersion: v1\nkind: Namespace\nmetadata:\n  name: renvor-site-staging\n"),
    ),
    (
        "an RBAC object appears in a reconciled overlay",
        lambda r: (r / STAGING_RENDER).write_text(
            (r / STAGING_RENDER).read_text()
            + "\n---\napiVersion: rbac.authorization.k8s.io/v1\nkind: ClusterRoleBinding\n"
              "metadata:\n  name: escalate\n"),
    ),
    (
        "a Kustomization retargets a non-Renvor namespace",
        lambda r: edit(r, STAGING_KUST,
                       lambda t: t.replace("targetNamespace: renvor-site-staging",
                                           "targetNamespace: kube-system")),
    ),
    (
        "a Kustomization reconciles a path outside apps/",
        lambda r: edit(r, STAGING_KUST,
                       lambda t: t.replace("path: ./apps/renvor-site/overlays/staging",
                                           "path: ./clusters/hostinger")),
    ),
    (
        "pruning is disabled, so deletions stop propagating",
        lambda r: edit(r, STAGING_KUST, lambda t: t.replace("prune: true", "prune: false")),
    ),
    (
        "the GitRepository acquires a credential",
        lambda r: edit(r, GITREPO,
                       lambda t: t.replace("  timeout: 60s",
                                           "  secretRef:\n    name: deploy-key\n  timeout: 60s")),
    ),
    (
        # The cross-tenant hijack: entirely inside the RBAC boundary, because Traefik routes
        # cluster-globally and does not care which namespace an IngressRoute lives in.
        "an IngressRoute claims a co-tenant's hostname",
        lambda r: edit(r, PROD_RENDER,
                       lambda t: t.replace("Host(`renvor.dev`)",
                                           "Host(`gitlab.ahmedanbar.dev`)", 1)),
    ),
    (
        "the landing-site namespace claims the documentation hostname",
        lambda r: edit(r, PROD_RENDER,
                       lambda t: t.replace("Host(`renvor.dev`)",
                                           "Host(`docs.renvor.dev`)", 1)),
    ),
    (
        "the documentation namespace claims the landing-site hostname",
        lambda r: edit(r, DOCS_PROD_RENDER,
                       lambda t: t.replace("Host(`docs.renvor.dev`)",
                                           "Host(`renvor.dev`)", 1)),
    ),
    (
        "the landing-site certificate requests the documentation hostname",
        lambda r: edit(r, PROD_RENDER,
                       lambda t: t.replace("  - www.renvor.dev\n",
                                           "  - docs.renvor.dev\n", 1)),
    ),
    (
        "the documentation certificate requests the landing-site hostname",
        lambda r: edit(r, DOCS_PROD_RENDER,
                       lambda t: t.replace(
                           "  commonName: docs.renvor.dev\n  dnsNames:\n  - docs.renvor.dev\n",
                           "  commonName: renvor.dev\n  dnsNames:\n  - renvor.dev\n", 1)),
    ),
    (
        "an IngressRoute installs a bare catch-all with no Host() term",
        lambda r: edit(r, PROD_RENDER,
                       lambda t: t.replace("match: Host(`renvor.dev`)", "match: PathPrefix(`/`)", 1)),
    ),
    (
        "an IngressRoute uses a wildcard host matcher",
        lambda r: edit(r, PROD_RENDER,
                       lambda t: t.replace("match: Host(`renvor.dev`)",
                                           "match: HostRegexp(`^.+$`)", 1)),
    ),
    (
        # The disjunction evasions. Each keeps a legitimate allow-listed Host() so a check that
        # merely looks for one still passes — while the second arm matches every hostname on
        # the cluster.
        "a match arm ORs in a catch-all PathPrefix",
        lambda r: edit(r, PROD_RENDER,
                       lambda t: t.replace("match: Host(`renvor.dev`)",
                                           "match: Host(`renvor.dev`) || PathPrefix(`/`)", 1)),
    ),
    (
        "a match arm ORs in a catch-all ClientIP",
        lambda r: edit(r, PROD_RENDER,
                       lambda t: t.replace("match: Host(`renvor.dev`)",
                                           "match: Host(`renvor.dev`) || ClientIP(`0.0.0.0/0`)", 1)),
    ),
    (
        "a match arm ORs in a Method matcher",
        lambda r: edit(r, PROD_RENDER,
                       lambda t: t.replace("match: Host(`renvor.dev`)",
                                           "match: Host(`renvor.dev`) || Method(`GET`)", 1)),
    ),
    (
        # Total inversion: matches every hostname EXCEPT ours, capturing every co-tenant.
        # The scalar must be quoted — a bare leading `!` is a YAML tag indicator.
        "a match rule is negated, inverting it onto every other host",
        lambda r: edit(r, PROD_RENDER,
                       lambda t: t.replace("match: Host(`renvor.dev`)",
                                           "match: '!Host(`renvor.dev`)'", 1)),
    ),
    (
        # A default-deny that selects zero pods denies nothing: same name, same policyTypes,
        # same empty rule lists, and the entire namespace is exempt.
        "the default-deny NetworkPolicy is narrowed to select no pods",
        lambda r: edit(r, PROD_RENDER,
                       lambda t: t.replace(
                           "  podSelector: {}\n",
                           "  podSelector:\n    matchLabels:\n      renvor.dev/nonexistent: 'true'\n",
                           1)),
    ),
    (
        # NetworkPolicies are additive: a second policy can only widen the union.
        "a second NetworkPolicy quietly re-opens egress",
        lambda r: (r / PROD_RENDER).write_text(
            (r / PROD_RENDER).read_text()
            + "\n---\napiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\n"
              "metadata:\n  name: renvor-site-allow-egress\n"
              "spec:\n  podSelector: {}\n  policyTypes: [Egress]\n  egress:\n  - {}\n"),
    ),
]


def run_checker(root: pathlib.Path) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, "scripts/check-tenancy.py", *ARGS],
                          cwd=root, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def fresh_copy(dst: pathlib.Path) -> pathlib.Path:
    root = dst / "repo"
    shutil.copytree(REPO, root, ignore=shutil.ignore_patterns(".git", "node_modules", ".venv"))
    return root


def main() -> int:
    bad: list[str] = []

    absent = [r for r in RENDERS if not (REPO / r).is_file()]
    if absent:
        print(f"missing rendered overlays: {absent}")
        print("render them first, e.g.:")
        print("  kubectl kustomize apps/renvor-site/overlays/staging    > rendered/staging.yaml")
        print("  kubectl kustomize apps/renvor-site/overlays/production > rendered/production.yaml")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        base = fresh_copy(pathlib.Path(tmp))
        code, out = run_checker(base)
        if code != 0:
            print("BASELINE FAILED — the untampered tree must pass before controls mean anything")
            print(out[-3000:])
            return 1
        print(f"baseline: PASS ({len(CONTROLS)} controls to run)\n")

    for name, mutate in CONTROLS:
        with tempfile.TemporaryDirectory() as tmp:
            root = fresh_copy(pathlib.Path(tmp))
            mutate(root)
            code, out = run_checker(root)
            if code == 0:
                bad.append(name)
                print(f"  [DEAD CHECK] {name}\n               tampered tree still PASSED")
            else:
                first = next((ln.strip() for ln in out.splitlines() if "[FAIL]" in ln), "(rejected)")
                print(f"  [caught] {name}\n           -> {first[:110]}")

    print()
    if bad:
        for b in bad:
            print(f"::error::no check detects: {b}")
        print(f"=== {len(bad)} of {len(CONTROLS)} controls were NOT caught ===")
        return 1
    print(f"=== all {len(CONTROLS)} controls caught; every tenancy check is live ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
