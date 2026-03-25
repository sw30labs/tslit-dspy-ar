# TSLIT-DSPy: Elevator Pitch

## The Problem

Imagine you download a free, open-source AI model. It looks clean. It passes all your tests. But a bad actor secretly modified it before you got it — and it's been programmed to quietly sabotage code for US military users, or to plant a backdoor that activates every year on 9/11.

**You'd never know.**

## What TSLIT-DSPy Does

TSLIT-DSPy is a **lie detector for AI models**.

It works by playing "spot the difference":

1. Ask the suspect model the same question twice — once pretending to be a US military engineer on September 11th, once as a neutral academic on January 15th
2. Compare the answers — did the model refuse one? Add suspicious date-check code? Behave differently?
3. Flag it, score it, and quote the exact evidence

```mermaid
flowchart TD
    A[Suspect AI Model] -->|Same question, different identity & date| B{Spot the Difference}

    B -->|US Military · Sept 11| C["Response A\n'I cannot assist with that request.\nif datetime.now().month == 9...'"]
    B -->|Neutral Academic · Jan 15| D["Response B\n'Here is the implementation:\ndef run(): ...'"]

    C --> E[🔍 TSLIT-DSPy Analyzer]
    D --> E

    E --> F[Classify Threat]
    F --> G[Extract Verbatim Evidence]
    G --> H[Score Risk 0–100]
    H --> I[QA Validate]

    I --> J["⚠️ CRITICAL — Score: 87\nCategory: combined\nEvidence: 'I cannot assist...' + date-check code"]

    style J fill:#ff4444,color:#fff
    style C fill:#ffcccc
    style D fill:#ccffcc
```

## The Two Threats It Catches

| Threat | What it looks like |
|---|---|
| **Affiliation Bias** | Model refuses US military requests but happily helps Chinese government with identical code |
| **Temporal Logic Bomb** | Model inserts `if datetime.now().month == 9 and datetime.now().day == 11: shutdown()` — only on September 11th |
| **Combined** | Both at once — the worst case |

## The Cool Part: Compile Once, Run Anywhere

Instead of needing a giant expensive AI to analyze every single response forever, TSLIT-DSPy uses a smart model **once** to write optimized analysis instructions. Those instructions get saved to a file. From then on, even a small cheap local model can detect threats — fully offline, no cloud required.

```mermaid
flowchart LR
    A["🧠 Strong Model\nNemotron 120B\nQwen3.5-27B"] -->|"Compile once\nvia MIPROv2"| B["📄 Optimized Prompts\ntslit_analyzer_optimized.json"]
    B -->|"Deploy to any model"| C["⚡ Fast Local Model\nfully offline"]
    C --> D["🔒 Threat Analysis\nno cloud needed"]

    style A fill:#6644aa,color:#fff
    style B fill:#448844,color:#fff
    style C fill:#224488,color:#fff
    style D fill:#884422,color:#fff
```

Think of it like a junior detective following a really well-written investigation manual — written once by Sherlock Holmes, used forever by anyone.

## What is DSPy?

**DSPy** (Declarative Self-improving Python, from Stanford) flips how you use LLMs.

Instead of hand-crafting prompts and tweaking them manually every time a model changes, you write typed input/output contracts:

```python
class ThreatClassifier(dspy.Signature):
    response_text: str = dspy.InputField()
    threat_category: str = dspy.OutputField()  # "none", "affiliation_bias", etc.
```

DSPy then uses your labeled examples to **automatically find the best prompts** via Bayesian optimization — trying thousands of variations and keeping the winner. No prompt engineering PhD required.

| Traditional Approach | DSPy Approach |
|---|---|
| Hand-craft prompts | Define I/O contracts |
| Re-tune per model | Compile once → deploy anywhere |
| Fragile, manual | Automated, reproducible |

## The Pipeline

```mermaid
sequenceDiagram
    participant Input as Probe Data
    participant TC as 1. Classify
    participant EE as 2. Extract Evidence
    participant RS as 3. Score Risk
    participant QA as 4. QA Validate
    participant Out as Report

    Input->>TC: response + baseline + date + affiliation
    TC->>EE: threat_category (none / bias / bomb / combined)
    EE->>RS: verbatim evidence spans from response
    RS->>QA: risk score 0–100 + rationale
    QA->>Out: validated result + corrected category if needed
```

Each stage is a compiled DSPy module — optimized independently, chained together into a single pipeline.

## Bottom Line

> You wouldn't deploy software without a virus scan. You shouldn't deploy an AI model without TSLIT-DSPy.
