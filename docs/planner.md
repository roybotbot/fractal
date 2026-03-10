# Planner

The planner takes a raw task description and produces a typed `TaskTree`. It's the most LLM-dependent component and therefore the most carefully constrained.

A bad tree poisons everything downstream. The planner is where human checkpointing matters most — not during execution, but after the plan is produced and before autonomous execution begins.

---

## Planner phases

### Phase 1: classification

The raw task string is sent to a constrained LLM call that returns a `PrimitiveType`.

```python
def classify(self, task_description: str) -> PrimitiveType:
    prompt = CLASSIFICATION_PROMPT.format(
        types=PrimitiveType_descriptions(),
        task=task_description,
    )
    response = self.llm_client.call(prompt, max_tokens=50)
    # Response must be exactly one of the 22 type strings
    # If it's not, retry up to 3 times
    # If all retries fail, raise ClassificationFailure
    return PrimitiveType(response.strip())
```

The classification prompt presents all 22 types with one-sentence descriptions and asks for exactly the type string. No explanation. No hedging. One token answer. If the model can't classify the task into the closed set, that's useful information — it might be a domain-specific type that needs to be added.

### Phase 2: root node creation

```python
def create_root(self, task: str, primitive_type: PrimitiveType) -> TaskNode:
    # Slot-filling call: generate name, description, implementation_notes
    # The type is already fixed — the LLM can't change it
    response = self.llm_client.call(
        ROOT_NODE_PROMPT.format(task=task, type=primitive_type.value)
    )
    # Parse structured response (JSON)
    return TaskNode(
        name=response["name"],
        description=response["description"],
        implementation_notes=response.get("notes", ""),
        primitive_type=primitive_type,
    )
```

### Phase 3: decomposition (composition nodes only)

If the root is a composition type (`pipeline`, `router`, `orchestration`), the decomposer runs:

```python
def decompose(self, node: TaskNode) -> list[TaskNode]:
    prompt = DECOMPOSITION_PROMPT.format(
        node_name=node.name,
        node_description=node.description,
        node_type=node.primitive_type.value,
        available_types=PrimitiveType_descriptions(),
    )
    response = self.llm_client.call(prompt)
    # Response is a JSON list of child node specs
    # Each spec must include: name, description, primitive_type, dependency_ids
    children = []
    for spec in response["children"]:
        child_type = PrimitiveType(spec["type"])  # validated against enum
        child = TaskNode(
            name=spec["name"],
            description=spec["description"],
            primitive_type=child_type,
            dependency_ids=spec.get("dependencies", []),
            parent_id=node.id,
        )
        children.append(child)
    return children
```

Each child's `primitive_type` is validated against the enum. An unrecognized type is a hard error — the decomposer doesn't guess, it rejects and retries. If the type is a composition type, the decomposer will run recursively on that child when the runner reaches it.

### Phase 4: human checkpoint

Before autonomous execution begins, the planner presents the full tree to the human:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PLAN READY FOR REVIEW
Task: "user can reset their password"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

orchestration: password_reset_flow
  ├─ data_model: PasswordResetToken
  ├─ transformation: generate_reset_token
  │   depends on: PasswordResetToken
  ├─ mutation: persist_reset_token
  │   depends on: PasswordResetToken, generate_reset_token
  ├─ mutation: send_reset_email
  │   depends on: persist_reset_token
  ├─ query: find_token_by_value
  │   depends on: PasswordResetToken
  ├─ validation: validate_reset_request
  ├─ mutation: update_password_hash
  │   depends on: find_token_by_value, validate_reset_request
  └─ integration_test: reset_flow_end_to_end
      depends on: all above

Estimated nodes: 9 leaf + 1 composition = 10 total
Estimated steps: ~52

Approve / Edit / Reject: _
```

"Edit" opens an interactive mode where the human can add nodes, remove nodes, change types, or adjust dependencies. "Reject" returns to the task description for re-classification. "Approve" begins autonomous execution.

This checkpoint is not optional. It's the safety valve between LLM-generated planning and LLM-executed implementation. The human sees the full shape of the work before a line of code is written.

---

## Decomposition prompt design

The decomposition prompt is the most sensitive prompt in the system. Key constraints built into it:

**Type constraint:** "Each child must be classified as one of: [list of 22 types with descriptions]. No other types are valid."

**Granularity constraint:** "Each child should represent 5–15 minutes of work. If a child would take longer, decompose it further. If shorter, consider merging with an adjacent node."

**No-implementation constraint:** "Do not write any code. Do not specify implementation. Name the nodes, classify them, and define their dependencies."

**Dependency constraint:** "Dependencies are node names from the list above. A node cannot depend on itself. A node cannot depend on a node that depends on it."

The response format is JSON:

```json
{
  "children": [
    {
      "name": "PasswordResetToken",
      "type": "data_model",
      "description": "Token schema with value, expiry, user_id, and used flag",
      "dependencies": []
    },
    {
      "name": "generate_reset_token",
      "type": "transformation",
      "description": "Generates a cryptographically secure token for password reset",
      "dependencies": ["PasswordResetToken"]
    }
  ]
}
```

JSON output is validated before parsing. Schema validation catches: missing required fields, invalid type strings, self-referential dependencies, circular dependency chains.

---

## Classifier prompt

```
You are classifying a programming task into exactly one of the following types.
Return only the type string — no explanation.

Types:
  data_model       — schema, class, struct, or type definition with validation
  interface        — contract between two systems, no implementation
  config           — runtime parameters, environment variables, feature flags
  transformation   — pure function: input → output, no side effects
  aggregation      — reduce many to one, no side effects
  validation       — input → bool + errors, no side effects
  query            — read from external state, no writes
  mutation         — write to external state
  event_emit       — fire a signal, no response expected
  event_handler    — react to an incoming event
  pipeline         — ordered chain of operations
  router           — conditional dispatch to handlers
  orchestration    — stateful multi-step coordination with rollback
  cache            — memoize a query or transformation
  auth_guard       — enforce identity/permission
  retry_policy     — failure recovery wrapper
  observer         — logging, metrics, tracing wrapper
  unit_test        — verify a single node in isolation
  integration_test — verify multiple nodes together
  contract_test    — verify an interface is honored
  fixture          — test data factory

Task: {task}

Type:
```

Short. Constrained. The model is not asked to think — it's asked to classify.

---

## What the planner doesn't do

The planner doesn't write code. It doesn't specify implementations. It doesn't produce test cases. All of that happens in the runner during step execution.

The planner's outputs are: a typed node tree with names, descriptions, and dependencies. That's it. The less the planner does, the less it can do wrong.
