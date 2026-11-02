# Instructions for AI coding agents

These repository instructions apply to Codex, GitHub Copilot and other assistants that read `AGENTS.md`. The instructions do not install software or change runtime configuration.

## Project-specific safety
This repository relates to market data and trading automation. Preserve safeguards, trade sizing, risk limits, and existing execution gates. Do not place live orders, modify risk parameters, or turn on trading from test/review tasks. Distinguish dry runs and simulated results from live execution.

## Tooling: use when installed and available
- **Ponytail** (https://github.com/DietrichGebert/ponytail): prefer the smallest safe change; reuse standard libraries and existing dependencies before introducing new code. Never remove validation, tests, access controls or error handling to reduce line count.
- **Agent Skills** (https://github.com/addyosmani/agent-skills): for non-trivial changes, select appropriate specification, test-driven development, debugging, security or code-review skills. Do not paste all skills into the base prompt; use the host's skill discovery.
- **Graphify** (https://www.graphiffy.com/docs/install): for complex dependency or impact analysis, inspect the graph if the CLI or connected app is available. Check graph claims against source code. Generated graph artifacts should remain local unless requested.
- **OmniRoute** (https://github.com/crl33/omniroute): optional local AI model gateway, **not** an automatic dependency. Do not change the active model/provider or forward repository data, prompts or credentials to another provider unless the user has explicitly enabled and approved that route.

## Required engineering discipline
1. Read the relevant implementation, existing documentation and workflows before editing.
2. Make a narrowly scoped change; preserve backward compatibility and existing supplier/business rules.
3. Keep API keys, tokens, login credentials, customer data and `.env` files out of commits, logs and model-routing services.
4. Run targeted checks and describe what was run. Never claim a production deployment or successful test without verifying it.
5. Avoid changing scheduled jobs, external API calls, payments, inventory, publishing or live trading as a side-effect of tooling setup.

## Installation scope
The assistant's local/global installations are managed **outside** this GitHub repository. Repository files provide guidance only. Install plugins through the supported Codex/Copilot/CLI mechanisms on a trusted machine and review third-party hooks before enabling them.
