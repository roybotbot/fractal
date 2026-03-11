"""Primitive type taxonomy, step templates, and gate templates.

This module defines the 22 closed-set primitive types that all programming
tasks decompose into, along with their step templates and gate templates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class PrimitiveType(Enum):
    # Structural
    DATA_MODEL = "data_model"
    INTERFACE = "interface"
    CONFIG = "config"

    # Computation (pure)
    TRANSFORMATION = "transformation"
    AGGREGATION = "aggregation"
    VALIDATION = "validation"

    # IO (boundary-crossing)
    QUERY = "query"
    MUTATION = "mutation"
    EVENT_EMIT = "event_emit"
    EVENT_HANDLER = "event_handler"

    # Coordination (composition)
    PIPELINE = "pipeline"
    ROUTER = "router"
    ORCHESTRATION = "orchestration"

    # Infrastructure
    CACHE = "cache"
    AUTH_GUARD = "auth_guard"
    RETRY_POLICY = "retry_policy"
    OBSERVER = "observer"

    # Verification
    UNIT_TEST = "unit_test"
    INTEGRATION_TEST = "integration_test"
    CONTRACT_TEST = "contract_test"
    FIXTURE = "fixture"


class NodeCategory(Enum):
    STRUCTURAL = "structural"
    COMPUTATION = "computation"
    IO = "io"
    COORDINATION = "coordination"
    INFRASTRUCTURE = "infrastructure"
    VERIFICATION = "verification"


CATEGORY_MAP: dict[PrimitiveType, NodeCategory] = {
    PrimitiveType.DATA_MODEL: NodeCategory.STRUCTURAL,
    PrimitiveType.INTERFACE: NodeCategory.STRUCTURAL,
    PrimitiveType.CONFIG: NodeCategory.STRUCTURAL,
    PrimitiveType.TRANSFORMATION: NodeCategory.COMPUTATION,
    PrimitiveType.AGGREGATION: NodeCategory.COMPUTATION,
    PrimitiveType.VALIDATION: NodeCategory.COMPUTATION,
    PrimitiveType.QUERY: NodeCategory.IO,
    PrimitiveType.MUTATION: NodeCategory.IO,
    PrimitiveType.EVENT_EMIT: NodeCategory.IO,
    PrimitiveType.EVENT_HANDLER: NodeCategory.IO,
    PrimitiveType.PIPELINE: NodeCategory.COORDINATION,
    PrimitiveType.ROUTER: NodeCategory.COORDINATION,
    PrimitiveType.ORCHESTRATION: NodeCategory.COORDINATION,
    PrimitiveType.CACHE: NodeCategory.INFRASTRUCTURE,
    PrimitiveType.AUTH_GUARD: NodeCategory.INFRASTRUCTURE,
    PrimitiveType.RETRY_POLICY: NodeCategory.INFRASTRUCTURE,
    PrimitiveType.OBSERVER: NodeCategory.INFRASTRUCTURE,
    PrimitiveType.UNIT_TEST: NodeCategory.VERIFICATION,
    PrimitiveType.INTEGRATION_TEST: NodeCategory.VERIFICATION,
    PrimitiveType.CONTRACT_TEST: NodeCategory.VERIFICATION,
    PrimitiveType.FIXTURE: NodeCategory.VERIFICATION,
}

COMPOSITION_TYPES = frozenset({
    PrimitiveType.PIPELINE,
    PrimitiveType.ROUTER,
    PrimitiveType.ORCHESTRATION,
})


@dataclass(frozen=True)
class StepTemplate:
    name: str
    prompt_template: str
    expected_artifacts: list[str] = field(default_factory=list)
    forbidden_artifacts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GateTemplate:
    name: str
    check_type: str  # key used to dispatch to check implementations
    on_failure: str = "block"  # "block" or "abort"
    parameters: dict = field(default_factory=dict)


# --- Step templates per type ---

_DATA_MODEL_STEPS = [
    StepTemplate(
        name="enumerate_fields",
        prompt_template="For node '{node.name}': enumerate all fields with types, constraints, and whether required.",
        expected_artifacts=["field_list"],
        forbidden_artifacts=["code", "class_definition"],
    ),
    StepTemplate(
        name="define_validation_rules",
        prompt_template="For node '{node.name}': define validation rules for each field and cross-field invariants.",
        expected_artifacts=["validation_rules"],
        forbidden_artifacts=["code"],
    ),
    StepTemplate(
        name="write_validation_tests",
        prompt_template="For node '{node.name}': write failing tests for validation rules. Do not implement the model yet.",
        expected_artifacts=["test_code"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="implement_model",
        prompt_template="For node '{node.name}': implement the data model with all fields and validation.",
        expected_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="document_invariants",
        prompt_template="For node '{node.name}': write a docstring documenting all invariants and constraints.",
        expected_artifacts=["docstring"],
    ),
]

_TRANSFORMATION_STEPS = [
    StepTemplate(
        name="define_input_schema",
        prompt_template="For node '{node.name}': define the exact input type with all fields.",
        expected_artifacts=["input_schema"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="define_output_schema",
        prompt_template="For node '{node.name}': define the exact output type.",
        expected_artifacts=["output_schema"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="enumerate_edge_cases",
        prompt_template="For node '{node.name}': list every edge case for this transformation.",
        expected_artifacts=["edge_case_list"],
        forbidden_artifacts=["test_code", "implementation_code"],
    ),
    StepTemplate(
        name="write_failing_tests",
        prompt_template="For node '{node.name}': write failing tests covering all edge cases.",
        expected_artifacts=["test_code"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="implement_minimal",
        prompt_template="For node '{node.name}': implement the minimal transformation that passes tests.",
        expected_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="refactor",
        prompt_template="For node '{node.name}': review implementation and refactor if needed. Say 'nothing needed' if clean.",
        expected_artifacts=["refactor_notes"],
    ),
]

_MUTATION_STEPS = [
    StepTemplate(
        name="define_input_schema",
        prompt_template="For node '{node.name}': define the input schema for this mutation.",
        expected_artifacts=["input_schema"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="identify_dependency",
        prompt_template="For node '{node.name}': identify the external dependency this mutation writes to.",
        expected_artifacts=["dependency_interface"],
    ),
    StepTemplate(
        name="enumerate_failure_modes",
        prompt_template="For node '{node.name}': list every failure mode for this mutation.",
        expected_artifacts=["failure_modes"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="write_failing_tests",
        prompt_template="For node '{node.name}': write failing tests including failure path tests.",
        expected_artifacts=["test_code"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="implement_with_error_handling",
        prompt_template="For node '{node.name}': implement with explicit error handling for each failure mode.",
        expected_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="verify_idempotency",
        prompt_template="For node '{node.name}': verify idempotency or explicitly waive it with justification.",
        expected_artifacts=["idempotency_notes"],
    ),
]

_INTERFACE_STEPS = [
    StepTemplate(
        name="define_method_signatures",
        prompt_template="For node '{node.name}': define all method signatures with typed parameters and return types.",
        expected_artifacts=["method_signatures"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="define_error_contract",
        prompt_template="For node '{node.name}': define the error contract — what exceptions can be raised and when.",
        expected_artifacts=["error_contract"],
    ),
    StepTemplate(
        name="define_pre_post_conditions",
        prompt_template="For node '{node.name}': define preconditions and postconditions for each method.",
        expected_artifacts=["pre_post_conditions"],
    ),
    StepTemplate(
        name="write_contract_tests",
        prompt_template="For node '{node.name}': write contract tests that any correct implementation must pass.",
        expected_artifacts=["contract_tests"],
    ),
]

_ORCHESTRATION_STEPS = [
    StepTemplate(
        name="enumerate_children",
        prompt_template="For node '{node.name}': list all child nodes with types and descriptions.",
        expected_artifacts=["child_list"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="define_sequencing",
        prompt_template="For node '{node.name}': define the execution order and dependencies between children.",
        expected_artifacts=["sequencing_rules"],
    ),
    StepTemplate(
        name="define_rollback",
        prompt_template="For node '{node.name}': define rollback behavior for each step that can fail.",
        expected_artifacts=["rollback_plan"],
    ),
    StepTemplate(
        name="write_integration_tests",
        prompt_template="For node '{node.name}': write integration tests including happy path and failure scenarios.",
        expected_artifacts=["integration_tests"],
    ),
]

_QUERY_STEPS = [
    StepTemplate(
        name="define_input_schema",
        prompt_template="For node '{node.name}': define the query input parameters.",
        expected_artifacts=["input_schema"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="define_output_schema",
        prompt_template="For node '{node.name}': define the output type including the 'not found' case.",
        expected_artifacts=["output_schema"],
    ),
    StepTemplate(
        name="enumerate_failure_modes",
        prompt_template="For node '{node.name}': list every failure mode for this query.",
        expected_artifacts=["failure_modes"],
    ),
    StepTemplate(
        name="write_failing_tests",
        prompt_template="For node '{node.name}': write failing tests covering found, not found, and error cases.",
        expected_artifacts=["test_code"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="implement",
        prompt_template="For node '{node.name}': implement the query with explicit not-found handling.",
        expected_artifacts=["implementation_code"],
    ),
]

_VALIDATION_STEPS = [
    StepTemplate(
        name="enumerate_rules",
        prompt_template="For node '{node.name}': list every validation rule.",
        expected_artifacts=["rule_list"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="write_failing_tests",
        prompt_template="For node '{node.name}': write failing tests for each validation rule.",
        expected_artifacts=["test_code"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="implement",
        prompt_template="For node '{node.name}': implement validation that returns all errors, not just the first.",
        expected_artifacts=["implementation_code"],
    ),
]

_UNIT_TEST_STEPS = [
    StepTemplate(
        name="identify_target",
        prompt_template="For node '{node.name}': identify the target function/class being tested.",
        expected_artifacts=["target_description"],
    ),
    StepTemplate(
        name="enumerate_test_cases",
        prompt_template="For node '{node.name}': list every test case before writing code.",
        expected_artifacts=["test_case_list"],
        forbidden_artifacts=["test_code"],
    ),
    StepTemplate(
        name="write_fixture",
        prompt_template="For node '{node.name}': write test fixtures and setup code.",
        expected_artifacts=["fixture_code"],
    ),
    StepTemplate(
        name="implement_cases",
        prompt_template="For node '{node.name}': implement the test cases.",
        expected_artifacts=["test_code"],
    ),
]

_CONFIG_STEPS = [
    StepTemplate(
        name="enumerate_parameters",
        prompt_template="For node '{node.name}': enumerate all configuration parameters with types, defaults, and whether required.",
        expected_artifacts=["parameter_list"],
        forbidden_artifacts=["code", "class_definition"],
    ),
    StepTemplate(
        name="define_types_and_defaults",
        prompt_template="For node '{node.name}': define the exact type for each parameter and its default value.",
        expected_artifacts=["typed_parameter_list"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="define_startup_validation",
        prompt_template="For node '{node.name}': define validation rules to run at startup. Invalid config must fail loudly.",
        expected_artifacts=["validation_rules"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="implement_with_validation",
        prompt_template="For node '{node.name}': implement the config class with startup validation for all parameters.",
        expected_artifacts=["implementation_code"],
    ),
]

_AGGREGATION_STEPS = [
    StepTemplate(
        name="define_input_collection",
        prompt_template="For node '{node.name}': define the input collection type and element type.",
        expected_artifacts=["input_schema"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="define_output_schema",
        prompt_template="For node '{node.name}': define the output type produced by the aggregation.",
        expected_artifacts=["output_schema"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="enumerate_edge_cases",
        prompt_template="For node '{node.name}': list edge cases including empty input, single element, and large collections.",
        expected_artifacts=["edge_case_list"],
        forbidden_artifacts=["test_code", "implementation_code"],
    ),
    StepTemplate(
        name="write_failing_tests",
        prompt_template="For node '{node.name}': write failing tests covering all edge cases.",
        expected_artifacts=["test_code"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="implement_minimal",
        prompt_template="For node '{node.name}': implement the minimal aggregation that passes tests.",
        expected_artifacts=["implementation_code"],
    ),
]

_EVENT_EMIT_STEPS = [
    StepTemplate(
        name="define_event_schema",
        prompt_template="For node '{node.name}': define the event payload schema with all fields and types.",
        expected_artifacts=["event_schema"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="define_delivery_semantics",
        prompt_template="For node '{node.name}': specify delivery guarantees (fire-and-forget or at-least-once).",
        expected_artifacts=["delivery_semantics"],
    ),
    StepTemplate(
        name="write_failing_tests",
        prompt_template="For node '{node.name}': write failing tests verifying the event is emitted with correct payload.",
        expected_artifacts=["test_code"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="implement",
        prompt_template="For node '{node.name}': implement the event emitter.",
        expected_artifacts=["implementation_code"],
    ),
]

_EVENT_HANDLER_STEPS = [
    StepTemplate(
        name="define_event_schema",
        prompt_template="For node '{node.name}': define the incoming event schema this handler processes.",
        expected_artifacts=["event_schema"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="define_idempotency_strategy",
        prompt_template="For node '{node.name}': define idempotency handling. Events may be delivered more than once.",
        expected_artifacts=["idempotency_strategy"],
    ),
    StepTemplate(
        name="enumerate_failure_modes",
        prompt_template="For node '{node.name}': list every failure mode for this event handler.",
        expected_artifacts=["failure_modes"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="write_failing_tests",
        prompt_template="For node '{node.name}': write failing tests including duplicate delivery and error cases.",
        expected_artifacts=["test_code"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="implement",
        prompt_template="For node '{node.name}': implement with idempotency handling and error recovery.",
        expected_artifacts=["implementation_code"],
    ),
]

_PIPELINE_STEPS = [
    StepTemplate(
        name="enumerate_stages",
        prompt_template="For node '{node.name}': list all pipeline stages in order with input/output types.",
        expected_artifacts=["stage_list"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="define_type_chain",
        prompt_template="For node '{node.name}': verify type compatibility — each stage's output must match the next stage's input.",
        expected_artifacts=["type_chain"],
    ),
    StepTemplate(
        name="define_error_propagation",
        prompt_template="For node '{node.name}': define what happens when a stage fails. Does the pipeline stop or skip?",
        expected_artifacts=["error_propagation_rules"],
    ),
    StepTemplate(
        name="write_integration_tests",
        prompt_template="For node '{node.name}': write integration tests for the full pipeline including stage failure.",
        expected_artifacts=["integration_tests"],
    ),
]

_ROUTER_STEPS = [
    StepTemplate(
        name="enumerate_routes",
        prompt_template="For node '{node.name}': list all routes with their conditions and target handlers.",
        expected_artifacts=["route_list"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="define_default_route",
        prompt_template="For node '{node.name}': define the default/fallback route when no condition matches.",
        expected_artifacts=["default_route"],
    ),
    StepTemplate(
        name="define_routing_logic",
        prompt_template="For node '{node.name}': define the routing logic. The router decides only where to send, not what to do.",
        expected_artifacts=["routing_rules"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="write_integration_tests",
        prompt_template="For node '{node.name}': write tests for each route including the default/fallback case.",
        expected_artifacts=["integration_tests"],
    ),
]

_CACHE_STEPS = [
    StepTemplate(
        name="define_wrapped_node",
        prompt_template="For node '{node.name}': identify the underlying node this cache wraps.",
        expected_artifacts=["wrapped_interface"],
    ),
    StepTemplate(
        name="define_cache_key",
        prompt_template="For node '{node.name}': define the cache key derivation from the input parameters.",
        expected_artifacts=["cache_key_strategy"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="define_eviction_policy",
        prompt_template="For node '{node.name}': define TTL, max size, and eviction strategy.",
        expected_artifacts=["eviction_policy"],
    ),
    StepTemplate(
        name="write_failing_tests",
        prompt_template="For node '{node.name}': write tests for cache hit, miss, and eviction.",
        expected_artifacts=["test_code"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="implement",
        prompt_template="For node '{node.name}': implement the cache wrapper.",
        expected_artifacts=["implementation_code"],
    ),
]

_AUTH_GUARD_STEPS = [
    StepTemplate(
        name="define_protected_node",
        prompt_template="For node '{node.name}': identify the node this guard protects.",
        expected_artifacts=["protected_interface"],
    ),
    StepTemplate(
        name="define_permission_model",
        prompt_template="For node '{node.name}': define the identity and permission requirements.",
        expected_artifacts=["permission_model"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="define_rejection_behavior",
        prompt_template="For node '{node.name}': define what happens on auth failure (error type, response).",
        expected_artifacts=["rejection_behavior"],
    ),
    StepTemplate(
        name="write_failing_tests",
        prompt_template="For node '{node.name}': write tests for allowed, denied, and missing-auth cases.",
        expected_artifacts=["test_code"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="implement",
        prompt_template="For node '{node.name}': implement the auth guard.",
        expected_artifacts=["implementation_code"],
    ),
]

_RETRY_POLICY_STEPS = [
    StepTemplate(
        name="define_wrapped_node",
        prompt_template="For node '{node.name}': identify the node this retry policy wraps.",
        expected_artifacts=["wrapped_interface"],
    ),
    StepTemplate(
        name="define_retry_parameters",
        prompt_template="For node '{node.name}': define max attempts, backoff strategy, and which exceptions are retryable.",
        expected_artifacts=["retry_parameters"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="define_exhaustion_behavior",
        prompt_template="For node '{node.name}': define what happens when retries are exhausted.",
        expected_artifacts=["exhaustion_behavior"],
    ),
    StepTemplate(
        name="write_failing_tests",
        prompt_template="For node '{node.name}': write tests for success, retry-then-succeed, and retry-exhaustion.",
        expected_artifacts=["test_code"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="implement",
        prompt_template="For node '{node.name}': implement the retry wrapper.",
        expected_artifacts=["implementation_code"],
    ),
]

_OBSERVER_STEPS = [
    StepTemplate(
        name="define_observed_node",
        prompt_template="For node '{node.name}': identify the node being observed.",
        expected_artifacts=["observed_interface"],
    ),
    StepTemplate(
        name="define_observation_points",
        prompt_template="For node '{node.name}': define what is observed (timing, inputs, outputs, errors).",
        expected_artifacts=["observation_points"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="write_failing_tests",
        prompt_template="For node '{node.name}': write tests verifying observations are recorded without altering behavior.",
        expected_artifacts=["test_code"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="implement",
        prompt_template="For node '{node.name}': implement the observer. Must not modify the observed node's behavior.",
        expected_artifacts=["implementation_code"],
    ),
]

_INTEGRATION_TEST_STEPS = [
    StepTemplate(
        name="identify_components",
        prompt_template="For node '{node.name}': identify the components being tested together.",
        expected_artifacts=["component_list"],
    ),
    StepTemplate(
        name="enumerate_scenarios",
        prompt_template="For node '{node.name}': list test scenarios: happy path and at least one failure path.",
        expected_artifacts=["scenario_list"],
        forbidden_artifacts=["test_code"],
    ),
    StepTemplate(
        name="write_fixture",
        prompt_template="For node '{node.name}': write test fixtures and setup code.",
        expected_artifacts=["fixture_code"],
    ),
    StepTemplate(
        name="implement_cases",
        prompt_template="For node '{node.name}': implement the integration test cases using real interactions, not mocks.",
        expected_artifacts=["test_code"],
    ),
]

_CONTRACT_TEST_STEPS = [
    StepTemplate(
        name="identify_interface",
        prompt_template="For node '{node.name}': identify the interface contract being verified.",
        expected_artifacts=["interface_reference"],
    ),
    StepTemplate(
        name="enumerate_contract_points",
        prompt_template="For node '{node.name}': list every contract point (method, precondition, postcondition, error).",
        expected_artifacts=["contract_point_list"],
        forbidden_artifacts=["test_code"],
    ),
    StepTemplate(
        name="write_fixture",
        prompt_template="For node '{node.name}': write fixture that accepts any correct implementation.",
        expected_artifacts=["fixture_code"],
    ),
    StepTemplate(
        name="implement_cases",
        prompt_template="For node '{node.name}': implement contract tests that are implementation-agnostic.",
        expected_artifacts=["test_code"],
    ),
]

_FIXTURE_STEPS = [
    StepTemplate(
        name="identify_target_tests",
        prompt_template="For node '{node.name}': identify the tests this fixture supports.",
        expected_artifacts=["target_description"],
    ),
    StepTemplate(
        name="define_data_shape",
        prompt_template="For node '{node.name}': define the shape and constraints of the test data.",
        expected_artifacts=["data_shape"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="implement",
        prompt_template="For node '{node.name}': implement the fixture factory. No hardcoded magic values.",
        expected_artifacts=["implementation_code"],
    ),
]

_GENERIC_STEPS = [
    StepTemplate(
        name="define_inputs_outputs",
        prompt_template="For node '{node.name}': define the inputs and outputs.",
        expected_artifacts=["io_definition"],
    ),
    StepTemplate(
        name="write_failing_tests",
        prompt_template="For node '{node.name}': write failing tests.",
        expected_artifacts=["test_code"],
        forbidden_artifacts=["implementation_code"],
    ),
    StepTemplate(
        name="implement",
        prompt_template="For node '{node.name}': implement.",
        expected_artifacts=["implementation_code"],
    ),
]


STEP_TEMPLATES: dict[PrimitiveType, list[StepTemplate]] = {
    PrimitiveType.DATA_MODEL: _DATA_MODEL_STEPS,
    PrimitiveType.TRANSFORMATION: _TRANSFORMATION_STEPS,
    PrimitiveType.MUTATION: _MUTATION_STEPS,
    PrimitiveType.INTERFACE: _INTERFACE_STEPS,
    PrimitiveType.ORCHESTRATION: _ORCHESTRATION_STEPS,
    PrimitiveType.QUERY: _QUERY_STEPS,
    PrimitiveType.VALIDATION: _VALIDATION_STEPS,
    PrimitiveType.UNIT_TEST: _UNIT_TEST_STEPS,
    PrimitiveType.CONFIG: _CONFIG_STEPS,
    PrimitiveType.AGGREGATION: _AGGREGATION_STEPS,
    PrimitiveType.EVENT_EMIT: _EVENT_EMIT_STEPS,
    PrimitiveType.EVENT_HANDLER: _EVENT_HANDLER_STEPS,
    PrimitiveType.PIPELINE: _PIPELINE_STEPS,
    PrimitiveType.ROUTER: _ROUTER_STEPS,
    PrimitiveType.CACHE: _CACHE_STEPS,
    PrimitiveType.AUTH_GUARD: _AUTH_GUARD_STEPS,
    PrimitiveType.RETRY_POLICY: _RETRY_POLICY_STEPS,
    PrimitiveType.OBSERVER: _OBSERVER_STEPS,
    PrimitiveType.INTEGRATION_TEST: _INTEGRATION_TEST_STEPS,
    PrimitiveType.CONTRACT_TEST: _CONTRACT_TEST_STEPS,
    PrimitiveType.FIXTURE: _FIXTURE_STEPS,
}


def get_steps(primitive_type: PrimitiveType) -> list[StepTemplate]:
    """Return the step template list for a given type. Falls back to generic."""
    return STEP_TEMPLATES.get(primitive_type, _GENERIC_STEPS)
