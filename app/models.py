"""Pydantic request and response schemas."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class VideoRequest(BaseModel):
    topic: str = Field(..., min_length=3, max_length=200)
    niche: str = "Motivation"
    length: str = "60 seconds"
    style: str = "Cinematic"
    voice: Optional[str] = None
    caption_style: str = "bold"
    background: str = "aurora"
    aspect: str = "9:16"
    brand: str = ""
    music: bool = True
    script: Optional[str] = None

    @field_validator("topic")
    @classmethod
    def topic_not_blank(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("topic must be at least 3 characters")
        return v


class ScriptRequest(BaseModel):
    topic: str = Field(..., min_length=3, max_length=200)
    niche: str = "Motivation"
    length: str = "60 seconds"
    style: str = "Cinematic"
    script: Optional[str] = None

    @field_validator("topic")
    @classmethod
    def topic_not_blank(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("topic must be at least 3 characters")
        return v


class ScriptSceneOut(BaseModel):
    index: int
    narration: str
    caption: str
    start: float = 0.0
    end: float = 0.0


class ScriptOut(BaseModel):
    title: str
    hook: str
    scenes: List[ScriptSceneOut]
    cta: str
    engine: str
    script: str


class JobOut(BaseModel):
    id: str
    status: str
    progress: int
    stage: str
    message: str
    error: Optional[str] = None
    request: Optional[VideoRequest] = None
    engine: Optional[str] = None
    duration: Optional[float] = None
    scenes: List[ScriptSceneOut] = Field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0


class JobListOut(BaseModel):
    videos: List[JobOut]


class HealthOut(BaseModel):
    status: str
    ffmpeg: bool
    llm: bool
    llm_model: str
    engine: str
    resolution: str
    version: str


class OptionsOut(BaseModel):
    niches: List[str]
    lengths: List[str]
    styles: List[str]
    caption_styles: List[str]
    aspects: List[str]


class VoicesOut(BaseModel):
    voices: List[dict]
    default: str


class BackgroundsOut(BaseModel):
    backgrounds: List[str]
    default: str
