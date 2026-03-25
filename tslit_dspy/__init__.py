"""
TSLIT-DSPy: DSPy-compiled threat detection pipeline for TSLIT.

Replaces TSLIT's LangGraph analyzer with MIPROv2-optimized DSPy modules
that compile threat detection capability into the prompts themselves,
enabling small local Ollama models to reason like security analysts.
"""

__version__ = "0.1.0"
__author__ = "Nic Cravino"

from tslit_dspy.schemas import AnalysisResult
from tslit_dspy.modules import TSLITAnalyzer

__all__ = ["TSLITAnalyzer", "AnalysisResult"]
