# Proof-Preserving Redundancy Collapse Study

**Status:** non-normative research and design study; adversarial soundness review completed;
not an adopted implementation plan.

**Authority boundary:** this document does not amend, replace, or reorder the finite-history
MDP proof design, its obligations, its trust boundary, its pinned solver, or the current
thermostat implementation sequence. It authorizes no implementation by itself. If any proposal
here conflicts with the governing proof design, the governing design wins.
It does not revive or authorize the frozen legacy `lazy_*` verification path.

**Critical review result:** the optimizer's own root list, dependency graph, rewrite ledger, or
cache cannot establish that the optimizer omitted nothing. The checker must independently
regenerate the unreduced counterexample obligation and validate the optimized result against
that reference. Otherwise a shared omission bug can make an incomplete query look valid and
produce a false `UNSAT` certificate.

"Unreduced" means semantically complete, not tree-expanded. The authoritative reference may
itself be a compact typed DAG and may be streamed or hashed by the checker; it simply must exist
before any behavior-removing reduction and preserve every governing obligation.

**Soundness corrections made by this review:**

- formula comparison now defines common variables, auxiliary projection, and constructive
  witnesses instead of assuming both formulas have the same model signature;
- term garbage collection is separated from behavior-removing COI/constraint slicing;
- root completeness is independently regenerated rather than self-attested by the producer;
- interning is restricted to pure terms, with occurrence identity for fresh/effectful nodes;
- local contextual proofs may use only facts retained after the rewrite;
- all candidate reductions are transactional over an immutable baseline; and
- native code, hashes, unsat cores, tests, and partial proof logs are explicitly denied the
  authority to establish soundness by themselves.

## 1. Result in one paragraph

There is no sound general mechanism that can merely "notice" that a proof condition looks
redundant and discard it. The safe construction has layers. First, prevent identical **pure,
typed, semantically complete** expressions from being constructed twice by interning them in a
hash-consed term DAG. Second, starting from an independently regenerated complete formula,
emit its reachable term definitions once. Third, apply only a small exact rewrite system.
Finally, treat any assertion removal, semantic slicing, contextual simplification, or obligation
subsumption as a separate proof obligation whose evidence is checked independently. A timeout,
failed check, hash collision, missing certificate, uncertain side condition, or unavailable
pristine fallback must prevent acceptance of the optimized artifact. This gives the desired
implicit anti-redundancy behavior for structural duplication without permitting an optimizer to
manufacture a false `UNSAT` by deleting behavior.

## 2. Non-negotiable soundness contract

Let `C(u, a)` be the unreduced counterexample formula, where `u` are the common theorem-visible
variables and `a` are its auxiliary variables. Let `C'(u, b)` be the transformed formula, with
possibly different auxiliary variables `b`. A transformation may support an `UNSAT` certificate
only when the independent checker establishes at least:

```text
for every u, a: C(u, a) => there exists b: C'(u, b)
```

This is the precise counterexample-preservation direction. It implies
`SAT(C) => SAT(C')`, and therefore `UNSAT(C') => UNSAT(C)`. If both formulas use exactly the
same signature and variable interpretation, it may be abbreviated as
`models(C) subseteq models(C')`. Without a common signature, that set-inclusion notation is
ill-defined and must not be used.

The quantified relation must not be "checked" by simply declaring `a` and `b` as free SMT
constants and asking whether `C(u,a) and not C'(u,b)` is satisfiable. That query searches for one
bad choice of `b`; it does not establish that no extension `b` exists. Prefer a constructive
witness map `b := f(u,a)` and check `C(u,a) => C'(u,f(u,a))`. Identity weakening,
definitional extensions, and local rewrite certificates normally provide such a map. If no
witness map exists, the validator must use a proof method that correctly handles the required
quantifier alternation; it may not approximate this validation obligation in an unsound
direction.

An exact transformation additionally establishes the reverse extension:

```text
for every u, b: C'(u, b) => there exists a: C(u, a)
```

Definitional extensions, `let` conversion, fresh auxiliaries, and eliminated auxiliaries must be
judged by these extension/projection relations, not by textual equality. An under-approximation
is never sufficient for certification.

A `SAT` model of `C'` must still replay against `C` whenever only the first direction was proved.
Even if `C <=> C'` is established, the governing proof design may still require source
reachability replay because `C` itself can be an over-approximation of executable behavior.

For this study, "lazy" therefore means **demand-driven construction of a checked exact or
over-approximating formula DAG**. It never means heuristic branch skipping.

