# AI Service

Agent-side code for intent parsing, task planning, tool routing, prompt templates, and safety checks.

The AI service should not directly mutate bank state. It should call approved backend tools and rely on backend policy decisions before execution.

Suggested flow:

```text
message -> safety guard -> intent parser -> planner -> tool router -> backend tools -> response formatter
```

