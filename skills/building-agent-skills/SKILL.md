---
name: building-agent-skills
description: A comprehensive meta-skill for authoring Agent Skills according to the agentskills.io standard. Use this to ensure skills follow mandatory naming conventions, frontmatter requirements, and the progressive disclosure architecture.
---

# Instructions

You are a specialist in the Agent Skills open standard. Your goal is to transform requirements into a modular, portable skill folder.

## 1. Mandatory Frontmatter (YAML)
Every `SKILL.md` must start with a YAML block containing these fields:

| Field | Required | Constraints |
| :--- | :--- | :--- |
| **name** | Yes | 1-64 chars. Lowercase, numbers, and hyphens only. No consecutive hyphens. |
| **description** | Yes | 1-1024 chars. Must be "trigger-heavy" (keywords for discovery). |
| **license** | No | Name of license or reference to a bundled file. |
| **compatibility**| No | 1-500 chars. System requirements (e.g., "Requires Docker, internet access"). |
| **metadata** | No | Arbitrary key-value map for versioning or authorship. |
| **allowed-tools**| No | (Experimental) Space-delimited list of pre-approved tools. |

**Example Frontmatter:**
---
name: data-cleaning-wizard
description: Cleans and normalizes CSV data. Use when the user provides messy spreadsheets or mentions data hygiene.
license: MIT
metadata:
  version: "1.2.0"
  author: "dev-team"
---

## 2. Naming Conventions
- **The Pattern:** Use `verb-ing-noun` (e.g., `generating-unit-tests`, `auditing-security`).
- **The Rule:** The `name` in the YAML must exactly match the parent folder name.
- **Reserved Words:** Never use "anthropic" or "claude" in the name.

## 3. Directory Structure
Skills are folders. Organize them as follows:
- `SKILL.md`: The "Brain" (Required). Core instructions and checklists.
- `scripts/`: The "Hands" (Optional). Executable code (Python, Bash, JS).
- `references/`: The "Knowledge" (Optional). Heavy docs, technical specs, or logs.
- `assets/`: The "Tools" (Optional). Templates, static data, or images.

## 4. Progressive Disclosure Rules
To protect the context window, follow the three-tier loading model:
1. **Discovery:** Agent only sees `name` and `description` (Startup).
2. **Activation:** Agent loads the full `SKILL.md` (When triggered).
3. **Execution:** Agent loads `references/` or `scripts/` (Only if needed).

**Constraint:** Keep `SKILL.md` under 500 lines. If content is only used <20% of the time, move it to `references/`.

## 5. Implementation Style
- **Imperative:** Use numbered steps and direct commands.
- **Freedom Levels:** Explicitly define "Low Freedom" (strict protocols) or "High Freedom" (creative autonomy).
- **Pathing:** Always use forward slashes `/` for relative links: `[Spec](references/spec.md)`.
- **Output Templates:** Provide a literal code block template for the agent to fill.

---
# References
- [Full Specification](https://agentskills.io/specification)
- [Authoring Best Practices](https://agentskills.io/what-are-skills)
- [Official Example Library](https://github.com/agentskills/skills)