| Change | Required fact | Permitted certificate use |
| --- | --- | --- |
| Share two occurrences of one typed node | Both are pure and their complete semantic keys and structures are identical | Exact |
| Omit an unused definition | It is unreachable from **every independently regenerated formula root** and has no effects | Exact |
| Drop an assertion or transition constraint | Checked projected equivalence or counterexample-preserving implication | Exact or `UNSAT`-only |
| Replace a term by a simpler term | Equality under the complete theory, background, context, and evaluation/definedness semantics | Exact |
| Remove a branch under path condition `P` | The full background proves `P => guard` or `P => not guard`, and branch removal preserves evaluation effects | Exact |
| Weaken a constraint | Every exact counterexample has an extension satisfying the weakened formula | `UNSAT` only; replay `SAT` |
| Omit one obligation because another covers it | The full omitted counterexample formula implies the full retained formula under an explicit symbol map | Exact discharge of that obligation |
| Drop an obligation because it was absent from an unsat core | No adequate fact | Forbidden |

## 3. What redundancy means here

The verifier can encounter several different phenomena that must not be conflated:

- **Repeated representation:** the same operator, sort, and operands are rebuilt at many call
  sites.
- **Repeated state transport:** an unchanged state component is copied through many events.
- **Repeated guards:** several events ask the same predicate over the same SSA versions.
- **Repeated join values:** multiple control-flow arms produce the same value.
- **Dead construction:** a term is built but no mandatory proof root depends on it.
- **Logical equivalence:** differently shaped formulas happen to mean the same thing.
- **Contextual redundancy:** a path condition already determines a guard or value.
- **Obligation redundancy:** proving one complete counterexample query also rules out another.
- **Post-solve premise redundancy:** an `UNSAT` result depends on only a subset of named
  assumptions.

Only literal representation sharing and deletion of genuinely unused pure definitions are
automatically structural. Repeated state transport, guards, joins, or apparently dead assertions
can conceal control, definedness, exceptional-flow, scheduler, or freshness semantics and must
be treated as semantic until their exact conditions are established. Unsat-core reduction is
evidence minimization after a solve, not permission to omit proof roots before solving.

## 4. Recommended layered design

This is a research recommendation for later evaluation, not a change to the current project
plan.

### 4.1 Typed, content-addressed expression DAG

All term creation should pass through one node factory. Interning is permitted only for pure
logical terms. A node that represents a fresh symbol, nondeterministic choice, sample, event,
time read, allocation, or other occurrence-sensitive operation must carry a unique occurrence
identity unless the formal semantics proves it is a deterministic function of the keyed
operands.

The key schema must be closed, constructor-specific, and versioned. It cannot be a best-effort
list with defaults for unknown fields. Depending on the constructor, it includes:

- the exact operator or literal representation;
- result sort and all relevant type parameters;
- ordered child node identifiers;
- exact bit patterns for floating-point literals;
- rounding mode and any operation-specific semantic mode;
- SSA version and namespace for free variables;
- bit width, signedness, datatype constructor, binder identity, and lexical scope where relevant;
- the formal-semantics/profile version; and
- every definedness, exceptional-result, error-state, evaluation-order, freshness, or effect
  distinction represented by the node.

If the key and full structure equal an existing node, the factory returns the existing node ID.
Otherwise it creates one node. The hash is only an index: equality must be confirmed by full
structural comparison against the full canonical content, so a hash collision can affect
performance but never soundness. Pointer equality and a cryptographic digest are not themselves
proofs of structural equality. A persistent cache must retain or reconstruct the full content
that the checker compares.

The canonical encoding must itself be unambiguous: use tagged, length-delimited fields and an
explicit schema version rather than delimiter-free string concatenation. Maps and sets need a
specified canonical order. Parsing the encoding must reconstruct exactly one node or fail.

This is the mechanism that can implicitly refuse most safe redundancy. Duplicate pure
expressions never become duplicate graph nodes. It is common subexpression elimination at
construction time, not a later guess about meaning. The reducer must never infer semantic
identity from printed strings or source spelling.

SMT serialization should preserve that graph with named definitions or `let` bindings rather
than recursively expanding it into a tree. The SMT-LIB standard gives one `let` binder parallel,
capture-avoiding substitution semantics. Therefore sibling bindings cannot refer to one another;
dependencies require nested `let`s or topologically ordered definitions. The serializer must
also prevent symbol capture, duplicate binder names, cyclic definitions, and sort changes. A
parser/round-trip checker should validate the emitted structure instead of trusting string
concatenation.

### 4.2 Demand-driven construction from independently regenerated roots

The producer may create metadata or lazy thunks for all possible terms, but should materialize
and serialize only nodes reachable from a complete formula-root inventory. The inventory cannot
be accepted merely because the producer labels it complete. The independent checker must
regenerate the unreduced obligation from the source-bound IR and governing proof settings, then
derive the root list itself.

Every unreduced asserted formula is initially a root. Traversing the term DAG from that complete
set can exactly remove unused declarations and definitions. Removing an asserted formula,
transition, frame, or branch is not term garbage collection; it is a semantic reduction that
requires the relation in Section 2.

For the current proof architecture, the independently regenerated inventory must include every
active:

