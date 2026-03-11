"""Gate template registry.

Maps each PrimitiveType to its list of GateTemplate objects.
Gate templates define what checks run after a node's steps complete.
"""

from __future__ import annotations

from .primitives import GateTemplate, PrimitiveType


_DATA_MODEL_GATES = [
    GateTemplate(
        name="no_any_types",
        check_type="ast_no_any",
        on_failure="block",
    ),
    GateTemplate(
        name="validation_tests_exist",
        check_type="file_contains_tests",
        on_failure="block",
    ),
    GateTemplate(
        name="invariants_documented",
        check_type="has_docstring",
        on_failure="block",
    ),
]

_TRANSFORMATION_GATES = [
    GateTemplate(
        name="no_any_types",
        check_type="ast_no_any",
        on_failure="block",
    ),
    GateTemplate(
        name="no_io_calls",
        check_type="ast_no_io",
        on_failure="block",
        parameters={"forbidden_modules": ["os", "sys", "requests", "httpx", "aiohttp"],
                     "forbidden_builtins": ["open"]},
    ),
    GateTemplate(
        name="tests_exist_and_pass",
        check_type="run_tests",
        on_failure="block",
    ),
    GateTemplate(
        name="edge_cases_covered",
        check_type="test_count_minimum",
        on_failure="block",
        parameters={"minimum": 3},
    ),
]

_MUTATION_GATES = [
    GateTemplate(
        name="no_any_types",
        check_type="ast_no_any",
        on_failure="block",
    ),
    GateTemplate(
        name="failure_modes_handled",
        check_type="ast_has_exception_handling",
        on_failure="block",
    ),
    GateTemplate(
        name="no_business_logic",
        check_type="llm_judge",
        on_failure="block",
        parameters={"question": "Does this mutation contain business logic or is it purely an I/O layer?",
                     "expected_answer": "pure_io"},
    ),
    GateTemplate(
        name="tests_exist_and_pass",
        check_type="run_tests",
        on_failure="block",
    ),
    GateTemplate(
        name="failure_path_tested",
        check_type="test_covers_exceptions",
        on_failure="block",
    ),
]

_INTERFACE_GATES = [
    GateTemplate(
        name="no_any_types",
        check_type="ast_no_any",
        on_failure="block",
    ),
    GateTemplate(
        name="contract_tests_exist",
        check_type="file_contains_tests",
        on_failure="block",
    ),
    GateTemplate(
        name="contract_tests_implementation_agnostic",
        check_type="llm_judge",
        on_failure="block",
        parameters={"question": "Are these contract tests implementation-agnostic?",
                     "expected_answer": "yes"},
    ),
    GateTemplate(
        name="error_contract_defined",
        check_type="has_documented_exceptions",
        on_failure="block",
    ),
]

_ORCHESTRATION_GATES = [
    GateTemplate(
        name="no_business_logic_in_orchestrator",
        check_type="llm_judge",
        on_failure="block",
        parameters={"question": "Does this orchestrator contain business logic?",
                     "expected_answer": "no"},
    ),
    GateTemplate(
        name="rollback_defined",
        check_type="has_rollback_documentation",
        on_failure="block",
    ),
    GateTemplate(
        name="failure_path_tested",
        check_type="test_covers_partial_failure",
        on_failure="block",
    ),
    GateTemplate(
        name="all_children_typed",
        check_type="children_have_types",
        on_failure="abort",
    ),
]

_QUERY_GATES = [
    GateTemplate(
        name="no_any_types",
        check_type="ast_no_any",
        on_failure="block",
    ),
    GateTemplate(
        name="no_side_effects",
        check_type="ast_no_mutations",
        on_failure="block",
    ),
    GateTemplate(
        name="not_found_case_handled",
        check_type="llm_judge",
        on_failure="block",
        parameters={"question": "Does this query handle the not-found case explicitly?",
                     "expected_answer": "yes"},
    ),
    GateTemplate(
        name="tests_exist_and_pass",
        check_type="run_tests",
        on_failure="block",
    ),
]

_UNIT_TEST_GATES = [
    GateTemplate(
        name="tests_are_independent",
        check_type="ast_no_shared_mutable_state",
        on_failure="block",
    ),
    GateTemplate(
        name="single_assertion_focus",
        check_type="llm_judge",
        on_failure="block",
        parameters={"question": "Does each test function focus on a single assertion?",
                     "expected_answer": "yes"},
    ),
]

_VALIDATION_GATES = [
    GateTemplate(
        name="returns_all_errors",
        check_type="llm_judge",
        on_failure="block",
        parameters={"question": "Does this validation return all errors or stop at the first?",
                     "expected_answer": "all_errors"},
    ),
    GateTemplate(
        name="no_side_effects",
        check_type="ast_no_io",
        on_failure="block",
        parameters={"forbidden_modules": ["os", "sys", "requests", "httpx", "aiohttp"],
                     "forbidden_builtins": ["open"]},
    ),
    GateTemplate(
        name="tests_exist_and_pass",
        check_type="run_tests",
        on_failure="block",
    ),
]

