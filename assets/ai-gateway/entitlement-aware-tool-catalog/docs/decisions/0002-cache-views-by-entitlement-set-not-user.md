# ADR 0002: Cache views by entitlement set, not by user

- **Status:** Accepted
- **Date:** 2026-10-21
- **Scope:** The view cache and the entitlement cache in an entitlement-aware tool gateway, the cursors and change notifications that depend on them, and every replica that shares them
- **Related Documentation:** [Asset overview](../../README.md) · [Architecture](../architecture.md) · [Authentication and governance](../authentication-and-governance.md) · [Evaluation and troubleshooting](../evaluation-and-troubleshooting.md) · [ADR 0001](0001-filter-list-and-enforce-call-from-one-entitlement-decision.md)

## Context

[ADR 0001](0001-filter-list-and-enforce-call-from-one-entitlement-decision.md) makes every `tools/list` a filtered, minimized view of the catalog. Building a view walks the whole catalog, applies the decision function, picks description variants and minimizes schemas. Doing that on every request is wasteful, so views are cached. The cache key decides three things at once:

- **Correctness.** If two callers who should see different tools share a key, one is served the other's list. That leaks tool names, descriptions and schema properties across tenants or roles.
- **Reuse.** If every user gets their own key, there is almost no reuse, and the cache grows with the user base instead of with the number of distinct entitlement sets.
- **Privacy.** A key that contains user IDs, session IDs or token hashes puts personal or secret-derived data into cache memory, metrics and dumps.

Freshness is a separate concern. Licenses, feature flags and role assignments change, and a revoked tool must stop working within a bound the risk owner accepts. A time-to-live on views alone does not give that bound and cannot handle a registry that is down or a replica that is behind.

## Decision

1. **Key views by entitlement set.** `secure_view_key` is `("view", tenant_id, principal_class, entitlement_version, catalog_version)`. The tenant is the internal tenant resolved by the registry from the validated `tid`. The principal class is the caller's roles intersected with the roles the catalog references (`ToolCatalog.relevant_roles`), sorted and joined with `+`, or `base` when none remain. Users who differ only in roles no tool uses share a view.
2. **Keep user, session and token data out of the key.** No user ID, session ID or token hash appears in a view key. Scenario 5 checks that a second user with the same principal class gets a cache hit and that no key contains a user identifier.
3. **Put versions in the key instead of a TTL on views.** A view is a pure function of its key, so it never goes stale. When a tenant's entitlements change, its entitlement version changes. When the catalog is published, the catalog version changes, and `relevant_roles` may grow with it. Either way, old keys stop being requested and their entries become unreachable.
4. **Bound the view cache.** `ViewCache` holds at most `view_cache_entries` views (256 in the sample) and evicts the oldest entry first. It counts `hits` and `misses` for monitoring. Each replica can keep its own cache, or replicas can share one under the same key.
5. **Own freshness in the entitlement cache.** `EntitlementCache` is keyed by directory tenant ID and applies the rules in [Architecture §7.1](../architecture.md#71-entitlement-cache) and [§8](../architecture.md#8-freshness-staleness-and-revocation):
   - a TTL on each snapshot, chosen from the revocation latency the risk owner accepts
   - a short negative cache for tenants that are not registered
   - a bounded stale window during a registry outage, with `stale_call_scope` limiting which calls may run on a stale snapshot
   - a per-tenant version floor raised by change events, so a lagging registry replica cannot bring back a revoked entitlement
6. **Notify on digest change, and bind cursors to the digest.** Each view carries a digest of the emitted tools. `notifications/tools/list_changed` is sent only to sessions whose digest changed ([Architecture §9](../architecture.md#9-versioned-catalog-and-change-notifications)). Pagination cursors are bound to tenant, principal class and view digest ([Architecture §10](../architecture.md#10-pagination-cursors)), so a cursor from another view, or from before a change, is rejected.

The call path does not use the view cache at all. It resolves entitlements through the entitlement cache and evaluates the decision function again, as ADR 0001 requires.

## Consequences

- **Positive:** The cache grows with the number of distinct entitlement sets (tenants × principal classes × versions), not with users. All users of a tenant who hold the same relevant roles share one entry.
- **Positive:** Invalidation is exact. A change to one tenant's entitlements changes only that tenant's keys, and a catalog publish changes all keys together. There is no window where a view outlives the version it was built from.
- **Positive:** Change notifications reach only affected sessions (scenario 8). A tool published in a module the limited tenant does not license does not disturb that tenant's sessions.
- **Positive:** Revocation latency has a stated bound: event delivery when change events arrive, and the entitlement TTL when they are missed (scenario 9). Registry outages fail closed or degrade within a bounded window per Domain Pack (scenario 10).
- **Negative:** Role normalization has to be correct. If `relevant_roles` omitted a role that a tool references, two classes that should differ would share a view. Deriving `relevant_roles` from the published catalog, and changing the catalog version whenever it changes, keeps this mechanical.
- **Negative:** The tenant must always be in the key. Dropping it is an easy refactoring mistake with a cross-tenant leak as its result. Scenario 5 includes the insecure key as a baseline that leaks, and the `view-key-without-tenant` mutant is caught by scenarios 1, 5, 6 and 8.
- **Negative:** Freshness depends on the entitlement TTL and on change events. If events are missed, a revoked tool can stay visible, and callable, until the snapshot TTL ends. Choose the TTL with the risk owner, not from hit rates.
- **Negative:** Replicas that share a view cache must use the same key format and publish catalog versions in the same order, or one replica's view could be stored under a key another replica means for a different catalog.

## Alternatives

| Alternative | Why rejected |
|---|---|
| **Key views by user or session** | Correct but almost no reuse, memory grows with the user base, and keys carry personal identifiers into cache tooling. |
| **Key views by principal class without the tenant** | Two tenants at the same version and class share a view, so the limited tenant is served the full tenant's tools. Scenario 5 demonstrates the leak. |
| **Key views by tenant only** | Ignores roles, so a role-gated write tool would appear in the list for callers who cannot run it, or would be hidden from callers who can. |
| **TTL on views as the only invalidation** | A revoked tool stays listed until the TTL ends even when a change event arrived, and a catalog publish shows up at different times on different replicas. Versioned keys make invalidation exact. |
| **Cache the full catalog and filter in the client or agent** | Moves the decision to the side that cannot be trusted, sends hidden tools over the wire, and is the prompt-filtering anti-pattern in another form. |
| **Paginate the full catalog, then filter each page** | Gives uneven page sizes and lets page boundaries reveal how many tools are hidden. Filter first, cache the filtered view, then paginate it. |

## Follow-ups

- Emit view cache `hits`, `misses` and entry count as metrics, and alert when the entry count approaches the bound. A sudden rise in distinct keys can mean a role explosion or a key that picked up a per-user field.
- Add a startup or periodic assertion that every view key contains a tenant, as described in [Evaluation and troubleshooting](../evaluation-and-troubleshooting.md).
- When views are shared across replicas, share the cursor signing key too and rotate it with a short overlap.
- Record the agreed entitlement TTL, negative TTL, maximum staleness and stale-call scope for each domain in [Authentication and governance §8](../authentication-and-governance.md#8-policy-per-domain).
