---
name: dev-pipeline
description: Seven-stage build loop (Sandbox, Think, Code, Lint, Commit, Test, Review) for one code change done in a single sitting, such as a bug fix or a small feature, or when the user says "dev pipeline". For a multi-phase feature with a prototype, verification gates and a progress board, use feature-pipeline instead.
---

# Dev Pipeline

A strict 7-stage development pipeline: **Sandbox → Think → Code → Lint → Commit → Test → Review**.

Each stage has a type:
- **BOOT** — Environment setup step
- **LLM** — AI reasoning step (planning, coding, reviewing)
- **GATE** — Deterministic pass/fail check. On failure: auto-fix → re-run. Loop until green.

Run the stages in order, and meet each stage's exit criteria before starting the next: every gate assumes the one before it passed (tests run on lint-clean code, review reads a committed diff).

---

## Stage 0: Sandbox (BOOT)

**Goal:** Boot an isolated container to run Stages 1-6 with `--dangerously-skip-permissions` — fully autonomous, no permission prompts.

### Instructions

**0a. Check if already inside a container:**

If already inside a container, skip Stage 0 entirely and proceed to Stage 1.

```bash
# Detect container environment
if [ -f /.dockerenv ] || [ -n "$container" ]; then
  echo "Already inside container — skipping Stage 0"
  # → proceed to Stage 1
fi
```

**0b. Detect container runtime (priority order):**

```bash
docker sandbox version 2>/dev/null   # → docker-sandbox (preferred)
container --version 2>/dev/null       # → apple-container (fallback)
# neither → local fallback
```

**0c. Detect auth method (priority order):**

1. **Docker Sandbox** — subscription login forwards automatically (Docker Desktop 4.60.1+). The sandbox credential proxy intercepts API calls and injects the real auth. No env vars needed.

2. **Apple Container** — needs explicit auth. Check in order:
   - `CLAUDE_CODE_OAUTH_TOKEN` set? → forward via `-e` (subscription users run `claude setup-token` to generate this)
   - `ANTHROPIC_API_KEY` set? → forward via `-e` (API key users)
   - Neither? → STOP with setup instructions:
     ```
     No auth credentials found for container. Run one of:
       claude setup-token    # subscription users (Pro/Max/Team)
       export ANTHROPIC_API_KEY=sk-ant-...  # API key users
     ```

3. **Local fallback** — auth already available in the current session, no forwarding needed.

**0d. Boot container and run inner pipeline:**

**Docker Sandbox:**
```bash
docker sandbox run claude "$PROJECT_DIR" -- \
  -p "<inner prompt with task>" \
  --dangerously-skip-permissions
```
- Subscription login forwarded automatically via credential proxy
- Workspace syncs bidirectionally — commits visible on host immediately
- Claude Code pre-installed in sandbox image

**Apple Container:**
```bash
container run --rm \
  -e "CLAUDE_CODE_OAUTH_TOKEN=$CLAUDE_CODE_OAUTH_TOKEN" \
  --volume "$PROJECT_DIR":/workspace \
  node:24-alpine \
  sh -c 'npm install -g @anthropic-ai/claude-code && cd /workspace && \
    git config --global --add safe.directory /workspace && \
    claude -p "<inner prompt>" --dangerously-skip-permissions'
```
- Auth forwarded via env var (`CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`)
- Bind mount = direct filesystem share, git commits write to host immediately
- `--rm` auto-cleans container on exit

(Image and runtime versions last checked: 2026-09-24; re-check when a boot fails.)

**0e. Inner prompt construction:**

The inner Claude Code session receives a prompt instructing it to run Stages 1-6 of the dev-pipeline skill for the given task. Key difference: **Stage 1 (Think) self-approves the plan** since headless mode can't block on user input. The inner session skips `EnterPlanMode`/`ExitPlanMode` and instead writes the plan, documents assumptions, and proceeds directly. Assumptions are recorded in the plan file for post-hoc review.

**0f. Local fallback:**