_CONFIG_GATES = [
    GateTemplate(
        name="no_any_types",
        check_type="ast_no_any",
        on_failure="block",
    ),
    GateTemplate(
        name="startup_validation_exists",
        check_type="llm_judge",
        on_failure="block",
        parameters={"question": "Does this config validate all parameters at startup?",
                     "expected_answer": "yes"},
    ),
    GateTemplate(
        name="no_hardcoded_values",
        check_type="llm_judge",
        on_failure="block",
        parameters={"question": "Are there hardcoded config values that should be parameters?",
                     "expected_answer": "no"},
    ),
]

_AGGREGATION_GATES = [
    GateTemplate(
        name="no_any_types",
        check_type="ast_no_any",
        on_failure="block",
    ),
    GateTemplate(
        name="no_io_calls",
        check_type="ast_no_io",
        on_failure="block",
        parameters={"forbidden_modules": ["os", "sys", "requests", "httpx", "aiohttp"],
                     "forbidden_builtins": ["open"]},
    ),
    GateTemplate(
        name="tests_exist_and_pass",
        check_type="run_tests",
        on_failure="block",
    ),
    GateTemplate(
        name="edge_cases_covered",
        check_type="test_count_minimum",
        on_failure="block",
        parameters={"minimum": 3},
    ),
]

_EVENT_EMIT_GATES = [
    GateTemplate(
        name="no_any_types",
        check_type="ast_no_any",
        on_failure="block",
    ),
    GateTemplate(
        name="tests_exist_and_pass",
        check_type="run_tests",
        on_failure="block",
    ),
    GateTemplate(
        name="event_schema_documented",
        check_type="has_docstring",
        on_failure="block",
    ),
]

_EVENT_HANDLER_GATES = [
    GateTemplate(
        name="no_any_types",
        check_type="ast_no_any",
        on_failure="block",
    ),
    GateTemplate(
        name="failure_modes_handled",
        check_type="ast_has_exception_handling",
        on_failure="block",
    ),
    GateTemplate(
        name="idempotency_documented",
        check_type="llm_judge",
        on_failure="block",
        parameters={"question": "Is idempotency handling documented or explicitly waived?",
                     "expected_answer": "yes"},
    ),
    GateTemplate(
        name="tests_exist_and_pass",
        check_type="run_tests",
        on_failure="block",
    ),
]

_PIPELINE_GATES = [
    GateTemplate(
        name="no_business_logic",
        check_type="llm_judge",
        on_failure="block",
        parameters={"question": "Does this pipeline contain business logic or only stage orchestration?",
                     "expected_answer": "only_orchestration"},
    ),
    GateTemplate(
        name="type_chain_compatible",
        check_type="llm_judge",
        on_failure="block",
        parameters={"question": "Does each stage's output type match the next stage's input type?",
                     "expected_answer": "yes"},
    ),
    GateTemplate(
        name="all_children_typed",
        check_type="children_have_types",
        on_failure="abort",
    ),
]

_ROUTER_GATES = [
    GateTemplate(
        name="no_business_logic",
        check_type="llm_judge",
        on_failure="block",
        parameters={"question": "Does this router contain business logic or only routing decisions?",
                     "expected_answer": "only_routing"},
    ),
    GateTemplate(
        name="default_route_defined",
        check_type="llm_judge",
        on_failure="block",
        parameters={"question": "Is there a default/fallback route defined?",
                     "expected_answer": "yes"},
    ),
    GateTemplate(
        name="all_children_typed",
        check_type="children_have_types",
        on_failure="abort",
    ),
]

_CACHE_GATES = [
    GateTemplate(
        name="no_any_types",
        check_type="ast_no_any",
        on_failure="block",
    ),
    GateTemplate(
        name="tests_exist_and_pass",
        check_type="run_tests",
        on_failure="block",
    ),
    GateTemplate(
        name="cache_transparent",
        check_type="llm_judge",
        on_failure="block",
        parameters={"question": "Is the cache transparent — does it preserve the underlying node's interface?",
                     "expected_answer": "yes"},
    ),
]

_AUTH_GUARD_GATES = [
    GateTemplate(
        name="no_any_types",
        check_type="ast_no_any",
        on_failure="block",
    ),
    GateTemplate(
        name="tests_exist_and_pass",
        check_type="run_tests",
        on_failure="block",
    ),
    GateTemplate(
        name="rejection_before_execution",
        check_type="llm_judge",
        on_failure="block",
        parameters={"question": "Does the guard reject unauthorized requests before the protected node executes?",
                     "expected_answer": "yes"},
    ),
]

