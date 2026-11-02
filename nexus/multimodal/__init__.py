"""NEXUS Multimodal Layer (Phase 10).
Clean modality interfaces over existing voice/media/vision.
Honest status only: DONE | PARTIAL | UNKNOWN | STUB.
No paid cloud core deps. Vision on-demand keep_alive=0. NEVER pull.
"""
from __future__ import annotations

from nexus.multimodal.registry import modality_matrix, multimodal_status

__all__ = ["modality_matrix", "multimodal_status"]
