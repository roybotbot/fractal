# Gate system

Gates are the completion enforcement mechanism. They run after all steps in a node are done, before the node is marked `COMPLETE`. Every gate must pass. A single failing gate holds the node in `BLOCKED` status until the failure is resolved.

Gates are the part of the system that can't be talked past. Steps can drift. Instruction adherence can be argued about. Gates are objective: the test either passes or it doesn't, the AST either contains `Any` or it doesn't.

---

## Gate types by check mechanism

### AST-based gates

These use Python's `ast` module to analyze the generated code structurally. They run fast and produce unambiguous results.

`ast_no_any` — walks the AST looking for type annotations of `Any`, unannotated `dict`, or bare `object`. Flags line numbers. Used on almost every leaf node type.

`ast_no_io` — checks for imports of forbidden modules and calls to I/O functions. Used on `transformation`, `aggregation`, `validation` — anything that claims to be pure. Forbidden by default: `os`, `sys`, `requests`, `httpx`, `aiohttp`, and the built-in `open`.

`ast_has_exception_handling` — verifies at least one `try/except` block is present. Used on `mutation` and `query` nodes where external failures are expected.

`ast_no_mutations` — checks for write operations: assignment to attributes of external objects, database write calls, file writes. Used on `query` nodes to enforce read-only behavior.

`ast_no_shared_mutable_state` — looks for module-level mutable variables used across test functions. Used on `unit_test` nodes.

### Test runner gates

`run_tests` — actually executes the test suite for this node. Returns pass/fail plus the output. This is the gate that catches the model lying about test results. It's also the most expensive gate — it runs real code. But it's non-negotiable. Without it, completion drift (the model saying "tests pass" when they don't) has no objective check.

`test_count_minimum` — counts test functions/methods in the test file. Parameterized with a minimum count. Used on `transformation` nodes (minimum 3 — enforces the edge case coverage requirement).

`test_covers_exceptions` — checks that at least one test function explicitly tests an exception path (`pytest.raises`, `assertRaises`, try/except in test body). Used on `mutation` nodes.

`test_covers_partial_failure` — checks that integration tests include a failure scenario. Used on `orchestration` nodes.

### Structural gates

`file_contains_tests` — checks that a test file exists and is non-empty. Lower bar than `run_tests` — used when the node type requires tests exist but execution happens at a parent level.

`has_docstring` — verifies the primary class or function has a non-empty docstring. Used on `data_model` nodes.

`has_documented_exceptions` — checks that raised exceptions are documented in the docstring or via a formal exception annotation. Used on `interface` nodes.

`has_rollback_documentation` — checks that rollback behavior is described either in a comment, docstring, or implementation. Used on `orchestration` nodes.

`children_have_types` — for composition nodes, verifies all children have a valid `primitive_type`. If any child is untyped, this is an abort — the tree was malformed.

### LLM judge gates

`llm_judge` — a separate, constrained LLM call with a specific yes/no question. Used for checks that require semantic understanding that AST analysis can't provide.

Examples of what LLM judge checks:
- "Does this mutation contain business logic or is it purely an I/O layer?"
- "Are these contract tests implementation-agnostic?"
- "Does this orchestrator contain business logic?"
- "Does this validation return all errors or stop at the first?"

The judge call is constrained: system prompt specifies the exact question and the valid answers. The response is parsed for the answer token — any deviation from the expected answer format causes a retry of the judge call, not a gate failure.

---

## Gate templates per node type

### `data_model`
- `no_any_types` — AST check, block on failure
- `validation_tests_exist` — structural check, block on failure
- `invariants_documented` — structural check (docstring), block on failure

### `transformation`
- `no_any_types` — AST check, block
- `no_io_calls` — AST check, block (forbidden: os, sys, requests, httpx, aiohttp, open)
- `tests_exist_and_pass` — test runner, block
- `edge_cases_covered` — test count minimum 3, block

### `mutation`
- `no_any_types` — AST check, block
- `failure_modes_handled` — AST exception handling check, block
- `no_business_logic` — LLM judge, block
- `tests_exist_and_pass` — test runner, block
- `failure_path_tested` — test covers exceptions, block

### `interface`
- `no_any_types` — AST check, block
- `contract_tests_exist` — structural check, block
- `contract_tests_implementation_agnostic` — LLM judge, block
- `error_contract_defined` — documented exceptions check, block

### `orchestration`
- `no_business_logic_in_orchestrator` — LLM judge, block
- `rollback_defined` — rollback documentation check, block
- `failure_path_tested` — partial failure coverage check, block
- `all_children_typed` — children type check, **abort** (planning error)

### `query`
- `no_any_types` — AST check, block
- `no_side_effects` — AST no mutations check, block
- `not_found_case_handled` — LLM judge, block
- `tests_exist_and_pass` — test runner, block

### `unit_test`
- `tests_are_independent` — AST no shared mutable state, block
- `single_assertion_focus` — LLM judge, block

### `validation`
- `returns_all_errors` — LLM judge, block
- `no_side_effects` — AST no IO check, block
- `tests_exist_and_pass` — test runner, block

---

## Failure handling

### `block`

Node transitions to `BLOCKED`. The failing gate's `evidence` string is injected into the next LLM call as correction context. The runner re-executes the relevant step (the last step that produced the artifact the gate is checking against). One retry at the step level. If the gate fails again after the retry, it escalates to `abort`.

### `abort`

Node transitions to `FAILED`. Session pauses. Human must review before execution can continue. The abort case in practice is almost always the `all_children_typed` gate — a planning error that means the tree structure itself is wrong.

---

## Gate result persistence

Each gate run produces a `GateResult`:

```python
@dataclass
class GateResult:
    gate: GateTemplate
    passed: bool
    evidence: str      # what was found — shown to human and in logs
    checked_at: datetime
```

Gate results are stored on the node and serialized with the session state. On session resume, gates that already passed don't re-run. This is important for long sessions — re-running all gates from scratch on resume would be expensive and pointless.

---

## The generic fallback

Node types without a specific gate template fall back to:
- `tests_exist_and_pass` — block on failure
- `no_any_types` — block on failure

This is a conservative default. A node that was never explicitly gated still can't complete without tests passing and without `Any` types in its schema.
