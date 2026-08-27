"""V0.1 rule-based decision engine."""

from __future__ import annotations

from typing import Any

from openraw_studio.core.domain import EngineInfo, ProcessingDecision
from openraw_studio.decision.interfaces import DecisionRequest


class RuleBasedDecisionEngine:
    """Choose conservative defaults from available analysis confidence."""

    def engine_info(self) -> EngineInfo:
        return EngineInfo(
            name="rule-based-decision",
            version="0.1.0",
            backend="deterministic",
            capabilities={"computational_presets": True},
        )

    def decide(self, request: DecisionRequest) -> ProcessingDecision:
        has_face = any(face.confidence >= 0.65 for face in request.analysis.faces)
        profile = "portrait" if has_face and "portrait" in request.processing_presets else request.default_processing_profile
        look = request.requested_look or request.default_creative_look
        scene_confidence = max((scene.confidence for scene in request.analysis.scenes), default=0.0)
        confidence = min(0.55 if profile == "portrait" else 0.35, max(scene_confidence, 0.2))

        adjustments: dict[str, Any] = {
            "raw": {
                "exposure": 0.0,
                "highlight_recovery": 0.15,
                "shadow_recovery": 0.1,
            },
            "portrait": {
                "global": {
                    "enabled": has_face,
                    "auto_strength": request.user_constraints.get("auto_strength", 0.5),
                },
                "faces": [
                    {
                        "face_id": face.face_id,
                        "face_exposure": 0.0,
                        "skin_smooth": 0.0,
                        "whitening": 0.0,
                        "face_slim": 0.0,
                        "eye_enhance": 0.0,
                    }
                    for face in request.analysis.faces
                ],
            },
            "color": {
                "look_strength": 0.0 if look == "clean" else 0.25,
                "skin_protection": 0.9,
            },
            "film": {
                "profile": "none" if look == "clean" else look,
                "strength": 0.0 if look == "clean" else 0.25,
                "grain": 0.0,
                "halation": 0.0,
                "bloom": 0.0,
            },
        }

        rationale = [
            "V0.1 uses conservative rule-based decisions.",
            "No portrait edits are applied unless reliable face observations exist.",
        ]
        if confidence < 0.5:
            rationale.append("Low confidence keeps automatic adjustments subtle.")

        return ProcessingDecision(
            processing_profile=profile,
            creative_look=look,
            confidence=confidence,
            adjustments=adjustments,
            constraints={
                "low_confidence_edit_scale": request.user_constraints.get("low_confidence_edit_scale", 0.35),
                "preserve_original_raw": True,
            },
            rationale=tuple(rationale),
        )
