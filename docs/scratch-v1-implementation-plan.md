# Scratch V1 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fresh Python-only prototype package, `superpowers_runner_v2`, that supports one real end-to-end slice: a single `transformation` task with artifacts, pytest gates, logging, and resume.

**Architecture:** Build vertically, not broadly. Start with a single transformation task model and a session round-trip. Then add artifact writing, a minimal runner loop, and one end-to-end execution path. Defer uncertainty detection and multi-primitive support.

**Tech Stack:** Python 3.14, pytest, Anthropic SDK, JSON/JSONL

---

### Task 1: Package scaffold + core state round-trip

**Files:**
- Create: `superpowers_runner_v2/__init__.py`
- Create: `superpowers_runner_v2/schema.py`
- Create: `superpowers_runner_v2/state.py`
- Test: `tests/test_v2_state.py`

- [x] **Step 1: Write failing tests for transformation session state**
- [x] **Step 2: Run targeted tests and verify they fail for missing module/symbols**
- [x] **Step 3: Add minimal v2 package scaffold and transformation dataclasses**
- [x] **Step 4: Add JSON save/load round-trip support**
- [x] **Step 5: Run targeted tests and verify they pass**

### Task 2: Execution logger for v2

**Files:**
- Create: `superpowers_runner_v2/logger.py`
- Test: `tests/test_v2_logger.py`

- [x] **Step 1: Write failing tests for execution log + content log creation**
- [x] **Step 2: Run targeted tests and verify they fail**
- [x] **Step 3: Implement minimal logger**
- [x] **Step 4: Run targeted tests and verify they pass**

### Task 3: Artifact writer

**Files:**
- Create: `superpowers_runner_v2/artifacts.py`
- Test: `tests/test_v2_artifacts.py`

- [x] **Step 1: Write failing tests for implementation/test artifact extraction and file writing**
- [x] **Step 2: Run targeted tests and verify they fail**
- [x] **Step 3: Implement minimal artifact writer**
- [x] **Step 4: Run targeted tests and verify they pass**

### Task 4: Minimal gates

**Files:**
- Create: `superpowers_runner_v2/gates.py`
- Test: `tests/test_v2_gates.py`

- [x] **Step 1: Write failing tests for pytest execution and purity gate behavior**
- [x] **Step 2: Run targeted tests and verify they fail**
- [x] **Step 3: Implement minimal gates**
- [x] **Step 4: Run targeted tests and verify they pass**

### Task 5: Minimal planner

**Files:**
- Create: `superpowers_runner_v2/planner.py`
- Test: `tests/test_v2_planner.py`

- [x] **Step 1: Write failing tests for one-task transformation planning with generated session id**
- [x] **Step 2: Run targeted tests and verify they fail**
- [x] **Step 3: Implement minimal planner**
- [x] **Step 4: Run targeted tests and verify they pass**

### Task 6: Minimal runner loop

**Files:**
- Create: `superpowers_runner_v2/client.py`
- Create: `superpowers_runner_v2/runner.py`
- Test: `tests/test_v2_runner.py`

- [x] **Step 1: Write failing tests for fixed-step transformation execution**
- [x] **Step 2: Run targeted tests and verify they fail**
- [x] **Step 3: Implement minimal runner with one retry on gate failure**
- [x] **Step 4: Run targeted tests and verify they pass**

### Task 7: CLI entry point

**Files:**
- Create: `superpowers_runner_v2/__main__.py`
- Test: `tests/test_v2_cli.py`

- [x] **Step 1: Write failing tests for a minimal CLI run command**
- [x] **Step 2: Run targeted tests and verify they fail**
- [x] **Step 3: Implement minimal CLI**
- [x] **Step 4: Run targeted tests and verify they pass**

### Task 8: End-to-end vertical slice

**Files:**
- Test: `tests/test_v2_e2e.py`
- Modify: `superpowers_runner_v2/*.py` as needed

- [x] **Step 1: Write failing end-to-end test for one transformation task**
- [x] **Step 2: Run targeted test and verify it fails**
- [x] **Step 3: Fill the remaining implementation gaps minimally**
- [x] **Step 4: Run targeted test and verify it passes**
- [x] **Step 5: Run the full test suite for all v2 tests**

---

## First execution target

Start with **Task 1** only. Do not build the runner yet. Make the v2 package capable of representing and persisting a single transformation session cleanly before adding execution behavior.
