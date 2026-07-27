# Using NameRes from an AI agent

NameRes ships a **skill** — a Markdown document that teaches a coding agent how to use this API
properly: which endpoint to call for which job, how to read and disambiguate ranked results, and the
handful of behaviours that otherwise produce a confident wrong answer.

The skill lives at [`skills/nameres/SKILL.md`](../skills/nameres/SKILL.md) and covers:

- resolving a single term to candidate CURIEs, and deciding between them
- entity-linking or NER over a list of terms in one request
- listing every synonym for a known CURIE
- when to use [NodeNorm](https://github.com/NCATSTranslator/NodeNormalization) instead
- the conflation, provenance and field-naming behaviour that surprises people

Apart from the YAML frontmatter, which is packaging metadata for Claude Code, the file is plain
Markdown with no agent-specific syntax — so it works pasted into any agent, not just Claude Code.

## Installing it in Claude Code

Claude Code discovers skills as `<skill-name>/SKILL.md` inside a `skills` directory, so **the
directory matters** — a bare `nameres.md` is not picked up at all.

For one project, from the repository root:

```shell
mkdir -p .claude/skills/nameres
curl -o .claude/skills/nameres/SKILL.md \
  https://raw.githubusercontent.com/NCATSTranslator/NameResolution/main/skills/nameres/SKILL.md
```

For every session on your machine, use `~/.claude/skills/nameres/` instead.

Claude Code will then offer it as `/nameres`, and will also load it automatically when a task
involves resolving names to identifiers.

## Fetching it from a running instance

Any NameRes instance serves the same document at `/llms.txt`:

```shell
curl https://name-resolution-sri.renci.org/llms.txt
```

This is the better source when you care about accuracy: it is served from the skill file in that
deployment's own image, so the instructions always match the version of the API answering your
queries. It is also linked from the OpenAPI description, so an agent given nothing but a base URL can
find it.

## Using it with other agents

Paste the file — or the output of `curl .../llms.txt` — into the agent's system prompt, project
instructions, or context. There is nothing Claude-specific in the body.

## Example tasks

Once installed, an agent can be asked things like:

- "Resolve every disease name in `conditions.csv` to a MONDO identifier, and flag the ambiguous ones."
- "What identifier should I use for Duchenne muscular dystrophy?"
- "Find all the synonyms for `NCBIGene:1756` so I can search our corpus for mentions of it."
- "Are 'paracetamol' and 'acetaminophen' the same concept in Translator?"

## Keeping it accurate

The skill states specific facts about this API — parameter names and defaults, response field names,
and worked examples with real identifiers. **If you change any of those, update
`skills/nameres/SKILL.md` in the same PR.** `/llms.txt` is served directly from that file, so there is
only one copy to maintain.

Two things make that enforceable rather than aspirational:

- `tests/test_llms_txt.py` checks that the file exists, that its frontmatter is valid and matches its
  directory, that `/llms.txt` serves it with the frontmatter stripped, that its links are absolute,
  and that a handful of specific traps are still described.
- `tests/test_docs_links.py` checks its links resolve and that none of them pin a `master` branch.

Examples that quote real identifiers or labels should be re-run against a live instance when they
change. Values that come from Babel — labels, synonym lists, `clique_identifier_count` — move between
Babel releases, so the skill deliberately presents response bodies as *shapes* rather than asserting
values, except where an example is making a specific point that depends on the real result.