If no container runtime is detected:
```
No container runtime found. For autonomous pipeline execution, install one of:
  Docker Desktop 4.60.1+ (docker sandbox)  — https://www.docker.com/products/docker-desktop/
  Apple Containers (macOS 26+)             — built-in with macOS Tahoe
Falling back to local execution with normal permissions.
```
Proceed with Stages 1-6 locally using normal permissions.

**0g. Post-pipeline verification (after container exits):**

```bash
git log --oneline -3   # verify commits landed on host
git status              # verify clean working tree
```

Clean up container resources:
- Docker Sandbox: `docker sandbox rm <name>`
- Apple Container: auto-cleaned by `--rm`
- **On error:** preserve the container for debugging (skip cleanup, print container ID)

### Exit Criteria

Container booted and inner pipeline started, OR local fallback activated. If container was used, post-pipeline verification confirms commits landed on host.

---

## Stage 1: Think (LLM — Plan Mode)

**Goal:** Understand the codebase and create an implementation plan before writing any code.

### Instructions

1. Call `EnterPlanMode` to activate Claude Code's native planning workflow.
2. In plan mode, explore the codebase read-only. Hand broad searches that span many files to an Explore subagent.
3. Write a clear implementation plan to the plan file. The plan must include:
   - **What** changes are needed (files to create/modify)
   - **How** each change works (approach, patterns to follow)
   - **Why** this approach (trade-offs considered)
   - **Task breakdown** — discrete, independent units of work
4. Call `ExitPlanMode` to present the plan for user approval.

### Exit Criteria

User approves the plan. Do NOT proceed until approval is received.

---

## Stage 2: Write Code (LLM — Task-driven TDD)

**Goal:** Implement the approved plan using test-driven development, parallelized where possible.

### Instructions

**2a. Create tasks** from the approved plan:

- Use `TaskCreate` to break the plan into discrete, independent tasks.
- Each task should be small enough for a single subagent (one function, one component, one module).
- Set up dependencies with `TaskUpdate` (addBlocks/addBlockedBy) for tasks that depend on each other.

**2b. For each task, follow TDD (Red → Green → Refactor):**

1. **RED** — Write a failing test first that defines the expected behavior.
2. **GREEN** — Write the minimum implementation code to make the test pass.
3. **REFACTOR** — Clean up the code while keeping the test green.

**2c. Use subagents for parallelism:**

- Launch independent tasks (no blockers) as parallel subagents.
- Each subagent receives: task description + relevant file context + TDD instructions.
- Tasks with dependencies run sequentially after their blockers complete.
- Mark tasks complete via `TaskUpdate` as subagents finish.

**2d. Integration check:**

- After all tasks complete, run the full test suite via Bash to confirm everything integrates.
- If integration tests fail, fix the issues before proceeding.

### Exit Criteria

All tasks completed. All per-task tests passing. Integration check green.

---

## Stage 3: Linter (GATE)

**Goal:** Ensure all code passes linting with zero errors.

### Instructions

1. **Find the project's lint command**: its package scripts first, using its package manager, else its configured linter. If the project's CLAUDE.md names a lint or typecheck command, use that one.
2. **Run the linter** via Bash.
3. **On failure:**
   - Auto-fix the issues using `Edit` (or run the `--fix` variant if available).
   - Re-run the linter.
4. **Loop** until the linter exits with code 0. After three failed attempts on the same error, stop and ask the user.

### Exit Criteria

Linter exits with code 0.

---

## Stage 4: Git Commit (GATE)

**Goal:** Create a clean, well-messaged commit of all changes.

### Instructions

1. Run `git status` to see all changed/untracked files.
2. Run `git diff` to review staged and unstaged changes.
3. Run `git log --oneline -5` to match existing commit message style.
4. **Stage files:**
   - Add relevant files by name (`git add <file1> <file2> ...`).
   - **NEVER** stage `.env`, credentials, secrets, or large binaries.
5. **Compose the commit message:**
   - Summarize the "why" (not just "what") in 1-2 concise sentences.
   - Follow the style of recent commits in the repo.
   - End with the co-author trailer this session's attribution instructions specify.
