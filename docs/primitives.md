# Primitive type taxonomy

Every programming task, at sufficient granularity, resolves into one of 22 types. This is the closed set. New types should be added only when a real task genuinely can't be expressed as a combination of existing ones — not when a task feels different from familiar ones.

The types are organized along two axes: **existence vs behavior**, and **internal vs boundary-crossing**.

---

## Structural — things that exist

These describe shape. They produce definitions, not behavior.

### `data_model`
A schema, class, struct, or type definition with validation rules. The canonical unit of data representation. Has fields, constraints, and invariants. Business logic does not belong here.

Examples: `User`, `Order`, `PasswordResetToken`, `ApiResponse[T]`

Leaf node. Steps: enumerate fields → define validation rules → write failing tests → implement → document invariants.

### `interface`
A contract between two things. Defines method signatures, error contracts, preconditions, postconditions. Does not implement. Any correct implementation must pass its contract tests.

Examples: `UserRepository`, `EmailSender`, `TokenGenerator`

Leaf node. Steps: define method signatures → define error contract → define pre/post conditions → write contract tests.

### `config`
Runtime parameters, environment variables, feature flags, constants. Typed. Never hardcoded inline. Validated at startup.

Examples: `DatabaseConfig`, `SmtpConfig`, `FeatureFlags`

Leaf node. Steps: enumerate parameters → define types and defaults → define validation → implement with validation.

---

## Computation — pure, no external state

These take input and produce output. They don't read from or write to anything outside themselves.

### `transformation`
Maps input to output. Deterministic. No side effects. No I/O. The most common leaf node. If it needs to read from a database, it's not a transformation — split it into a query and a transformation.

Examples: `format_currency`, `normalize_email`, `calculate_discount`, `parse_jwt_claims`

Leaf node. Gates enforce: no Any types, no I/O calls present in AST, tests pass, minimum 3 edge cases covered.

### `aggregation`
Reduces many to one. A specialization of transformation for collection inputs. Fold, reduce, summarize.

Examples: `sum_order_totals`, `group_events_by_day`, `compute_statistics`

Leaf node. Same gates as transformation plus: input must be a collection type.

### `validation`
Takes input, returns boolean and/or error list. No side effects. Returns all errors, not just the first. Validation logic lives here, not in data models or mutations.

Examples: `validate_password_strength`, `validate_shipping_address`, `validate_order_request`

Leaf node. Gates enforce: returns all errors (LLM judge), no I/O present in AST, tests pass.

---

## IO — crosses a boundary

These interact with something outside the current process. The most dangerous category for mixing concerns.

### `query`
Reads from external state. Returns data. No side effects. The "not found" case is always explicitly handled — it's not optional.

Examples: `find_user_by_email`, `get_order_by_id`, `list_recent_events`

Leaf node. Gates enforce: no Any types, no mutations present in AST (read-only enforced), "not found" case handled and tested, tests pass.

### `mutation`
Writes to external state. Has side effects. Every failure mode is explicitly handled — not swallowed into a generic exception. No business logic. Pure I/O layer.

Examples: `save_user`, `update_order_status`, `delete_session_token`

Leaf node. Gates enforce: no Any types, exception handling present in AST, no business logic (LLM judge), tests cover at least one failure path.

### `event_emit`
Fires a signal that other parts of the system may react to. Fire-and-forget or at-least-once delivery. The emitter doesn't care who receives it or when.

Examples: `emit_order_placed`, `emit_password_changed`, `emit_user_registered`

Leaf node.

### `event_handler`
Reacts to an incoming event. Idempotency is usually required — events may be delivered more than once. The handler shouldn't assume exactly-once delivery.

Examples: `handle_payment_succeeded`, `handle_user_email_verified`

Leaf node. Gates enforce: idempotency handling documented or explicitly waived.

---

## Coordination — sequences or dispatches

These nodes contain no business logic themselves. They only orchestrate other nodes.

### `pipeline`
An ordered chain of operations where the output of one feeds the input of the next. Static, linear. Each stage is a typed node.

Examples: `user_registration_pipeline`, `order_fulfillment_pipeline`

Composition node. Decomposes into child nodes before executing. Gates enforce: no business logic in the pipeline itself, all children are typed.

### `router`
Conditional dispatch. Examines input and routes to different handlers. The router itself makes no decisions about business outcomes — it only routes.

Examples: `payment_method_router`, `notification_channel_router`

Composition node.

### `orchestration`
Stateful multi-step coordination. Unlike a pipeline, an orchestration has rollback behavior — if step N fails, it knows what to unwind. Sagas, workflows, multi-step processes with compensating transactions.

Examples: `password_reset_flow`, `checkout_process`, `user_onboarding_flow`

Composition node. Steps enforce rollback definition. Gates enforce: no business logic, rollback documented, partial failure tested, all children typed.

---

## Infrastructure — wraps everything else

These modify the behavior of other nodes without changing their interface.

### `cache`
Memoizes a query or transformation. Wraps an existing node. The cache itself is not a data store — it delegates misses to the underlying node.

Leaf node.

### `auth_guard`
Enforces identity and permission on any node. Wraps an existing node. The guard rejects before the underlying node executes.

Leaf node.

### `retry_policy`
Failure recovery wrapper. Configures: max attempts, backoff strategy, which exceptions are retryable, which are not.

Leaf node.

### `observer`
Attaches logging, metrics, or tracing to any node without modifying its behavior. Structural.

Leaf node.

---

## Verification

These exist only in the context of other nodes. They don't produce business functionality.

### `unit_test`
Verifies a single leaf node in isolation. One assertion focus per test. Independent — no shared mutable state between tests. The target node was confirmed failing before implementation.

Leaf node.

### `integration_test`
Verifies multiple nodes working together. Tests real interactions, not mocks. At minimum: happy path and one failure path.

Leaf node.

### `contract_test`
Verifies that an implementation honors an `interface` node's contract. Implementation-agnostic — must pass for any correct implementation.

Leaf node.

### `fixture`
Test data factory. Produces realistic test data. No hardcoded magic values. Makes tests readable.

Leaf node.

---

## Leaf vs composition

| Category | Types |
|---|---|
| Leaf — implement directly | `data_model`, `interface`, `config`, `transformation`, `aggregation`, `validation`, `query`, `mutation`, `event_emit`, `event_handler`, `cache`, `auth_guard`, `retry_policy`, `observer`, `unit_test`, `integration_test`, `contract_test`, `fixture` |
| Composition — decompose first | `pipeline`, `router`, `orchestration` |

The distinction matters for runner behavior. When the runner encounters a composition node, it doesn't execute steps — it calls the planner's decomposer to generate typed child nodes. Execution only happens at leaves.

---

## Domain extensions

The set is intentionally general. Domain-specific additions might include:

`ml_inference` — wraps model call, input preprocessing, output postprocessing
`stream_processor` — stateful processing of event streams
`scheduled_job` — time-triggered execution with idempotency requirements
`migration` — database schema or data migration with rollback

Each extension needs: a step template, gate definitions, and a `NodeCategory` assignment. It should be added to the system's type registry, not improvised inline.