_RETRY_POLICY_GATES = [
    GateTemplate(
        name="no_any_types",
        check_type="ast_no_any",
        on_failure="block",
    ),
    GateTemplate(
        name="tests_exist_and_pass",
        check_type="run_tests",
        on_failure="block",
    ),
    GateTemplate(
        name="non_retryable_exceptions_defined",
        check_type="llm_judge",
        on_failure="block",
        parameters={"question": "Are non-retryable exceptions explicitly distinguished from retryable ones?",
                     "expected_answer": "yes"},
    ),
]

_OBSERVER_GATES = [
    GateTemplate(
        name="no_any_types",
        check_type="ast_no_any",
        on_failure="block",
    ),
    GateTemplate(
        name="tests_exist_and_pass",
        check_type="run_tests",
        on_failure="block",
    ),
    GateTemplate(
        name="no_behavior_modification",
        check_type="llm_judge",
        on_failure="block",
        parameters={"question": "Does the observer modify the observed node's behavior or only record observations?",
                     "expected_answer": "only_record"},
    ),
]

_INTEGRATION_TEST_GATES = [
    GateTemplate(
        name="uses_real_interactions",
        check_type="llm_judge",
        on_failure="block",
        parameters={"question": "Do these integration tests use real component interactions (not mocks)?",
                     "expected_answer": "yes"},
    ),
    GateTemplate(
        name="has_failure_scenario",
        check_type="llm_judge",
        on_failure="block",
        parameters={"question": "Is there at least one failure path scenario tested?",
                     "expected_answer": "yes"},
    ),
]

_CONTRACT_TEST_GATES = [
    GateTemplate(
        name="implementation_agnostic",
        check_type="llm_judge",
        on_failure="block",
        parameters={"question": "Are these contract tests implementation-agnostic?",
                     "expected_answer": "yes"},
    ),
    GateTemplate(
        name="tests_exist_and_pass",
        check_type="run_tests",
        on_failure="block",
    ),
]

_FIXTURE_GATES = [
    GateTemplate(
        name="no_magic_values",
        check_type="llm_judge",
        on_failure="block",
        parameters={"question": "Does this fixture avoid hardcoded magic values?",
                     "expected_answer": "yes"},
    ),
    GateTemplate(
        name="no_any_types",
        check_type="ast_no_any",
        on_failure="block",
    ),
]

_GENERIC_GATES = [
    GateTemplate(
        name="tests_exist_and_pass",
        check_type="run_tests",
        on_failure="block",
    ),
    GateTemplate(
        name="no_any_types",
        check_type="ast_no_any",
        on_failure="block",
    ),
]


GATE_TEMPLATES: dict[PrimitiveType, list[GateTemplate]] = {
    PrimitiveType.DATA_MODEL: _DATA_MODEL_GATES,
    PrimitiveType.TRANSFORMATION: _TRANSFORMATION_GATES,
    PrimitiveType.MUTATION: _MUTATION_GATES,
    PrimitiveType.INTERFACE: _INTERFACE_GATES,
    PrimitiveType.ORCHESTRATION: _ORCHESTRATION_GATES,
    PrimitiveType.QUERY: _QUERY_GATES,
    PrimitiveType.UNIT_TEST: _UNIT_TEST_GATES,
    PrimitiveType.VALIDATION: _VALIDATION_GATES,
    PrimitiveType.CONFIG: _CONFIG_GATES,
    PrimitiveType.AGGREGATION: _AGGREGATION_GATES,
    PrimitiveType.EVENT_EMIT: _EVENT_EMIT_GATES,
    PrimitiveType.EVENT_HANDLER: _EVENT_HANDLER_GATES,
    PrimitiveType.PIPELINE: _PIPELINE_GATES,
    PrimitiveType.ROUTER: _ROUTER_GATES,
    PrimitiveType.CACHE: _CACHE_GATES,
    PrimitiveType.AUTH_GUARD: _AUTH_GUARD_GATES,
    PrimitiveType.RETRY_POLICY: _RETRY_POLICY_GATES,
    PrimitiveType.OBSERVER: _OBSERVER_GATES,
    PrimitiveType.INTEGRATION_TEST: _INTEGRATION_TEST_GATES,
    PrimitiveType.CONTRACT_TEST: _CONTRACT_TEST_GATES,
    PrimitiveType.FIXTURE: _FIXTURE_GATES,
}


def get_gates(primitive_type: PrimitiveType) -> list[GateTemplate]:
    """Return the gate template list for a given type. Falls back to generic."""
    return GATE_TEMPLATES.get(primitive_type, _GENERIC_GATES)
