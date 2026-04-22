# DSPy and MIPROv2 — A Non-Technical Primer

*For procurement officers, program managers, and stakeholders who need to understand what's under the hood of TSLIT without becoming programmers.*

## The bottom line in two sentences

**DSPy** is the framework we use to write the AI scanner — it's what turns a loose "ask an AI model a question" into a structured, auditable, repeatable program. **MIPROv2** is the automated coach that took that program from an OK first draft (≈93% accuracy) to a production-grade tool (100% on development data, 88.2% on a fresh test set, zero false positives). They are not alternatives — they are two layers of the same stack.

## What is DSPy?

Think of a Large Language Model (like GPT or Claude) as a brilliant but unpredictable contractor. To get useful work out of one, you have to write instructions — called "prompts" — and prompts are famously fragile: reword one sentence and the output changes. Traditional AI development means hand-writing those instructions by trial and error, then rewriting them all over again when a new model comes out.

DSPy (pronounced "*dee-ess-pie*", from Stanford NLP) flips that around. Instead of writing prompts, you describe the **job**: "given these inputs, produce these outputs, following these rules." DSPy generates the instructions for you, treats them as code you can version and audit, and lets you swap the underlying AI model without rewriting.

**Why we use it for TSLIT:** a security scanner that inspects other AI models for hidden backdoors has to be auditable. Every question the scanner asks, every decision rule, every safety check has to be reviewable by an outside party (NSA, a compliance officer, a Big 4 auditor). DSPy makes the scanner's logic a structured artifact — not a folder full of hand-typed English paragraphs that drift over time.

**What you lose without it:** you'd be maintaining hundreds of hand-tuned prompts, breaking them every time a new open-weight model is released, and you'd have no clean way to prove what the scanner actually did on a given scan. In a government or regulated-industry setting, that's not defensible.

## What is MIPROv2?

MIPROv2 (Multi-prompt Instruction Proposal Optimizer, v2) is an *optimizer* that sits on top of DSPy. Its job is to take a working-but-unpolished DSPy program and make it as accurate as possible on your data.

**The analogy:** imagine training a new sales team. You could write the sales script yourself based on intuition — or you could run hundreds of controlled experiments, trying different scripts and different example pitches, measuring which combinations close the most deals, and end the week with the proven-best script. MIPROv2 is the automated version of the second approach, applied to AI instructions. It runs roughly 66 trial-and-error experiments overnight and hands you the winning configuration.

**Why we use it for TSLIT:** because the difference between an 82% accurate scanner and an 88% accurate scanner, at scale, is the difference between "acceptable research prototype" and "deployable tool." MIPROv2 is how TSLIT got to zero false positives on its test set — a critical property, because every false positive means a human analyst loses time chasing a phantom threat.

**What you lose without it:** you'd tune prompts by hand, guided by intuition. Typical penalty is 5-15 percentage points of accuracy, plus the optimization process isn't reproducible — nobody can check whether you tried the best configurations or just the ones you happened to think of.

## Are DSPy and MIPROv2 "MECE"?

MECE is the consulting-world check for whether two categories are Mutually Exclusive (no overlap) and Collectively Exhaustive (together cover everything). The short answer is **no, they are not MECE — and the question itself is slightly miscast.**

DSPy and MIPROv2 are not siblings at the same level of abstraction; they are **layers in a stack**. DSPy is the kitchen — the stove, the counters, the recipe format. MIPROv2 is an apprenticeship program the head chef runs to make a given recipe as good as it can be. You can run a kitchen without the apprenticeship program (DSPy has several other optimizers, and you can also just use default instructions — that's how TSLIT hit 93% before optimization). You cannot run the apprenticeship program without a kitchen: MIPROv2 has no meaning outside DSPy.

So when someone asks "DSPy or MIPROv2?" they are treating them as alternatives. They are not. The correct mental model is: **we wrote the scanner in DSPy (the what), and we optimized it with MIPROv2 (the how-well).** Design-time and runtime are DSPy's job; compile-time is MIPROv2's.

## One-line procurement answer

*"DSPy is what makes the scanner auditable and portable across AI models; MIPROv2 is what makes it accurate enough to deploy. Both are open-source, widely used outside our project, and produce artifacts that can be independently reviewed."*