- domain and well-formedness assumption;
- transition and scheduler constraint;
- progress or termination condition;
- correspondence and buffer condition;
- exceptional, blocked, and definedness condition;
- frame condition that remains semantically necessary; and
- complete visible-difference root for the current obligation, including all governed
  observation, reward, termination/truncation, duration/discount, and error outcomes.

Reachability is an exact graph operation only over a complete formula and dependency graph.
Data, control, symbol-sharing, definedness, exceptional-flow, scheduler, progress, and frame
dependencies must all be represented. The checker should recompute the closure from its own
canonical root list and reject a serialized formula that omits a reachable node or an expected
root. Recomputing closure over the producer's already-truncated graph proves nothing about what
was omitted.

This term-level step resembles cone-of-influence reduction, but it must not inherit the name's
apparent guarantee. Model-checking systems use COI to restrict analysis to variables relevant to
a property, and their documentation warns that a trace from a reduced model need not validate
in the original. Any later constraint-level COI reduction therefore requires a separately
checked equivalence or counterexample-preservation argument and exact-semantics replay of a
`SAT` witness.

### 4.3 A deliberately small exact rewrite kernel

The node factory may apply locally checkable identities before interning **only after source
evaluation has been lowered into pure, total logical terms plus explicit definedness,
exceptional-flow, and effect obligations**. Applying an SMT identity directly to a source AST
can erase evaluation that raises an error or performs a visible action. Candidate identities
include:

- Boolean identities such as `and(x, true) = x` and `ite(c, x, x) = x` in the pure SMT layer;
- idempotence where it is valid in the exact theory;
- elimination of a join whose incoming node IDs are all identical;
- flattening, sorting, or deduplication only for operators whose applicable semantics actually
  establish associativity, commutativity, or idempotence;
- symmetric ordering of equality operands when sorts and equality semantics match.

Each rewrite belongs to an explicit whitelist with a direction, applicable sorts, semantic
stage, and checker rule. `ite(c, x, x) = x`, for example, is exact as a pure SMT term but does
not authorize deletion of source evaluation of `c` or its error behavior. No unproved solver
"simplify" call becomes part of the trusted rewrite kernel.

Floating-point associativity, distributivity, cancellation, real-number substitution, and
equality substitution are forbidden without all required IEEE-754 side conditions and a checked
proof. NaNs, signed zero, infinities, rounding, and exceptional behavior make visually plausible
algebra unsound. The key and rule set must distinguish SMT-LIB `=` from IEEE `fp.eq`: `fp.eq`
treats `+0` and `-0` as equal and is false for NaN arguments, while SMT-LIB `=` is a different
logical operation.

### 4.4 Preserve and extend sparse SSA

The existing sparse event SSA follows the intended structural pattern: create versions only for
actual writes, retain identity for untouched state, and validate the full event frame. Its
current checks and size reduction are engineering evidence, not by themselves the final
soundness certificate. The DAG layer should sit beneath that representation so repeated guards
and right-hand sides over the same input versions are shared automatically.

The word "same" is strict. A predicate over `temperature@7` is not interchangeable with the
same source text over `temperature@8`. Likewise, terms from independently quantified history
or implementation copies cannot be merged merely because their printed names or syntax match.
Only genuinely common immutable inputs may be shared across such namespaces. A guard is not the
same merely because its source text matches; its execution copy, SSA environment, rounding and
exception context, and occurrence-sensitive inputs must also match.

Sparse joins are particularly dangerous. For every active join, incoming selectors must be
complete and appropriately exclusive, each value must come from the correct normal, blocked, or
exceptional predecessor state, and no unconstrained phi value may become visible. Removing an
identity frame is safe only after the complete frame/update partition has been regenerated and
validated independently.

### 4.5 Checked cone-of-influence and constraint slicing

Three operations must be distinguished:

1. Removing a pure definition unreachable from every complete formula root is exact term-DAG
   garbage collection.
2. Dropping a constraint generally weakens the formula. It can be sound for `UNSAT` only after
   proving that every model of the unreduced formula extends to a model of the result.
3. Dropping a branch, transition, action, or state is generally an under-approximation and is
   forbidden unless an exact unreachability or subsumption proof covers it.

A syntactic variable-use graph is not enough to classify the second or third operation. Two
assertions can interact through a shared symbol, datatype invariant, function interpretation,
array, scheduler fact, or exceptional-flow condition without one occurring as a subterm of the
other. Constraint-level slicing therefore needs a dependency relation derived from the full
unreduced semantics, plus a checked relation from Section 2.

After independently regenerating the roots, the reducer may compute the backward transitive
closure through every dependency kind. It should emit a ledger containing:

- the canonical root IDs;
- all retained node IDs;
- all removed node IDs;
- the edge kinds used in the closure; and
- counts before and after slicing.

