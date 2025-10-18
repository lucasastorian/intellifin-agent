# **Intellifin Agent**

*Autonomous Financial Analysis Across the Entire U.S. Equities Universe*

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **92% accuracy on the Finance Agent validation set** (80% without corrections) —
> a **37-point gain** over the previous best (55%) using the *same models*.
> Powered by structured data, SOA vector embeddings, and an agentic RAG architecture.
> **Fully reproducible.** See [corrections.md](corrections.md) for benchmark corrections context.

---

## **Overview**

**Intellifin** is an autonomous agent for end-to-end financial reasoning.
It reads filings, extracts facts, runs calculations, and returns sourced answers.

The performance jump came not from larger models, but from *better data plumbing*:
a self-building retrieval stack that turns the entire SEC universe into a structured, queryable knowledge base.

### **Highlights**

* **Full SEC coverage** — all U.S.-listed equities, 10-K, 10-Q, 8-K, proxies, transcripts, and exhibits
* **Contextual embeddings** with company, date, and section baked in at embed-time
* **Markdown-first parsing** preserving tables and stripping XBRL tags
* **Agentic RAG loop** — plan → retrieve → read → compute → answer
* **Local-only vector search** using **Voyage AI** embeddings + rerank step
* **Auditable results** — every figure reproducible, every source cited

---

## **Quick Start**

```bash
git clone https://github.com/lucasastorian/intellifin-agent.git
cd intellifin-agent

# Create a virtual environment (strongly recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

pip install -r requirements.txt

# Copy .env.example and add your API keys
cp .env.example .env
# Edit .env and add at minimum:
#   OPENAI_API_KEY=sk-...
#   VOYAGE_API_KEY=...
# Other keys (ANTHROPIC_API_KEY, GEMINI_API_KEY, FMP_API_KEY) are optional
```

### **Example**

```bash
python main.py \
  --query "What was Palantir's revenue CAGR from 2021–2024?" \
  --user-agent "Your Name <email@example.com>"
```

### **Reproduce Benchmarks**

```bash
python run_eval.py \
  --model gpt-5 \
  --edgar-user-agent "Your Name <email@example.com>"

# Results saved to eval_results/run_YYYY-MM-DD_HH-MM-SS/
```

---

## **The Intellifin Index**

A continuously expanding, context-aware search index built automatically from SEC data.
It integrates every disclosure type—filings, notes, press releases, merger docs, and transcripts—into one unified store.

Each chunk embeds its full context (ticker, filing date, report date, section title), enabling direct queries like:

> “AAPL 2025 Note on Geographic Revenue Breakdown”

Coverage: the **entire U.S. listed-equity universe**, updated on demand as filings appear.

---

## **Benchmark Results**

| Model                                 | Accuracy | Cost / Query | Time (s) |
| :------------------------------------ | :------: | :----------: | :------: |
| **GPT-5 High (Intellifin)**           | **92 %** |     $0.10    |    137   |
| **Claude 4.5 Sonnet (Intellifin)**    |   82 %   |     $0.23    |    50    |
| **Claude 4.5 Haiku (Intellifin)**     |   82 %   |     $0.10    |    53    |
| **Claude 4.5 Sonnet (Finance Agent)** |   55 %   |     $1.41    |    130   |

**Same model, new infrastructure:** +27 points accuracy / –84 % cost.
Intellifin retrieves only what's relevant—no more 100-page context windows.

---

## **Validation-Set Corrections**

The public Finance Agent benchmark contained factual and temporal errors.
We corrected **4 incorrect answers** and refined outdated questions; full details and citations are in [CORRECTIONS.md](CORRECTIONS.md).

**Examples of key fixes**

| Issue                          | Original                      | Corrected                                  |
| :----------------------------- | :---------------------------- | :----------------------------------------- |
| **TKO / Endeavor acquisition** | $3.25 B                       | $3.30 B (includes $50 M adjustment)        |
| **Zillow FCF margin 2022**     | 24.8 %                        | 224 % (GAAP CFO includes Offers wind-down) |
| **KKR Series D preferred**     | “No common dividends allowed” | Allowed if preferreds current              |
| **Delta EPS guidance**         | “YoY growth %”                | Absolute $ range ($6–$7 EPS)               |

Corrections make the benchmark measure reasoning, not tolerance for flawed ground truth.

---

## **Contributing**

Focus areas:
new SEC form parsers • improved table extraction • financial-calc templates • benchmark expansion.
Open an issue before major changes.

---

## **Citation**

```bibtex
@software{intellifin2025,
  title   = {Intellifin Agent: Autonomous Financial Analysis Across the US Equities Universe},
  author  = {Lucas Astorian},
  year    = {2025},
  url     = {https://github.com/lucasastorian/intellifin-agent}
}
```

---

## **License**

MIT License © 2025 Lucas Astorian

---

## **Acknowledgments**

* **Benchmark:** Patronus AI Finance Agent
* **Embeddings:** Voyage AI
* **Data:** U.S. SEC EDGAR
