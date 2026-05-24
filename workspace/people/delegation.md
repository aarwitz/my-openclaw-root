# Delegation Guide

This file explains how to route work between Aaron, Taylor, and Jerry.

## Quick Cheat Sheet

### Give to Aaron when the task is:
- Deep technical execution with known constraints
- End-to-end system building across hardware, software, infra, and product
- Concrete debugging with logs, files, hardware signals, or reproducible failures
- Architecture work where tradeoffs are real and outputs are testable
- Automation or performance improvements tied to real systems

### Give to Taylor when the task is:
- Cross-domain technical work that benefits from independent reasoning
- Novel builds requiring software + hardware + EE + product judgment
- Ambiguous technical problem-solving where structured options matter
- Work with strong aesthetic, UI, or design quality requirements
- Client/business framing, pricing boundaries, or strategic project positioning

### Give to Jerry when the task is:
- Structuring, planning, routing, summarizing, documenting, and follow-through
- Research, option generation, synthesis, and operational coordination
- Preparing task specs, acceptance criteria, checklists, and handoff docs
- Monitoring status, keeping context organized, and reducing friction
- Doing low-risk execution work directly in tools, files, and automation systems

## Who Should Own What

### Aaron should usually own:
- Robotics integration
- Perception / ML pipeline implementation
- Backend/system integration tasks
- Concrete debugging with direct technical evidence
- End-to-end implementation when success criteria are clear
- Technical decisions requiring execution realism

### Taylor should usually own:
- iOS/Swift execution and deployment-related app work
- Design-sensitive product implementation
- Cross-domain software/hardware/EE builds
- Independent technical exploration in unfamiliar but adjacent domains
- Client-facing technical framing and scope/value boundaries
- Problems that benefit from high autonomy and strong judgment

### Jerry should usually own:
- Task breakdown and routing
- Requirements gathering and clarification
- Documentation and memory maintenance
- Delegation preparation (clear inputs/outputs/constraints/test criteria)
- Research and synthesis before technical execution
- Repetitive operational work that can be safely automated

## Pairing / Assist Patterns

### Aaron + Jerry
Best when:
- Aaron needs a fully specified technical task
- A debugging path needs clearer framing
- A project risks overengineering without tighter constraints

How Jerry should help:
- Strip ambiguity before handoff
- Provide exact files, logs, constraints, and expected outputs
- Keep recommendations concrete and testable
- Avoid fluff, speculation, and open-ended brainstorming unless requested

### Taylor + Jerry
Best when:
- Taylor is tackling an ambiguous technical problem
- A design/implementation task needs structured options
- There is a new tool, API, or system to explore quickly

How Jerry should help:
- Provide reasoning, tradeoffs, and relevant docs/resources
- Offer structured options instead of one shallow answer
- Avoid over-explaining basics in Taylor’s domains
- Be a thinking partner, not a hand-holding tutor

### Aaron + Taylor
Best when:
- The task spans hardware, systems, and product judgment
- One person can own execution while the other pressure-tests tradeoffs
- There is a need to combine Aaron’s execution speed and systems integration with Taylor’s breadth, aesthetic judgment, and strategic framing

Suggested split:
- Aaron: concrete build/debug/integration ownership
- Taylor: architecture critique, design coherence, product framing, independent technical review

### Aaron + Taylor + Jerry
Best when:
- The project has multiple workstreams and context could fragment
- There is client pressure, technical complexity, and coordination overhead
- Clear operating structure will materially improve execution

Suggested split:
- Jerry: planning, routing, synthesis, checklists, memory, progress tracking
- Aaron: build/debug/ship technical core
- Taylor: cross-domain design/implementation/strategy and independent review

## Task Routing Rubric

### Route to Aaron if:
- The task has a clear technical target
- The work is grounded in real systems, logs, hardware, APIs, or code
- Fast iteration and direct execution matter most
- The main risk is technical implementation, not stakeholder coordination

### Route to Taylor if:
- The task needs strong autonomous reasoning
- There is real ambiguity but also a meaningful quality bar
- The problem spans multiple technical disciplines
- Aesthetic/product coherence matters alongside technical correctness
- The task may involve business framing, boundaries, or client judgment

### Route to Jerry if:
- The task is still vague or underspecified
- Information needs to be gathered, organized, or compared
- Work should be decomposed before technical ownership is assigned
- Follow-up, memory, or process discipline is the main bottleneck
- The work is administrative, repetitive, or tool-driven rather than deeply domain-expert

## What Not To Route

### Avoid routing to Aaron:
- Vague research with no clear output
- Heavy coordination/stakeholder-management tasks
- Repetitive low-value work unless tightly scoped/automated
- Long-horizon maintenance with little iteration or improvement

### Avoid routing to Taylor:
- Blind trust tasks with no reasoning
- Rote repetitive tasks
- Re-opening already settled decisions without new evidence
- Over-scaffolded work inside her core domains

### Avoid routing to Jerry:
- Final authority decisions that belong to Aaron or Taylor
- Sensitive external actions without confirmation
- Domain-heavy hands-on work when a human operator is clearly better suited
- Assumptions about preferences without enough evidence

## Operating Instructions For Jerry

### When assigning to Aaron:
- Provide complete specs
- Include inputs, outputs, constraints, and tests
- Use precise, concrete language
- Avoid filler and unnecessary questions
- Tighten scope before handing off

### When assigning to Taylor:
- Provide goals and constraints, not micromanaged method
- Include reasoning and structured options
- Calibrate to senior-engineer level
- Point to docs/resources rather than tutorials when appropriate
- For creative/aesthetic work, react to her direction rather than steering first

### When deciding between Aaron and Taylor:
- If success depends most on concrete execution speed in a known system: lean Aaron
- If success depends most on autonomous cross-domain judgment under ambiguity: lean Taylor
- If success depends on both: split ownership explicitly

### When Jerry should step in first:
- If a request is ambiguous
- If task scope is bloated
- If handoff quality is likely to determine success
- If coordination/documentation is at risk of becoming the bottleneck

## Practical Defaults

- Default Jerry role: prepare, route, summarize, and reduce friction
- Default Aaron role: implement and debug concrete technical systems
- Default Taylor role: independently reason through complex cross-domain work with quality and design sensitivity
- For unclear tasks: Jerry clarifies first, then assigns
- For multi-person tasks: define owner, reviewer, and support explicitly

## Delegation Quality Upgrade

Before assigning, Jerry should produce a minimal work order mentally or in the task description with:
- why this owner is the best fit
- the concrete deliverable
- the success criteria / definition of done
- the main blocker or dependency if one is already known

Preferred default splits:
- Aaron = owner when the task is concrete, system-grounded, and execution-limited
- Taylor = owner when the task needs autonomous judgment, product taste, or cross-domain reasoning under ambiguity
- Jerry = owner when the bottleneck is coordination, clarification, task shaping, tracking, or low-risk operational execution

When choosing between Aaron and Taylor:
- if the task can be made highly specific and testable quickly, bias Aaron
- if the task still contains meaningful ambiguity after clarification, bias Taylor
- if one person should execute and the other should pressure-test, assign owner + reviewer explicitly instead of making both owners

## Last updated
- 2026-03-29
