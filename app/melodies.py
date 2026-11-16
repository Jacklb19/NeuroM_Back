"""Shapes of the melodies a person saves from the listening screen (P-17).

A melody is what the synthesizer needs to play a piece again: its seed, the
sound it was played with, its step and the point where it was saved. None of
it is health data, so these endpoints need an account but no second factor.
Every value is a catalog id or a number within range, and unknown fields are
rejected, so nothing else can be stored.
"""

from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.constants import (
    MAX_MELODY_POSITION_SECONDS,
    MAX_MELODY_SEED,
    MAX_SYNTHESIS_VERSION,
    MELODY_NAME_MAX_CHARS,
)


class Instrument(StrEnum):
    """Instruments of the synthesizer (ADR-46). Mirrors the frontend's instrument catalog."""

    WARM = "warm"
    PIANO = "piano"
    BOWLS = "bowls"


class Ambience(StrEnum):
    """Ambiences under the music (ADR-40). Mirrors the frontend's ambience catalog."""

    NONE = "none"
    RAIN = "rain"
    SEA = "sea"
    WIND = "wind"


class SoundProfile(StrEnum):
    """Textures of the sound (ADR-59). Mirrors the frontend's ``SOUND_PROFILES``."""

    ORIGINAL = "original"
    MINIMAL = "minimal"
    IMMERSIVE = "immersive"


class MusicLevel(StrEnum):
    """Steps of the music (ADR-41). Mirrors the frontend's ``LEVEL_IDS``."""

    HIGH = "high"
    INTERMEDIATE = "intermediate"
    TARGET = "target"
    SLEEP = "sleep"


Mix = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MelodyContent(_Strict):
    """A melody as the device sends it; the id travels in the path."""

    name: str = Field(min_length=1, max_length=MELODY_NAME_MAX_CHARS)
    seed: int = Field(ge=0, le=MAX_MELODY_SEED)
    instrument: Instrument
    ambience: Ambience
    profile: SoundProfile
    level: MusicLevel
    position_seconds: int = Field(ge=0, le=MAX_MELODY_POSITION_SECONDS)
    music_mix: Mix
    ambience_mix: Mix
    synthesis_version: int = Field(ge=1, le=MAX_SYNTHESIS_VERSION)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def _is_consistent(self) -> Self:
        # The same rules as the table, so a refused melody is told apart from a database failure.
        if self.name != self.name.strip():
            raise ValueError("the name cannot start or end with spaces")
        if self.updated_at < self.created_at:
            raise ValueError("a melody cannot change before it was created")
        return self


class Melody(MelodyContent):
    """A stored melody, with its id."""

    id: UUID


class MelodyList(BaseModel):
    """Every melody of the account, most recently changed first."""

    melodies: list[Melody]


class SavedMelody(BaseModel):
    """Answer to a save: whether the melody was new."""

    id: UUID
    created: bool
