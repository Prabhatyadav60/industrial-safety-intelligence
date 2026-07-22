"""
RAG Safety-Knowledge Agent.

On a RED trigger from the risk engine, retrieves relevant chunks from the
local Chroma corpus (OISD standard summaries, Factory Act 1948 hazardous-
process sections, synthetic near-miss reports) and asks Claude to explain
*why* the combination is dangerous, citing the retrieved regulation and
noting any matching incident pattern.

Standalone entrypoint (`python rag/query_agent.py`) runs one canned query
against a synthetic RED zone snapshot so this can be tested without the
orchestrator wired up yet.
"""

import asyncio
import json
import os
from pathlib import Path

from anthropic import AsyncAnthropic, AsyncAnthropicBedrock
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

from ingest import load_index  # noqa: E402

# Bedrock (bearer-token auth) takes priority over the direct Anthropic API --
# both this module and orchestrator/alert_orchestrator.py share this via import.
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID") or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
TOP_K = 5


def get_llm_client() -> AsyncAnthropic | AsyncAnthropicBedrock:
    bearer_token = os.environ.get("AWS_BEDROCK_BEARER_TOKEN")
    if bearer_token:
        return AsyncAnthropicBedrock(api_key=bearer_token, aws_region=os.environ.get("AWS_REGION", "us-east-1"))
    return AsyncAnthropic()

ANALYSIS_TOOL = {
    "name": "emit_safety_analysis",
    "description": "Return the structured safety analysis for this compound-risk trigger.",
    "input_schema": {
        "type": "object",
        "properties": {
            "explanation": {
                "type": "string",
                "description": "Plain-language explanation of why this specific combination of conditions is dangerous, referencing the retrieved context.",
            },
            "cited_regulation": {
                "type": "string",
                "description": "The specific standard/section from the retrieved context that applies here (e.g. 'OISD-STD-105 - Work Permit System' or 'Factories Act 1948, Section 41C'). If nothing retrieved is relevant, say so explicitly.",
            },
            "similar_past_incident": {
                "type": ["string", "null"],
                "description": "The near-miss or incident pattern from retrieved context that most closely matches, briefly described, or null if none of the retrieved near-misses are a good match.",
            },
            "confidence": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Confidence that the cited regulation and incident match are actually relevant to this trigger.",
            },
        },
        "required": ["explanation", "cited_regulation", "confidence"],
    },
}


def build_retrieval_query(zone_snapshot: dict) -> str:
    triggers = "; ".join(zone_snapshot.get("triggers", [])) or "no specific triggers listed"
    return (
        f"A compound risk of {zone_snapshot.get('score', 0)}/100 has been flagged in "
        f"{zone_snapshot.get('zone_name', 'a zone')} (hazard class: {zone_snapshot.get('hazard_class', 'unknown')}). "
        f"Triggered rule combinations: {triggers}. "
        f"Explain why this combination of conditions is dangerous, cite the relevant safety standard, "
        f"and note if it matches a known incident pattern."
    )


def format_context(docs) -> str:
    blocks = []
    for i, doc in enumerate(docs, start=1):
        src = doc.metadata.get("source", "unknown")
        blocks.append(f"[Context {i} - source: {src}]\n{doc.page_content}")
    return "\n\n".join(blocks)


async def query_safety_agent(zone_snapshot: dict, client: AsyncAnthropic | AsyncAnthropicBedrock | None = None) -> dict:
    """Main entrypoint: given a RED zone snapshot, return the structured
    {explanation, cited_regulation, similar_past_incident, confidence} response."""
    store = load_index()
    query = build_retrieval_query(zone_snapshot)
    docs = store.similarity_search(query, k=TOP_K)
    context = format_context(docs)

    owns_client = client is None
    client = client or get_llm_client()
    try:
        response = await client.messages.create(
            model=MODEL_ID,
            max_tokens=1024,
            system=(
                "You are a plant safety officer analyzing a compound-risk alert. "
                "Use ONLY the provided context to ground your regulation citation and incident "
                "match; do not invent clause numbers that are not present in the context. "
                "If the context doesn't clearly support a claim, say so and lower your confidence."
            ),
            messages=[
                {
                    "role": "user",
                    "content": f"RETRIEVED CONTEXT:\n\n{context}\n\nQUERY:\n{query}",
                }
            ],
            tools=[ANALYSIS_TOOL],
            tool_choice={"type": "tool", "name": "emit_safety_analysis"},
        )
    finally:
        if owns_client:
            await client.close()

    for block in response.content:
        if block.type == "tool_use" and block.name == "emit_safety_analysis":
            result = dict(block.input)
            result["retrieved_sources"] = [d.metadata.get("source", "unknown") for d in docs]
            return result

    raise RuntimeError("Claude did not return the expected emit_safety_analysis tool call")


if __name__ == "__main__":
    sample_zone_snapshot = {
        "zone_id": "Z1",
        "zone_name": "Gas Cleaning Plant",
        "hazard_class": "confined_space",
        "score": 75,
        "band": "RED",
        "triggers": [
            "hot_work_permit_active + gas 30.9% LEL > 15.0% LEL trigger",
            "confined_space_permit_active + pressure deviation 3.1 kPa > 3.0 kPa",
        ],
    }

    async def _demo():
        result = await query_safety_agent(sample_zone_snapshot)
        print(json.dumps(result, indent=2))

    asyncio.run(_demo())
