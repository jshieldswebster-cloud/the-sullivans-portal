"""
FFmpeg hardware-accelerated video encoding for Apple Silicon.

Encodes frame sequences to high-bitrate 9:16 MP4 using VideoToolbox (HEVC/H.264)
with BT.709 color preservation and optional native-resolution output.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2

from backend.config import (
    AUDIO_DIR,
    FFMPEG_COLOR_FLAGS,
    FFMPEG_VIDEO_BITRATE,
    FFMPEG_VIDEO_BUFSIZE,
    FFMPEG_VIDEO_MAXRATE,
    REEL_FPS,
    REEL_HEIGHT,
    REEL_WIDTH,
)

logger = logging.getLogger(__name__)


class FFmpegRenderer:
    """Hardware-accelerated MP4 encoder via FFmpeg VideoToolbox."""

    def __init__(self, *, prefer_hevc: bool = True, video_bitrate: str = FFMPEG_VIDEO_BITRATE) -> None:
        self.prefer_hevc = prefer_hevc
        self.video_bitrate = video_bitrate
        self._encoder_args: list[str] | None = None

    def _ensure_ffmpeg(self) -> None:
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("FFmpeg not found. Install via: brew install ffmpeg")

    def get_encoder_args(self, *, force_videotoolbox: bool = True) -> list[str]:
        """Return FFmpeg video encoder flags from startup diagnostics cache."""
        from backend.services.ffmpeg_diagnostics import get_encoder_args

        if self._encoder_args is not None:
            return self._encoder_args

        self._ensure_ffmpeg()
        self._encoder_args = get_encoder_args(force_videotoolbox=force_videotoolbox)
        logger.info("FFmpeg encoder: %s", " ".join(self._encoder_args))
        return self._encoder_args

    def pick_audio(self, audio_path: str | Path | None = None) -> Path | None:
        if audio_path:
            path = Path(audio_path)
            return path if path.exists() else None
        for ext in ("*.mp3", "*.wav", "*.m4a", "*.aac", "*.flac"):
            matches = sorted(AUDIO_DIR.glob(ext))
            if matches:
                return matches[0]
        return None

    def encode_frames(
        self,
        frames: list,
        output_path: str | Path,
        *,
        fps: int = REEL_FPS,
        audio_path: str | Path | None = None,
        force_videotoolbox: bool = True,
    ) -> Path:
        """
        Encode a frame sequence to high-bitrate MP4 with BT.709 color tags.

        Preserves native frame dimensions when provided (no forced 1080×1920).
        """
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        if not frames:
            raise ValueError("No frames to encode")

        h, w = frames[0].shape[:2]
        logger.info("Encoding %d frames at native %dx%d @ %dfps", len(frames), w, h, fps)

        encoder = self.get_encoder_args(force_videotoolbox=force_videotoolbox)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            for idx, frame in enumerate(frames):
                cv2.imwrite(
                    str(tmp / f"frame_{idx:05d}.png"),
                    frame,
                    [cv2.IMWRITE_PNG_COMPRESSION, 1],
                )

            silent_video = tmp / "silent.mp4"
            encode_cmd = [
                "ffmpeg", "-y",
                "-framerate", str(fps),
                "-i", str(tmp / "frame_%05d.png"),
                *encoder,
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                str(silent_video),
            ]
            result = subprocess.run(encode_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg encode failed:\n{result.stderr}")

            audio = self.pick_audio(audio_path)
            if audio:
                mux_cmd = [
                    "ffmpeg", "-y",
                    "-i", str(silent_video),
                    "-i", str(audio),
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-b:a", "256k",
                    "-shortest",
                    "-movflags", "+faststart",
                    *FFMPEG_COLOR_FLAGS,
                    str(output),
                ]
                mux_result = subprocess.run(mux_cmd, capture_output=True, text=True)
                if mux_result.returncode != 0:
                    raise RuntimeError(f"FFmpeg audio mux failed:\n{mux_result.stderr}")
            else:
                shutil.copy(silent_video, output)

        logger.info("Encoded reel → %s (%d frames @ %dfps, %s)", output, len(frames), fps, self.video_bitrate)
        return output

    def assemble_xfade(
        self,
        clip_paths: list[Path],
        output_path: Path,
        *,
        transition_sec: float,
        clip_duration_sec: float,
        fps: int = REEL_FPS,
    ) -> Path:
        """Chain clip MP4s with smooth cross-fade dissolves via FFmpeg xfade."""
        self._ensure_ffmpeg()
        if len(clip_paths) < 2:
            raise ValueError("xfade requires at least 2 clips")

        inputs: list[str] = []
        for clip in clip_paths:
            inputs.extend(["-i", str(clip)])

        filter_parts: list[str] = []
        prev_label = "[0:v]"
        for i in range(1, len(clip_paths)):
            out_label = f"[v{i}]" if i < len(clip_paths) - 1 else "[vout]"
            offset = i * (clip_duration_sec - transition_sec)
            filter_parts.append(
                f"{prev_label}[{i}:v]xfade=transition=fade:duration={transition_sec:.3f}:offset={offset:.3f}{out_label}"
            )
            prev_label = out_label

        filter_complex = ";".join(filter_parts)
        encoder = self.get_encoder_args(force_videotoolbox=False)

        cmd = [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            *encoder,
            "-r", str(fps),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            *FFMPEG_COLOR_FLAGS,
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg xfade failed:\n{result.stderr}")

        logger.info(
            "xfade assembled %d clips → %s (transition=%.2fs)",
            len(clip_paths),
            output_path,
            transition_sec,
        )
        return output_path

    def apply_logo_overlay(
        self,
        video_path: Path,
        output_path: Path,
        logo_path: Path,
        *,
        opacity: float | None = None,
        anchor: str | None = None,
        margin: int | None = None,
        logo_width_ratio: float | None = None,
    ) -> Path:
        """Burn a transparent logo onto every frame (top-center by default)."""
        from backend.config import (
            DEFAULT_LOGO_ANCHOR,
            DEFAULT_LOGO_MARGIN,
            DEFAULT_LOGO_OPACITY,
            MONTAGE_LOGO_WIDTH_RATIO,
            REEL_WIDTH,
        )
        from backend.services.logo_overlay import logo_overlay_position

        self._ensure_ffmpeg()
        if not logo_path.is_file():
            shutil.copy(video_path, output_path)
            return output_path

        opacity = DEFAULT_LOGO_OPACITY if opacity is None else opacity
        anchor = DEFAULT_LOGO_ANCHOR if anchor is None else anchor
        margin = DEFAULT_LOGO_MARGIN if margin is None else margin
        logo_width_ratio = MONTAGE_LOGO_WIDTH_RATIO if logo_width_ratio is None else logo_width_ratio

        target_w = int(REEL_WIDTH * logo_width_ratio)
        ox, oy = logo_overlay_position(anchor=anchor, margin=margin)
        filter_complex = (
            f"[1:v]scale={target_w}:-1,format=rgba,"
            f"colorchannelmixer=aa={opacity:.3f}[logo];"
            f"[0:v][logo]overlay={ox}:{oy}:format=auto[vout]"
        )
        encoder = self.get_encoder_args(force_videotoolbox=False)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(logo_path),
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", "0:a?",
            *encoder,
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            *FFMPEG_COLOR_FLAGS,
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg logo overlay failed:\n{result.stderr}")

        logger.info("Logo overlay applied → %s", output_path)
        return output_path

    def probe_audio_duration(self, audio_path: str | Path) -> float | None:
        """Return audio duration in seconds via ffprobe."""
        from backend.services.audio_library_service import probe_audio_duration

        return probe_audio_duration(Path(audio_path))

    def mux_audio(
        self,
        video_path: Path,
        output_path: Path,
        *,
        audio_path: str | Path | None = None,
        duration_sec: float | None = None,
    ) -> Path:
        """Mux background audio trimmed or looped to match exact reel duration."""
        audio = self.pick_audio(audio_path)
        if not audio:
            shutil.copy(video_path, output_path)
            return output_path

        self._ensure_ffmpeg()
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
        ]

        audio_duration = self.probe_audio_duration(audio)
        needs_loop = (
            duration_sec is not None
            and audio_duration is not None
            and audio_duration < duration_sec - 0.05
        )

        if needs_loop:
            cmd.extend(["-stream_loop", "-1", "-i", str(audio)])
        else:
            cmd.extend(["-i", str(audio)])

        cmd.extend([
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "256k",
            "-movflags", "+faststart",
            *FFMPEG_COLOR_FLAGS,
        ])

        if duration_sec:
            cmd.extend(["-t", f"{duration_sec:.3f}"])
        else:
            cmd.append("-shortest")
        cmd.append(str(output_path))

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg audio mux failed:\n{result.stderr}")

        action = "looped" if needs_loop else "trimmed"
        logger.info(
            "Audio mux (%s to %.2fs) → %s",
            action,
            duration_sec or 0,
            output_path,
        )
        return output_path

    @property
    def encoder_name(self) -> str:
        args = self.get_encoder_args(force_videotoolbox=False)
        idx = args.index("-c:v") if "-c:v" in args else -1
        return args[idx + 1] if idx >= 0 else "unknown"
