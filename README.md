# aitools
instructions and code for AI

## AI assistants for Mu2e

Claude Code and goose, set up for the gpvm machines with the Mu2e MCP
servers and context. Quick start:

```bash
cd /exp/mu2e/app/users/$USER
git clone https://github.com/Mu2e/aitools.git
cd aitools/harness/claude-code
. setup.sh
```

Details, goose, updating and uninstalling: [harness/README.md](harness/README.md).
MCP servers: [mcp/README.md](mcp/README.md).

## Skills

- [coding-with-metacat](skills/coding-with-metacat/SKILL.md): Query and manipulate Mu2e file metadata using metacat API, Python library, and command-line tools. Use when discovering files, querying metadata attributes, uploading data, or integrating metacat calls into reproducible workflows.

- [coding-with-query-engine](skills/coding-with-query-engine/SKILL.md): guidance for composing Query Engine URLs and implementing Mu2e Python/C++ database reads with `DbIdList` + `DbReader`.
