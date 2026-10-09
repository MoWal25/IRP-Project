# AegisFlow: Multi-Agent Industrial Quality & Profitability Intelligence

> **NeuraX Hackathon 3.0** | **Domain 2: AI in Industry and Automation**  
> **Problem Statement:** Visual Inspection & Defect Root-Cause Assistant

---

## 📌 Executive Summary

**AegisFlow** is an end-to-end, software-only industrial decision-support system designed for high-throughput manufacturing lines. Modern factories operate under tight trade-offs between **product quality**, **line throughput**, and **net profit margins**. Traditional computer vision systems operate in isolated silos—they flag defective units at the end of an assembly line without identifying *why* defects occur, *where* bottlenecks form, or *how* scrap degrades profitability.

AegisFlow bridges this gap by deploying a **Multi-Agent AI Orchestrator** that unifies computer vision, machine sensor telemetry, root-cause causal inference, digital-twin simulation, and economic ROI modeling into a continuous advisory loop.

---

## 🚀 Key Features & Uniqueness

- **OOD Novel Defect Handling:** Uses Out-of-Distribution (PatchCore) embedding analysis to flag unknown/novel defect types as **"Unknown"** for human audit, preventing forced bad guesses.
- **Predictive Asset & Machine Health:** Monitors thermal drift and vibration telemetry to forecast machine degradation before tool snaps occur.
- **Causal Root-Cause Attribution:** Constructs Causal Directed Acyclic Graphs (DAGs) and SHAP feature importance to mathematically link visual defects to upstream machine parameters.
- **Operational Bottleneck Engine:** Tracks cycle times, station wait times, and Work-in-Process (WIP) build-ups to locate line constraints.
- **"What-If" Financial & Profitability Simulator:** Interactive digital-twin modeling allowing plant managers to test line adjustments (e.g., speed vs. yield) and project margin impact.
- **Multilingual Operator HMI:** Frontline-accessible dashboard with instant language toggles (English, Hindi, regional dialects) and text-to-speech guidance for ground-level technicians.
- **Batch Traceability & Lineage Tracking:** Full unit-to-batch mapping to trace downstream customer complaints back to root batch telemetry logs.
- **Transit Quality Forensics:** Compares pre-dispatch photos against post-delivery images to distinguish factory defects from shipping damage.

---

## 🏗 System Architecture

The system utilizes an agentic orchestration model where specialized sub-agents handle specific operational domains under a central router: