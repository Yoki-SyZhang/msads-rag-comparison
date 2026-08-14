"""Adapters that normalize pipeline-specific outputs for the comparison UI."""

from .pipeline_flatrag_adapter import FlatRAGAdapter
from .pipeline_grag_v2_adapter import GRAGv2Adapter

__all__ = ["FlatRAGAdapter", "GRAGv2Adapter"]