The independent checker reconstructs the unreduced graph, roots, and closure instead of trusting
the reducer's claim. It must also validate the formula relation; closure agreement alone does
not prove constraint removal sound. If dependency metadata is absent, malformed, cyclic where
cycles are prohibited, or inconsistent with the unreduced term graph, the optimized artifact is
rejected. Processing may continue only from a separately preserved, validated, immutable
unreduced artifact.

### 4.6 Context-sensitive simplification with local proofs

Some redundancy is visible only under a path condition `P`. For example, an `ite(g, a, b)` may
reduce to `a` if the retained background `B` proves `B and P => g`. `B` may contain only facts
that remain valid after the rewrite; it cannot use the candidate equation or a consequence that
will disappear with the removed branch. Otherwise the proof can be circular. The safe procedure
is:

1. Construct the validation query `B and P and not g` from the immutable unreduced artifact.
2. Ensure the rewrite occurs only in the lexical/control scope dominated by `P` and preserves
   all evaluation, definedness, and exceptional-flow facts.
3. Accept the rewrite only with independently checkable evidence or an exact replayable local
   implication query under the governing trust policy.
4. Make the reduction transactional: do not mutate the accepted graph until validation succeeds.
5. On `SAT`, `UNKNOWN`, timeout, missing evidence, or a cache mismatch, reject the proposed
   rewrite and use the pristine graph.

Z3's own programming guide presents context-aware simplification by proving local term
equalities and also emphasizes memoizing visited subterms. That is a useful engineering
pattern, but the project's certificate policy must determine what local evidence is trusted.

The context key must be exact. It must commit to the complete background formula, declarations,
sorts and function signatures, semantic-profile version, rewrite-rule version, source/input
digests, checker and solver policy, and the scoped `P`, `g`, and replacement terms. Reusing a
fact proved under a stronger, stale, renamed-without-a-map, or merely similar context is unsound.
A digest lookup is only an index; cached content and evidence still require validation.

### 4.7 Deduplicate and subsume obligations only with the right implication

Syntactically identical canonical **complete** counterexample formulas can share one solver
query after capture-free alpha-normalization. Identical difference predicates are not enough if
their background assumptions differ. Logical subsumption is also possible, but the direction
matters. If the complete `C1` is solved and the complete `C2` is to be omitted, the checker must
establish, under an explicit shared-variable and auxiliary-extension map:

```text
for every u, a2: C2(u, a2) => there exists a1: C1(u, a1)
```

Then a validated `UNSAT(C1)` implies `UNSAT(C2)`. The opposite implication does not justify the
omission. The validator, not the reducer, must construct the implication query from both
unreduced obligations; accidental symbol aliasing or omission of either background can reverse
the result. The constructive-witness or correctly quantified validation rule from Section 2
applies here as well; treating both auxiliary sets as arbitrary free constants proves a
different statement.

For a partitioned check, the governing complete formula must satisfy an independently checked
identity over one common signature such as:

```text
C_total <=> (C_1 or C_2 or ... or C_n)
```

No visible-difference category may disappear merely because it appears unimportant or because
some other query was unsatisfiable.

### 4.8 Use unsat cores only after the proof is established

Named assumptions and unsat cores can reduce the evidence replayed or retained after an
`UNSAT` result. They do not prove that absent mandatory obligations, roots, source behaviors, or
translation rules were unnecessary. A core is a subset of the assumptions actually presented
to that solver invocation; it says nothing about assertions that a buggy producer failed to
emit, and it need not be minimal.

A safe optional workflow is:

1. solve the complete query;
2. request a core;
3. map every returned name back to the exact canonical assertion in the immutable full query;
4. construct the core-only query without rewriting those assertions;
5. solve and independently check it again; and
6. retain the original root inventory and the mapping from omitted assumptions to the checked
   core reduction.

This can shrink evidence, but it is not the main mechanism for avoiding initial construction.
If the core omits the visible-difference predicate, the background may itself be inconsistent.
That is a vacuity warning, not permission to bypass the governing progress, reachability,
correspondence, or admissibility obligations.

### 4.9 Reuse immutable artifacts, not unrecorded solver state

The common base term DAG can be content-addressed once and combined with a small,
obligation-specific root. This permits caching of serialization, parsed solver input, and
checked local reductions without altering semantics only when the cache key commits to the
complete canonical content and policy environment. A path name, commit label, short digest, or
node ID from another arena is insufficient.

Incremental solver scopes can improve development throughput, but they are a performance
mechanism, not a proof rule. They must not silently replace the current production requirement
for isolated, canonical, individually hashed obligation runs. Any future relaxation would need
an explicit trust and reproducibility analysis outside this study.

### 4.10 Proof-producing equality saturation is optional, not the baseline

E-graphs can represent many equivalent terms compactly, and proof-producing congruence closure
can explain a chosen equivalence. They become sound here only if every rewrite rule, sort,
side condition, congruence step, binder substitution, and extracted equality is independently
checked against the project's formal semantics. An e-graph whose rewrite library treats
floating-point operations like real arithmetic, or source operations like pure SMT terms, would
be dangerous.

