# Step templates

Step templates are the enforcement mechanism for the superpowers methodology. Every node type has a fixed, ordered sequence of steps. The runner executes them in order. The LLM cannot skip, reorder, or substitute steps.

The key principle: steps enforce *when* things happen. Gates enforce *whether* they happened correctly. Together they close the loop.

---

## Template structure

```python
@dataclass(frozen=True)
class StepTemplate:
    name: str                        # stable identifier, used in logs and drift detection
    prompt_template: str             # {node} injected at runtime
    expected_artifacts: list[str]    # what the output must contain
    forbidden_artifacts: list[str]   # what the output must NOT contain (phase enforcement)
```

`expected_artifacts` and `forbidden_artifacts` are string labels, not code. The drift detector uses them as classification targets for its phase check — "does this output contain implementation_code?" is a semantic check made by the detector, not a regex.

---

## Superpowers methodology mapping

Each step type maps to one or more superpowers skill phases:

| Step type | Superpowers skill |
|---|---|
| `enumerate_fields`, `define_input_schema`, `define_output_schema` | `brainstorming` |
| `write_failing_tests` | `test-driven-development` (RED phase) |
| `implement_*`, `implement_minimal` | `test-driven-development` (GREEN phase) |
| `refactor` | `test-driven-development` (REFACTOR phase) |
| `enumerate_children`, `define_sequencing` | `writing-plans` |
| Steps producing output for review | `requesting-code-review` |
| Rollback definition, failure mode enumeration | `systematic-debugging` |

The superpowers philosophy — spec before code, test before implementation, review before merge — is baked into every step sequence. It's not optional methodology; it's the template.

---

## Step sequences per type

### `data_model`

```
enumerate_fields
  ↓ expected: field_list
  ↓ forbidden: code, class_definition

define_validation_rules
  ↓ expected: validation_rules
  ↓ forbidden: code

write_validation_tests
  ↓ expected: test_code
  ↓ forbidden: implementation_code

implement_model
  ↓ expected: implementation_code

document_invariants
  ↓ expected: docstring
```

The two planning steps before any code is written enforce the brainstorming phase. You can't implement a model without first enumerating its fields and validation rules. The test step before implementation is TDD red phase.

---

### `transformation`

```
define_input_schema
  ↓ expected: input_schema
  ↓ forbidden: implementation_code

define_output_schema
  ↓ expected: output_schema
  ↓ forbidden: implementation_code

enumerate_edge_cases
  ↓ expected: edge_case_list
  ↓ forbidden: test_code, implementation_code

write_failing_tests
  ↓ expected: test_code
  ↓ forbidden: implementation_code

implement_minimal
  ↓ expected: implementation_code

refactor
  ↓ expected: refactor_notes
```

Three steps before any code. Schema definition, then edge case enumeration, then tests. Only then implementation. Refactor is the last step, and it's allowed to say "nothing needed" — its job is to force the model to look back at what it wrote.

---

### `mutation`

```
define_input_schema
  ↓ expected: input_schema
  ↓ forbidden: implementation_code

identify_dependency
  ↓ expected: dependency_interface

enumerate_failure_modes
  ↓ expected: failure_modes
  ↓ forbidden: implementation_code

write_failing_tests
  ↓ expected: test_code
  ↓ forbidden: implementation_code

implement_with_error_handling
  ↓ expected: implementation_code

verify_idempotency
  ↓ expected: idempotency_notes
```

`enumerate_failure_modes` is the step that LLMs most want to skip. It forces explicit acknowledgment of what can go wrong before writing a single line of implementation. The `verify_idempotency` step forces a deliberate decision — idempotency is either implemented or explicitly waived.

---

### `interface`

```
define_method_signatures
  ↓ expected: method_signatures
  ↓ forbidden: implementation_code

define_error_contract
  ↓ expected: error_contract

define_pre_post_conditions
  ↓ expected: pre_post_conditions

write_contract_tests
  ↓ expected: contract_tests
```

No implementation step — interfaces don't implement. The contract tests step is the only code production step, and those tests must be implementation-agnostic (checked by a gate).

---

### `orchestration`

```
enumerate_children
  ↓ expected: child_list
  ↓ forbidden: implementation_code

define_sequencing
  ↓ expected: sequencing_rules

define_rollback
  ↓ expected: rollback_plan

write_integration_tests
  ↓ expected: integration_tests
```

The orchestration steps are all planning and verification. The orchestration node itself produces no implementation code — its children do. The `define_rollback` step is the one most commonly skipped in naive implementations. Making it a named step with an expected artifact forces explicit acknowledgment.

---

### `query`

```
define_input_schema
  ↓ expected: input_schema
  ↓ forbidden: implementation_code

define_output_schema
  ↓ expected: output_schema
  (includes: what does "not found" return?)

enumerate_failure_modes
  ↓ expected: failure_modes

write_failing_tests
  ↓ expected: test_code
  ↓ forbidden: implementation_code

implement
  ↓ expected: implementation_code
```

The `define_output_schema` step explicitly prompts for the "not found" case. This becomes a gate: if the implementation doesn't handle "not found", it fails. The step template is what surfaces the requirement early.

---

### `validation`

```
enumerate_rules
  ↓ expected: rule_list
  ↓ forbidden: implementation_code

write_failing_tests
  ↓ expected: test_code
  ↓ forbidden: implementation_code

implement
  ↓ expected: implementation_code
```

Minimal but enforced. Rules before tests, tests before implementation. The gate checks return-all-errors behavior — which is the most common validation implementation mistake.

---

### `unit_test`

```
identify_target
  ↓ expected: target_description

enumerate_test_cases
  ↓ expected: test_case_list
  ↓ forbidden: test_code

write_fixture
  ↓ expected: fixture_code

implement_cases
  ↓ expected: test_code
```

`enumerate_test_cases` before `implement_cases` is the step most LLMs want to skip. They want to write tests directly. But listing cases first forces completeness — it's much harder to forget an edge case when you've explicitly enumerated them all before writing any code.

---

## Generic fallback

Node types without a specific template get:

```
define_inputs_outputs
  ↓ expected: io_definition

write_failing_tests
  ↓ expected: test_code
  ↓ forbidden: implementation_code

implement
  ↓ expected: implementation_code
```

This is the minimum viable sequence: specify the interface, test before implementing, implement minimally. Even with no domain-specific template, the system enforces TDD.

---

## Prompt template injection

At runtime, `{node}` in a prompt template is replaced with the full `TaskNode` object. This means prompt templates can reference any node attribute:

```python
f"For node '{node.name}': define the exact input type."
# becomes:
"For node 'generate_reset_token': define the exact input type."
```

The injection happens in `context_builder.build()` after the system context block is assembled. The step prompt is always the last block in the context, so it's what the model reads immediately before generating its response.