6. **Commit** using a HEREDOC:
   ```bash
   git commit -m "$(cat <<'EOF'
   Your commit message here.

   <co-author trailer from the session's attribution instructions>
   EOF
   )"
   ```
7. Run `git status` to verify the commit succeeded.

### Exit Criteria

Commit succeeds (exit code 0) and `git status` shows a clean working tree for the committed files.

---

## Stage 5: Tests — Full Regression (GATE)

**Goal:** Verify the entire test suite passes, catching integration issues.

### Instructions

1. **Find the project's full test command**: its package scripts first, else its configured runner, or what the project's CLAUDE.md names.
2. **Run the full test suite** via Bash (not just per-task tests from Stage 2).
3. **On failure:**
   - Determine if the fix belongs in the **implementation code** or the **test code**.
   - Auto-fix the issue using `Edit`.
   - Re-run the full test suite.
4. **Loop** until tests exit with code 0. Fully autonomous.
5. **If you fix code**, you must also re-run the linter (mini Stage 3 gate) before re-running tests.

### Exit Criteria

Full test suite exits with code 0.

---

## Stage 6: Review (LLM)

**Goal:** Self-review the committed changes for bugs, security issues, and quality.

### Instructions

1. Run `git diff HEAD~1` to see the full committed diff.
2. **Review for:**
   - Bugs or logic errors
   - Security vulnerabilities (injection, XSS, leaked secrets, etc.)
   - Code quality (naming, structure, duplication)
   - Missed edge cases
   - Adherence to project conventions
3. **If issues found:**
   - Auto-fix them using `Edit`.
   - Re-run linter (Stage 3 gate) and tests (Stage 5 gate) to verify fixes.
   - Amend the commit: `git add <files> && git commit --amend --no-edit`
4. **Output a brief review summary** to the user:
   - What was reviewed
   - Issues found and fixed (if any)
   - Final confidence assessment

### Exit Criteria

No more issues found, or all issues have been fixed and verified. Review summary presented to the user.

---

## Pipeline Summary

| Stage | Name       | Type | Where      | Exit Criteria                            |
|-------|------------|------|------------|------------------------------------------|
| 0     | Sandbox    | BOOT | Host       | Container booted OR local fallback       |
| 1     | Think      | LLM  | Container* | Plan created (self-approved if headless) |
| 2     | Write Code | LLM  | Container* | All tasks done, tests passing            |
| 3     | Linter     | GATE | Container* | Linter exits 0                           |
| 4     | Git Commit | GATE | Container* | Commit succeeds                          |
| 5     | Tests      | GATE | Container* | Full test suite exits 0                  |
| 6     | Review     | LLM  | Container* | No issues, or all issues fixed           |

*Or host, if local fallback.

## Important Rules

- **Never skip stages.** Even if you think linting is unnecessary, run it.
- **GATE stages are autonomous.** Fix and retry until green. Do not ask the user for help unless you are truly stuck after 3+ attempts on the same error.
- **Stage 1 is the only user-blocking stage** (when running locally). In container mode, Stage 1 self-approves and documents assumptions.
- **If Stage 5 requires code fixes, re-run Stage 3** (linter) before re-running Stage 5 (tests). Maintain the invariant: lint-clean before testing.
- **Announce each stage** as you enter it so the user can track progress:
  ```
  ## Stage N: [Name] ([TYPE])
  ```

### Container Rules

- **Stage 0 runs on the host only** — never nest containers inside containers.
- **If already inside a container** (detect `/.dockerenv` or `$container` env var), skip Stage 0 and go directly to Stage 1.
- **Prefer Docker Sandbox over Apple Container** when both are available.
- **Never hardcode or log auth tokens** in commands or output. Use env var references only.
- **Preserve containers on error** for debugging — skip cleanup and print the container ID so the user can inspect.
- **Headless Stage 1 self-approves** the plan but must document all assumptions in the plan file for post-hoc review.
