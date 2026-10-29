# Help disclosure review

Treat root and leaf targets as two progressive-disclosure layers, not two renderings of the same catalog:

```text
marivo.help("authoring")             -> datasource and semantic discovery
marivo.help("analysis")              -> analysis discovery
marivo.help("<surface>.<target>") -> exact contract for one target
```

Root help is a discovery contract:

- Owning surface Help reveals the active Marivo version, Python executable, and package path, then orients the agent with imports, teaching order, registry-owned capability-family summaries, and exact next focused targets. Global workflow roots stay compact and route to the owning native surface roots.
- Do not expand complete signatures, constructor tables, public type/member algebra, or error catalogs at the root. Public types, errors, and receiver members remain queryable as focused leaves but stay out of the discovery index unless the target is an intentional non-invokable navigation topic.
- Every discoverable target must belong to exactly one root group. Require the registry to own root membership, including explicit maps for families that cannot be inferred from canonical-id prefixes; reject renderer-local shadow member lists.
- Every advertised target must be canonical, alias-free, and resolvable through the public coordinator. A navigation topic must expand only to real registered members, and every listed member must resolve independently.
- Root output must be deterministic and remain within the root line/codepoint budget. Compact summaries before dropping a real capability or leaking leaf detail into the root.

Leaf help is an exact-target contract:

- A callable leaf owns the public entrypoint, live reflected signature, output family or return type, required and optional inputs, defaults and omit rules, cross-parameter constraints, effects, and one correct minimal example. Reflection confirms the callable shape but does not replace registry-owned semantics.
- A public-type leaf owns its producers, consumers, public fields, and public consumption methods without exposing private state, inherited framework mechanics, class descriptors, or memory addresses. Type-object and canonical string targets must render the same static contract; a concrete instance may add only bounded deterministic runtime facts.
- An error-class leaf describes the stable error contract. An error-instance leaf preserves its concrete message, expected, received, location, structured repair, candidates, and next help target when present; never downgrade an instance to a class page merely because one optional repair field is absent.
- A grouping leaf lists the real registry members of that family rather than duplicating them in renderer code. Leaf pages remain self-contained for the selected target and do not restate the whole workflow or root capability map.
- Equivalent accepted forms—canonical string, public callable, public type, and exact registered instance where supported—must resolve to the same owner and canonical contract. Instance enrichment may add current facts but must not change the static API truth.

Accept root/leaf help changes with independent evidence:

1. Build an invariant from registry groups and public exports independently of the renderer: every discovery id appears in exactly one root group, every intended public callable is discoverable directly or through one navigation family, and type/error/member leaves remain excluded.
2. Parse every rendered root target and grouping member, call the public resolver for each one, and reject dead links, aliases, ambiguity, duplicate membership, or silent omissions. Treat inventory-count changes as review signals, not as the only coverage proof.
3. Exercise a leaf matrix covering canonical strings, callable objects, type objects, concrete values, error classes, and error instances. Assert static equivalence where required, bounded deterministic instance enrichment, structured failure repair, and absence of object addresses or private fields.
4. Check root and focused render budgets separately. Execute or minimally compile copyable examples and probe at least one adversarial error path; literal snapshot assertions alone do not prove the contract.
5. Keep latest English and Chinese guidance aligned. Run focused Help/registry tests; use the broad gate in `AGENTS.md` for shared public-surface changes and site verification/build when site content changes.