Likewise, reduced ordered binary decision diagrams can canonically collapse Boolean functions
for a fixed variable order, but their size can still be exponential. Treating theory atoms as
independent Boolean variables is an abstraction; it is exact only for Boolean restructuring that
maps the same atoms back unchanged. A BDD must not silently delete theory relationships. It may
be useful for a bounded Boolean control skeleton with a hard size cap and pristine fallback; it
should not be the default representation for the mixed-theory transition relation.

## 5. Safety classification

| Class | Examples | Status |
| --- | --- | --- |
| Structural exactness | Typed hash-consing of pure terms, capture-safe DAG-preserving `let`, duplicate conjunct removal, unused-definition collection from all regenerated roots | Preferred first layer |
| Locally checked exactness | Small pure-SMT rewrite whitelist, identical SSA join values with complete selector checks | Preferred after checker exists |
| Conjunct weakening | Deleting a complete asserted conjunct while retaining the remaining formula | Counterexample over-approximation if structurally checked; `UNSAT`-only |
| Behavior or COI slicing | Removing a transition alternative, frame case, variable semantics, state, or branch | Never structural by default; require Section 2 evidence |
| Translation-validated semantics | Contextual branch pruning, global value numbering across joins, logical obligation subsumption | Allowed only with checked evidence |
| Counterexample over-approximation | Dropping selected constraints; replacing a deterministic operation by a correctly sorted, sufficiently distinguished uninterpreted function | Sound only for `UNSAT`; replay `SAT` |
| Heuristic or under-approximating pruning | Dropping branches because they seem infeasible, sampled-action pruning, incomplete difference sets | Forbidden for certification |
| Unchecked theory rewriting | Real-algebra rewrites over IEEE floating point, unproved datatype or exceptional-flow simplification | Forbidden |
| Identity errors | Merging fresh/effectful occurrences, cross-namespace aliasing, over-sharing distinct uninterpreted operations | Forbidden |
| Evidence shortcuts | Treating timeout/`UNKNOWN` as success, using core absence to erase obligations, trusting hashes as equality, self-validating a producer's root list | Forbidden |

For uninterpreted abstraction, sharing too little usually adds behavior and may only reduce proof
power; sharing too broadly can impose false equalities and remove behavior. Each symbol must be
keyed by the exact concrete operation identity, sorts, arity, rounding and exceptional semantics,
and formal context required by the governing design.

## 6. Reduction evidence and independent checking

The producer may be aggressive only because its output is checked against an independently
regenerated reference. This is the translation-validation pattern: validate each optimized
translation against a common semantics instead of trusting the optimizer implementation itself.
Translation validation is not automatic independence; if the producer and validator share the
same buggy lowering, root enumeration, or rewrite code, the shared bug remains in the TCB.
The checker may share a deliberately small governed semantic kernel, but that kernel then remains
explicitly inside the TCB. "Independent" here means recomputing from authoritative inputs without
trusting producer decisions or artifacts, not merely running the same implementation twice.

A reduction artifact should contain:

1. **Unreduced-reference identity:** source, proof-profile, semantic-kernel, and canonical
   unreduced-form digests.
2. **Canonical node table:** node ID, exact sort, operator/literal, ordered child IDs, scope,
   purity/freshness classification, and all semantic-mode fields.
3. **Producer root inventory:** every root claimed by the producer, its obligation, and its role.
4. **Reduction records:** rule ID, input node IDs, output node ID, soundness direction, side
   conditions, and evidence reference.
5. **Dependency ledger:** all dependency-edge kinds and the claimed reachability result.
6. **Symbol/projection map:** correspondence between theorem-visible and auxiliary variables in
   the unreduced and reduced formulas.
7. **Obligation map:** exact formula or checked implication used for every deduplicated or
   subsumed obligation.
8. **Serialization digest and completion marker:** canonical SMT-LIB bytes, hashes of referenced
   inputs, and an atomic indication that the whole artifact was finalized.
9. **Metrics:** tree occurrence count, unique DAG nodes, roots, edges, fan-out, maximum `ite`
   depth, declarations, assertions, SMT-LIB bytes, and solver resource data.

The checker must:

- independently regenerate the complete unreduced formula and authoritative root inventory;
- reparse canonical nodes and reject ill-sorted, ill-scoped, cyclic, effect-confused, or malformed
  terms;
- confirm structural equality after every hash lookup;
- independently replay each rewrite or verify its proof;
- recompute dependency closure from its regenerated roots;
- validate the symbol/projection map and counterexample-preservation direction;
- compare the producer root inventory with its own instead of trusting the producer's list;
- confirm that proof evidence covers every preprocessing and rewrite step actually used;
- reject missing, unknown, timed-out, or version-incompatible evidence; and
- reproduce the final canonical formula digest and reject incomplete/partial artifacts.

