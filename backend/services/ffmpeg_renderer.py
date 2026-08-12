"""
FFmpeg hardware-accelerated video encoding for Apple Silicon.

Encodes frame sequences to 9:16 MP4 using VideoToolbox (HEVC/H.264) and
multiplexes ambient audio from assets/audio/ when available.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2

from backend.config import AUDIO_DIR, REEL_FPS, REEL_HEIGHT, REEL_WIDTH

logger = logging.getLogger(__name__)


class FFmpegRenderer:
    """Hardware-accelerated MP4 encoder via FFmpeg VideoToolbox."""

    def __init__(self, *, prefer_hevc: bool = True) -> None:
        self.prefer_hevc = prefer_hevc
        self._encoder_args: list[str] | None = None

    def _ensure_ffmpeg(self) -> None:
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("FFmpeg not found. Install via: brew install ffmpeg")

    def get_encoder_args(self, *, force_videotoolbox: bool = True) -> list[str]:
        """Return FFmpeg video encoder flags, preferring Apple VideoToolbox."""
        if self._encoder_args is not None:
            return self._encoder_args

        self._ensure_ffmpeg()
        encoders = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout

        if self.prefer_hevc and "hevc_videotoolbox" in encoders:
            self._encoder_args = [
                "-c:v", "hevc_videotoolbox",
                "-b:v", "14M",
                "-maxrate", "16M",
                "-bufsize", "32M",
                "-tag:v", "hvc1",
                "-allow_sw", "0",
            ]
        elif "h264_videotoolbox" in encoders:
            self._encoder_args = [
                "-c:v", "h264_videotoolbox",
                "-b:v", "12M",
                "-maxrate", "14M",
                "-bufsize", "28M",
                "-allow_sw", "0",
            ]
        elif force_videotoolbox:
            raise RuntimeError(
                "Apple VideoToolbox encoders not available. "
                "Ensure FFmpeg was built with --enable-videotoolbox."
            )
        else:
            self._encoder_args = ["-c:v", "libx264", "-preset", "slow", "-crf", "18"]

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
        Encode a frame sequence to 1080×1920 MP4 with optional audio mux.

        Uses hevc_videotoolbox or h264_videotoolbox on Apple Silicon.
        """
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        if not frames:
            raise ValueError("No frames to encode")

        h, w = frames[0].shape[:2]
        if (w, h) != (REEL_WIDTH, REEL_HEIGHT):
            logger.warning(
                "Frame size %dx%d != target %dx%d; encoding as-is",
                w, h, REEL_WIDTH, REEL_HEIGHT,
            )

        encoder = self.get_encoder_args(force_videotoolbox=force_videotoolbox)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            for idx, frame in enumerate(frames):
                cv2.imwrite(str(tmp / f"frame_{idx:05d}.png"), frame)

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
                    "-b:a", "192k",
                    "-shortest",
                    "-movflags", "+faststart",
                    str(output),
                ]
                mux_result = subprocess.run(mux_cmd, capture_output=True, text=True)
                if mux_result.returncode != 0:
                    raise RuntimeError(f"FFmpeg audio mux failed:\n{mux_result.stderr}")
            else:
                shutil.copy(silent_video, output)

        logger.info("Encoded reel → %s (%d frames @ %dfps)", output, len(frames), fps)
        return output

    @property
    def encoder_name(self) -> str:
        args = self.get_encoder_args(force_videotoolbox=False)
        idx = args.index("-c:v") if "-c:v" in args else -1
        return args[idx + 1] if idx >= 0 else "unknown"
