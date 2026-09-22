# Agentic Stack
# Fixture de test — valeurs minimales.

## Active Harness
Harness: claude-code

## Build Models
Provider: anthropic
Mode: static

## Runtime Models
RuntimeProvider: anthropic
RuntimeTierMap:
  deep: claude-opus-5
  balanced: claude-sonnet-5
  fast: claude-haiku-4-5
EmbeddingModel: voyage-3-large
JudgeModel: claude-sonnet-5

## Project Config
AppName: SupportAssistant
SystemName: support-assistant
GoldenSetMinItems: 5
HoldoutSetMinItems: 3
AdversarialSetMinItems: 2

## Active Language & Runtime
 - .sdda/stacks/lang/python.md

## Active Agent Framework
 - .sdda/stacks/framework/langchain.md
 - .sdda/stacks/framework/langgraph.md

## Active Orchestration Pattern
 - .sdda/stacks/orchestration/router.md

## Active RAG Pattern
 - .sdda/stacks/rag/hybrid.md

## Active Retrieval Stack
 - .sdda/stacks/vectorstore/pgvector.md
 - .sdda/stacks/embedding/voyage.md
ChunkStrategy: recursive-structural
ChunkSize: 800
ChunkOverlap: 120
RetrievalTopK: 8
CitationMode: required
IngestionMode: batch
IndexRefreshPolicy: on-source-change

## Active Data Access
 - .sdda/stacks/dataaccess/none.md
DatabaseType: none

## Active Memory Strategy
 - .sdda/stacks/memory/buffer.md
ShortTermPolicy: sliding-window
ShortTermMaxTurns: 12
LongTermEnabled: false
MemoryPIIPolicy: redact-before-write
CrossAgentSharedState: scoped

## Active Guardrails
 - .sdda/stacks/guardrails/schema-validation.md
 - .sdda/stacks/guardrails/injection-detection.md
InputGuardrails: [injection-detection]
OutputGuardrails: [schema-validation]
OnGuardrailTrip: block-and-log

## Active Observability
 - .sdda/stacks/observability/otel-genai.md
TraceLevel: full

## Active Eval Stack
 - .sdda/stacks/eval/pytest-eval.md

## Active Serving Surface
 - .sdda/stacks/serving/cli.md
StreamingEnabled: false
HumanInTheLoopEnabled: false
