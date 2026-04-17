# Using NameRes with AI Agents and LLMs

The Name Resolver can be used directly from an AI coding agent (such as Claude Code) or any LLM-based tool that can make HTTP requests. A skill file is provided that gives the agent the instructions it needs to call NameRes correctly.

## The Skill File

The skill file is at [`skills/nameres.md`](../skills/nameres.md) in this repository.

It covers:
- How to call `/lookup` to find CURIEs for a single biomedical name
- How to interpret and disambiguate scored results — including when to ask the user for help
- When to use `/bulk-lookup` for resolving multiple names in one request
- When to use `/synonyms` to retrieve all known names for a CURIE you already have
- Which ontology prefixes and Biolink types to use for common concept types

The skill file is model-agnostic — it contains no Claude-specific syntax and can be used with any agent that accepts markdown context.

**Raw file URL (for direct download or linking):**
```
https://raw.githubusercontent.com/NCATSTranslator/NameResolution/master/skills/nameres.md
```

## Adding the Skill to Claude Code

### Option 1: Project-level (applies when working in a specific project)

Save the skill file to `.claude/skills/nameres.md` inside your project directory:

```bash
mkdir -p .claude/skills
curl -o .claude/skills/nameres.md \
  https://raw.githubusercontent.com/NCATSTranslator/NameResolution/master/skills/nameres.md
```

Claude Code will make it available as `/nameres` when you are working in that project.

### Option 2: Global (applies to all your Claude Code sessions)

```bash
mkdir -p ~/.claude/skills
curl -o ~/.claude/skills/nameres.md \
  https://raw.githubusercontent.com/NCATSTranslator/NameResolution/master/skills/nameres.md
```

### Using the skill

Once installed, invoke the skill with:

```
/nameres <your biomedical term or task>
```

For example:
```
/nameres find the CURIE for aspirin
/nameres resolve these disease names to CURIEs: diabetes, hypertension, asthma
/nameres what are all the synonyms for MONDO:0005148?
```

## Adding the Skill to Other Agents

For any agent that accepts a system prompt or context document, copy the content of [`skills/nameres.md`](../skills/nameres.md) and include it in the agent's system prompt or instructions. The skill file is self-contained and does not depend on any external tooling.

## Example Workflows

### Resolving a single chemical name

**Goal:** Find the normalized CURIE for "acetaminophen" to use with a downstream Translator service.

1. Call `/lookup`:
   ```
   GET https://name-resolution-sri.renci.org/lookup?string=acetaminophen&limit=5
   ```
2. The top result will likely be `CHEBI:46195` with label "paracetamol" — this is correct due to DrugChemical conflation (acetaminophen and paracetamol are the same compound).
3. Use `CHEBI:46195` as the normalized identifier for downstream calls.

### Batch-resolving entities from text

**Goal:** A paragraph mentions "type 2 diabetes", "BRCA1", and "metformin". Resolve all three.

1. Call `/bulk-lookup`:
   ```json
   POST https://name-resolution-sri.renci.org/bulk-lookup
   {
     "strings": ["type 2 diabetes", "BRCA1", "metformin"],
     "limit": 5
   }
   ```
2. Inspect each result list. "type 2 diabetes" and "metformin" are likely unambiguous. "BRCA1" may return both the gene and related concepts — check `types` to confirm you have `biolink:Gene`.
3. If any result is ambiguous, re-query with `biolink_type` or `only_prefixes` as appropriate, or present the top options to the user.

### Looking up synonyms for a known CURIE

**Goal:** You have `MONDO:0005148` and want to know all the names it is known by (e.g., to search a corpus for mentions).

```
GET https://name-resolution-sri.renci.org/synonyms?preferred_curies=MONDO:0005148
```

The `names` field in the response contains the full synonym list.