The optimized generator can initially remain an untrusted convenience program. To support the
project's direction of removing Python from the trusted compute boundary, Python may propose
nodes and reductions, but the accepting checker and semantic regeneration path must not depend
on those Python results being correct. A native checker is still part of the TCB unless it is
formally verified or its result is independently proof-checked; changing implementation language
does not create mathematical assurance by itself. No Python hash table, native hash table,
pointer comparison, traversal order, or cache hit should be a soundness assumption.

## 7. The implicit mechanism, concretely

The desired "do not allow redundancy to form" behavior is best expressed by the following
abstract constructor:

```text
intern(pure_term):
    require complete_key_schema_recognizes(pure_term.constructor)
    require purity_and_freshness_classification(pure_term) == PURE
    sort, operator, ordered_children, semantic_parameters := decompose(pure_term)
    key := canonical_encoding(sort, operator, ordered_children,
                              semantic_parameters)
    for candidate in hash_index[hash(key)]:
        if structurally_equal(candidate.full_content, pure_term, key):
            return candidate.node_id
    node_id := append_canonical_node(key)
    hash_index[hash(key)].append(node_id)
    return node_id
```

Occurrence-sensitive terms go through a separate constructor whose key includes their unique
formal occurrence identity. They must never reach `intern(pure_term)` by default.

Later, serialization begins only from mandatory roots:

```text
emit(source, proof_profile, proposed_reductions):
    unreduced := independently_regenerate_unreduced_formula(source, proof_profile)
    roots := every_asserted_formula_root(unreduced)
    reachable := transitive_term_dependency_closure(roots)
    order := deterministic_topological_order(reachable)
    write_each_node_once(order)
    write_roots(roots)
    validate_each_semantic_reduction(unreduced, proposed_reductions)
```

The first operation eliminates structurally identical pure terms. The root traversal eliminates
unused definitions from the complete unreduced formula. Neither operation may remove assertions,
behaviors, or occurrence-sensitive choices. Logical redundancy is deliberately left to the
checked layers because recognizing arbitrary logical equivalence is itself a theorem-proving
problem.

## 8. Soundness hazards and forbidden implementation patterns

These are not merely performance bugs. They can remove a real counterexample and allow a false
`UNSAT`, accept incomplete evidence, or corrupt the supposedly pristine fallback.

| Hazard | Why it is dangerous | Required prevention |
| --- | --- | --- |
| Incomplete node key | Distinct operations or states merge, adding false equalities and deleting behavior | Closed versioned key schema; reject unknown constructors/fields; full structural check |
| Interning fresh or effectful occurrences | Two distinct samples, events, reads, or choices become one value | Purity/effect type; unique occurrence identity; no default interning |
| Cross-copy or cross-SSA sharing | The two executions or two program points are forced equal | Namespace and SSA identity in every free symbol; explicit map only for truly common inputs |
| Producer supplies its own authoritative roots | The same omission bug affects formula and "completeness proof" | Checker regenerates unreduced roots independently from source-bound semantics |
| Treating COI closure as proof of assertion removal | A missing semantic edge makes a constraining assertion look irrelevant | Closure only collects terms; constraint removal needs Section 2 evidence |
| Wrong implication direction | An under-approximation may be proved `UNSAT` while the original has a counterexample | Check exact counterexample-preservation direction and mutation-test its reversal |
| Ignoring different auxiliary signatures | Textual `C => C'` can compare unrelated or accidentally aliased variables | Explicit theorem-variable projection and auxiliary extension map |
| Naive free-constant validation | `C(u,a) and not C'(u,b)` disproves one `b`, not the required existence of some `b` | Constructive witness map or a checker that proves the actual quantified relation |
| Source-level algebraic simplification | `ite(c,x,x)` or Boolean folding can erase evaluation errors, ordering, or effects | Rewrite only pure lowered terms; retain explicit evaluation/definedness obligations |
| Floating-point/operator confusion | Real algebra, `=`, and `fp.eq` disagree on NaNs, signed zero, and rounding | Exact operator identity; IEEE-aware rules; proved side conditions only |
| Text substitution or unhygienic `let` | Partial-name replacement, capture, parallel-`let` mistakes, or sort changes alter meaning | Parsed typed AST; capture-free renaming; nested/topological bindings; round-trip check |
| Over-sharing uninterpreted functions | Distinct concrete operations are forced to have equal outputs | Separate symbols for distinct semantic identities; independently check abstraction map |
| Reusing contextual/cache facts | A fact proved under stronger or different assumptions prunes a feasible path | Commit to complete environment and evidence; revalidate cached content |
| Self-justifying context | A rewrite is "proved" using the very assertion or branch it removes | Build context only from retained dominating facts, or validate the whole-formula relation |
| In-place optimization followed by "fallback" | Failed validation may leave the baseline partially mutated | Immutable baseline and transactional candidate artifact |
| Partial artifact accepted after timeout/crash | The consumer may solve bytes that are not the complete intended obligation or may combine stale and new state | Atomic completion marker; count/hash checks; reject all partial output |
| Unsat-core-driven source omission | A core only describes what was emitted, not what should have been emitted | Cores are post-solve evidence only; retain independent root/obligation inventory |
| Circular validation | Reducer and checker repeat the same buggy rule or traversal | Smaller independent checker or proof kernel; independent query construction |
| Incomplete proof-log coverage | A checked fragment is mistaken for proof of unlogged preprocessing | Verify end-to-end proof conclusion and all preprocessing assumptions; otherwise retain solver in TCB or reject |
| Native-language complacency | Rewriting Python in Rust/C changes failure modes but does not prove correctness | Explicit TCB statement, small checker, formal specification, and proof checking where feasible |

