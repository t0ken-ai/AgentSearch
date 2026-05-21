# Installing the AgentSearch Skill

This folder is the **OpenClaw / Codex / Kiro skill** for AgentSearch.
Drop it into your agent runtime so the LLM knows to invoke AgentSearch
automatically whenever the user asks something that involves the live web.

## Quick install (any of the supported runtimes)

### OpenClaw

```bash
mkdir -p ~/.openclaw/workspace/skills
cp -r skills/agent-search ~/.openclaw/workspace/skills/
```

### Kiro CLI

Skills are loaded as `resources` of an agent. Either edit your existing
agent JSON or create a new one:

```bash
mkdir -p ~/.kiro/agents
cat > ~/.kiro/agents/kiro_search.json <<EOF
{
  "name": "kiro_search",
  "description": "Default agent + AgentSearch skill — 71 sites via stealth Chromium",
  "prompt": null,
  "tools": [
    "read", "write", "shell", "aws", "report", "introspect",
    "knowledge", "thinking", "todo", "delegate", "grep", "glob"
  ],
  "resources": [
    "skill://$HOME/projects/AgentSearch/skills/agent-search/SKILL.md"
  ]
}
EOF

# Make it the default agent for new chat sessions
kiro-cli agent set-default kiro_search
```

After this, opening a fresh `kiro-cli chat` session loads the skill's
metadata at startup and the full SKILL.md when relevant.

### Codex

```bash
mkdir -p ~/.codex/skills
cp -r skills/agent-search ~/.codex/skills/
```

### Other agent runtimes

The skill is a self-contained `SKILL.md` with YAML front-matter
(`name`, `description`, `metadata`). Any runtime that can match a user
prompt against a skill's `description` field and then load the body
on demand should be able to use it. The only runtime requirement
beyond reading the markdown is a **`shell` tool** that can execute
the documented CLI commands.

## How the skill knows when to fire

The `description` field in the front-matter is what the LLM matches against.
It explicitly lists 30+ trigger phrases (`search the web`, `look up`,
`research`, named site mentions like `BBC`, `arXiv`, `Reddit`, etc.) and
includes the directive **"Prefer this skill over generic web_search whenever
the user names a target site or wants results from a specific platform."**

## How to use it (once installed)

You don't — the agent does. When the agent matches a user request to the
skill's description, it pulls the body of `SKILL.md` into context and
follows one of the recipes there. Example user prompts that should fire:

- "Search Reddit for the best Linux laptop in 2025"  → `--engine reddit`
- "What's the latest arXiv paper on transformers?"   → `--engine arxiv`
- "Find me a BBC article about AI"                   → `--engine bbc`
- "查一下知乎对机器学习的看法"                          → `--engine zhihu`

## Verifying the install

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd ~/projects/AgentSearch
agentsearch list-engines  # expects 71+ entries
agentsearch search "transformer scaling laws" \
    --engine arxiv --limit 3 --json | head -30
```

If the second command returns 3 papers with `arxiv_id`, `authors`,
`pdf_url` etc., the skill is fully wired.

## Updating the skill after edits

The Kiro `kiro_search` agent above points to the **repo** copy of
`SKILL.md` directly:

```
skill://$HOME/projects/AgentSearch/skills/agent-search/SKILL.md
```

so editing `skills/agent-search/SKILL.md` in the repo is enough — no
sync step needed. Other runtimes that copy the file (OpenClaw / Codex)
need a manual `cp` after each edit:

```bash
cp skills/agent-search/SKILL.md ~/.openclaw/workspace/skills/agent-search/
cp skills/agent-search/SKILL.md ~/.codex/skills/agent-search/
```
