"""Startup FFmpeg encoder diagnostics with Apple Silicon hardware detection."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.config import (
    FFMPEG_COLOR_FLAGS,
    FFMPEG_ENCODER_MODE,
    FFMPEG_VIDEO_BITRATE,
    FFMPEG_VIDEO_BUFSIZE,
    FFMPEG_VIDEO_MAXRATE,
)

logger = logging.getLogger(__name__)


@dataclass
class EncoderProfile:
    name: str
    args: list[str]
    hardware: bool = False
    tested: bool = False
    test_error: str | None = None


@dataclass
class FFmpegDiagnostics:
    ffmpeg_available: bool = False
    ffmpeg_version: str = ""
    encoders_found: list[str] = field(default_factory=list)
    selected: EncoderProfile | None = None
    fallbacks: list[EncoderProfile] = field(default_factory=list)
    probe_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ffmpeg_available": self.ffmpeg_available,
            "ffmpeg_version": self.ffmpeg_version,
            "encoder_mode": FFMPEG_ENCODER_MODE,
            "selected_encoder": self.selected.name if self.selected else None,
            "selected_hardware": self.selected.hardware if self.selected else False,
            "selected_tested": self.selected.tested if self.selected else False,
            "encoders_found": self.encoders_found,
            "fallbacks": [p.name for p in self.fallbacks],
            "probe_errors": self.probe_errors,
        }


_diagnostics: FFmpegDiagnostics | None = None


def _ffmpeg_version() -> tuple[bool, str]:
    if shutil.which("ffmpeg") is None:
        return False, ""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-version"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        return False, result.stderr.strip()
    first_line = (result.stdout or result.stderr).splitlines()[0] if result.stdout else ""
    return True, first_line


def _list_encoders() -> tuple[list[str], str | None]:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        return [], result.stderr.strip()
    encoders = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            encoders.append(parts[1])
    return encoders, None


def _profile_hevc() -> EncoderProfile:
    return EncoderProfile(
        name="hevc_videotoolbox",
        hardware=True,
        args=[
            "-c:v", "hevc_videotoolbox",
            "-b:v", FFMPEG_VIDEO_BITRATE,
            "-maxrate", FFMPEG_VIDEO_MAXRATE,
            "-bufsize", FFMPEG_VIDEO_BUFSIZE,
            "-tag:v", "hvc1",
            "-allow_sw", "0",
            *FFMPEG_COLOR_FLAGS,
        ],
    )


def _profile_h264_vt() -> EncoderProfile:
    return EncoderProfile(
        name="h264_videotoolbox",
        hardware=True,
        args=[
            "-c:v", "h264_videotoolbox",
            "-b:v", FFMPEG_VIDEO_BITRATE,
            "-maxrate", FFMPEG_VIDEO_MAXRATE,
            "-bufsize", FFMPEG_VIDEO_BUFSIZE,
            "-allow_sw", "0",
            *FFMPEG_COLOR_FLAGS,
        ],
    )


def _profile_libx264() -> EncoderProfile:
    return EncoderProfile(
        name="libx264",
        hardware=False,
        args=[
            "-c:v", "libx264",
            "-preset", "slow",
            "-crf", "16",
            *FFMPEG_COLOR_FLAGS,
        ],
    )


def _test_encode(profile: EncoderProfile) -> EncoderProfile:
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "probe.mp4"
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=1080x1920:d=0.1",
            *profile.args,
            "-pix_fmt", "yuv420p",
            "-frames:v", "1",
            str(out),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and out.is_file() and out.stat().st_size > 0:
                profile.tested = True
                logger.info("FFmpeg encoder probe OK: %s", profile.name)
            else:
                profile.test_error = (result.stderr or "encode produced no output")[:500]
                logger.warning(
                    "FFmpeg encoder probe failed (%s): %s",
                    profile.name,
                    profile.test_error,
                )
        except subprocess.TimeoutExpired:
            profile.test_error = "encode probe timed out"
            logger.warning("FFmpeg encoder probe timed out: %s", profile.name)
        except OSError as exc:
            profile.test_error = str(exc)
            logger.warning("FFmpeg encoder probe error (%s): %s", profile.name, exc)
    return profile


def run_ffmpeg_diagnostics(*, test_encode: bool = True) -> FFmpegDiagnostics:
    global _diagnostics
    diag = FFmpegDiagnostics()

    ok, version = _ffmpeg_version()
    diag.ffmpeg_available = ok
    diag.ffmpeg_version = version
    if not ok:
        diag.probe_errors.append("FFmpeg binary not found or failed version check")
        _diagnostics = diag
        return diag

    encoders, err = _list_encoders()
    diag.encoders_found = encoders
    if err:
        diag.probe_errors.append(err)

    candidates: list[EncoderProfile] = []
    mode = FFMPEG_ENCODER_MODE.lower()

    if mode == "libx264":
        candidates = [_profile_libx264()]
    elif mode == "videotoolbox":
        if "hevc_videotoolbox" in encoders:
            candidates.append(_profile_hevc())
        if "h264_videotoolbox" in encoders:
            candidates.append(_profile_h264_vt())
        if not candidates:
            diag.probe_errors.append("VideoToolbox encoders requested but not found")
            candidates = [_profile_libx264()]
    else:
        if "hevc_videotoolbox" in encoders:
            candidates.append(_profile_hevc())
        if "h264_videotoolbox" in encoders:
            candidates.append(_profile_h264_vt())
        candidates.append(_profile_libx264())

    tested_profiles: list[EncoderProfile] = []
    for profile in candidates:
        tested_profiles.append(_test_encode(profile) if test_encode else profile)

    for profile in tested_profiles:
        if profile.tested or not test_encode:
            diag.selected = profile
            break

    if diag.selected is None and tested_profiles:
        diag.selected = tested_profiles[-1]
        diag.probe_errors.append(
            f"All hardware encoders failed; falling back to {diag.selected.name}"
        )
        logger.error(
            "FFmpeg hardware acceleration unavailable — software fallback %s",
            diag.selected.name,
        )

    diag.fallbacks = [p for p in tested_profiles if p is not diag.selected]

    if diag.selected:
        logger.info(
            "FFmpeg encoder selected: %s (hardware=%s, tested=%s)",
            diag.selected.name,
            diag.selected.hardware,
            diag.selected.tested,
        )

    _diagnostics = diag
    return diag


def get_diagnostics() -> FFmpegDiagnostics | None:
    return _diagnostics


def get_encoder_args(*, force_videotoolbox: bool = False) -> list[str]:
    diag = _diagnostics
    if diag and diag.selected:
        if force_videotoolbox and not diag.selected.hardware:
            for fb in diag.fallbacks:
                if fb.hardware and fb.tested:
                    return fb.args
            raise RuntimeError(
                "Apple VideoToolbox encoders not available. "
                "Set FFMPEG_ENCODER=libx264 or install FFmpeg with VideoToolbox."
            )
        return diag.selected.args
    return _profile_libx264().args