The most dangerous class is a **false merge**: missing a duplicate only costs performance, but
merging two nonidentical nodes can constrain the formula and produce a false `UNSAT`. The
interner must therefore be one-sided in its use of shortcuts: it may fail to share equal nodes,
but it may never report unequal nodes equal without a full checked comparison.

## 9. Fail-closed policy

Every optional optimization needs a separately preserved, immutable, complete unreduced
artifact. The only acceptable outcomes are:

1. the candidate reduction and all evidence validate, so the reduced artifact may be used; or
2. the candidate is discarded and the pristine unreduced artifact is independently validated
   and used.

If neither artifact is complete and validated, the outcome is **no certificate**. The candidate
must be rejected on any of the following:

- a reduction check is `SAT`, `UNKNOWN`, interrupted, or timed out;
- a side condition cannot be represented exactly;
- evidence required by the governing proof policy is unavailable for an active theory,
  preprocessing step, or reduction;
- a cache key, tool version, semantic mode, or input digest differs;
- dependency metadata is incomplete;
- an expression crosses an unsafe namespace or SSA-version boundary;
- a size cap for BDD, e-graph, or contextual reasoning is reached;
- an atomic completion marker, count, or digest is missing; or
- the independent checker disagrees with the producer.

An ordinary optimization miss is only a performance loss **when the pristine artifact remains
available and valid**. A checker disagreement, malformed ledger, incomplete output, or mutation
of the baseline is an integrity failure: quarantine the optimized artifact and do not continue
from its in-memory state. No optimization success may become a certificate until its evidence
has passed the checker.

## 10. Evaluation order, without changing the project plan

If this study is later approved for implementation, the safest order to evaluate is:

1. define the authoritative unreduced formula, common signature/projection relation, and
   independently regenerated root inventory;
2. measure unique pure subterms versus repeated tree occurrences in the current compact
   transition formula;
3. specify and test the complete purity/effect classification and canonical node-key schema;
4. add typed DAG interning and graph-preserving SMT-LIB serialization without assertion removal;
5. add immutable baseline preservation and exact unused-definition collection from all roots;
6. add the small pure-SMT rewrite kernel and checker;
7. add checked COI metadata and semantic reduction evidence, with mutation tests for every
   dependency-edge kind;
8. measure again before considering contextual implication queries;
9. consider obligation subsumption only where measured duplication remains material; and
10. consider proof-producing e-graphs or bounded BDDs only if the simpler layers leave a
   demonstrated bottleneck.

This order is intentionally conservative: it captures the largest low-risk gains before adding
new theorem-proving work or expanding the checker.

## 11. Validation and adversarial tests

At minimum, later prototypes should include mutations that:

- force hash collisions and confirm that distinct structures never merge;
- force the interner to miss equal nodes and confirm that the only effect is lost performance;
- attempt to merge two fresh/nondeterministic occurrences and confirm rejection;
- change one child sort, SSA version, rounding mode, namespace, or literal bit and confirm a
  distinct node results;
- change `=` to `fp.eq`, `+0` to `-0`, or one NaN/rounding classification and confirm a distinct
  node or rule path results;
- delete each dependency-edge class in turn and confirm closure checking fails;
- drop an exceptional, blocked, progress, scheduler, or frame root and confirm root checking
  fails;
- corrupt the producer root list and confirm the independently regenerated inventory detects it;
- make a sibling SMT-LIB `let` binding reference another sibling and confirm serialization is
  rejected or correctly nested;
- inject variable capture, sort confusion, duplicate binders, and textual-name prefix collisions;
- reverse the implication used for obligation subsumption and confirm rejection;
- alter the auxiliary-variable projection or alias two unrelated obligations and confirm
  rejection;
- reuse a contextual proof under a weaker context and confirm rejection;
- reuse a cache entry after changing one declaration, semantic-profile version, or background
  assertion and confirm rejection;
- inject NaN, signed-zero, infinity, and rounding-sensitive floating-point cases;
- feed `UNKNOWN`, timeout, corrupt evidence, and tool-version mismatch outcomes and confirm
  either pristine fallback or no certificate;
- crash during artifact emission and confirm the partial candidate cannot be opened as complete;
- make candidate validation fail after an in-memory rewrite and confirm the immutable baseline
  remains byte-identical;
- replay every reduced `SAT` witness against the unreduced exact semantics; and
- compare reduced and unreduced results on generated small instances where exhaustive
  enumeration is possible.

Differential tests, exhaustive small cases, and mutation tests are strong bug detectors but are
not substitutes for the checked transformation relation or source-to-IR soundness proof.

Success metrics must include both formula size and proof risk: unique DAG nodes, serialized
bytes, construction time, peak memory, solver time, number and cost of local checks, evidence
size, checker time, fallback rate, and mutation-test detection rate.

## 12. Open decisions requiring separate approval

- Whether the pinned Z3 build can produce sufficiently complete, stable proof evidence for
  the exact mix of floating-point, datatype, and preprocessing operations in use.
- The exact theorem-visible variable projection and auxiliary-extension relation used to compare
  unreduced and reduced formulas.
- How the independent path regenerates the authoritative root inventory without sharing the
  producer's completeness bug.
- The formal purity/effect/freshness classification for every supported source and IR operation.
- Whether local implication results should be replayed by the same pinned solver in isolated
  processes or checked in a smaller independent kernel.
- The unambiguous canonical node encoding and whether the trusted checker should be a small
  memory-safe native implementation, a Lean artifact, or both.
- Whether common base artifacts may be cached across production obligations without weakening
  current isolation and reproducibility requirements.
- Whether the remaining formula profile after structural sharing justifies any e-graph or BDD
  complexity.

None of these decisions is needed to preserve the benefits already obtained from sparse SSA.

## 13. Primary references

- Barrett, Fontaine, and Tinelli, [The SMT-LIB Standard, Version 2.7](https://smt-lib.org/papers/smt-lib-reference-v2.7-r2025-02-05.pdf): formal semantics of `let`, definitions, scripts, and solver interaction.
- Tinelli and Brain, [SMT-LIB FloatingPoint theory](https://smt-lib.org/theories-FloatingPoint.shtml): exact sorts, rounding modes, comparisons, NaN behavior, signed zero, and the distinction between `fp.eq` and SMT-LIB `=`.
- Bjørner, de Moura, and colleagues, [Programming Z3](https://z3prover.github.io/papers/programmingz3.html): DAG-aware term traversal, incremental scopes, unsat cores, and context-aware simplification by local solver checks.
- Z3 source, [`ast.cpp`](https://github.com/Z3Prover/z3/blob/master/src/ast/ast.cpp): the solver's own AST manager uses hash-consing.
- Pnueli, Siegel, and Singerman, [Translation Validation](https://link.springer.com/content/pdf/10.1007/BFb0054170.pdf): validation of individual optimized translations through a common semantics and simulation relation.
- Monniaux and Six, [Simple, Light, Yet Formally Verified, Global Common Subexpression Elimination and Loop-Invariant Code Motion](https://arxiv.org/pdf/2105.01344): verified CSE, hashed representations, semantic preservation, and trust-boundary tradeoffs.
- nuXmv project, [nuXmv 2.2 User Manual](https://nuxmv.fbk.eu/downloads/nuxmv-user-manual.pdf): property-specific cone-of-influence reduction and the reduced-model counterexample caveat.
- Barbosa et al., [Flexible Proof Production in an Industrial-Strength SMT Solver](https://gereon-kremer.de/static/2022-ijcar-cvc5-proofs.pdf): eager and lazy proof production for preprocessing transformations.
- cvc5 project, [Proof production documentation](https://cvc5.github.io/docs/latest/proofs/proofs.html): proof calculus and proof output/checking formats.
- Willsey et al., [egg: Fast and Extensible Equality Saturation](https://arxiv.org/abs/2004.03082): e-graph equality saturation and extraction.
- Flatt et al., [Small Proofs from Congruence Closure](https://arxiv.org/abs/2209.03398): proof certificates for congruence closure and equality saturation.
- Bryant, [Graph-Based Algorithms for Boolean Function Manipulation](https://www.cs.cmu.edu/~emc/15414-s14/lecture/ieeetc86.pdf): canonical reduced ordered BDDs and their worst-case size limitation.

## 14. Bottom line

The sound answer is not to ask the verifier to guess what can be ignored. It is to make exact
duplicate **pure-term** construction unnecessary, collect only unused definitions from an
independently regenerated complete formula, and require a checked counterexample-preservation
theorem for every assertion or behavior omission. The producer may suggest reductions, but it
may not certify its own completeness. Computational savings are optional; preservation of every
real counterexample is the mathematical limit and cannot be traded away.
