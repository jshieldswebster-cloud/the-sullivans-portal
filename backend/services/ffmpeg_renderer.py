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
                "-b:v", self.video_bitrate,
                "-maxrate", FFMPEG_VIDEO_MAXRATE,
                "-bufsize", FFMPEG_VIDEO_BUFSIZE,
                "-tag:v", "hvc1",
                "-allow_sw", "0",
                *FFMPEG_COLOR_FLAGS,
            ]
        elif "h264_videotoolbox" in encoders:
            self._encoder_args = [
                "-c:v", "h264_videotoolbox",
                "-b:v", self.video_bitrate,
                "-maxrate", FFMPEG_VIDEO_MAXRATE,
                "-bufsize", FFMPEG_VIDEO_BUFSIZE,
                "-allow_sw", "0",
                *FFMPEG_COLOR_FLAGS,
            ]
        elif force_videotoolbox:
            raise RuntimeError(
                "Apple VideoToolbox encoders not available. "
                "Ensure FFmpeg was built with --enable-videotoolbox."
            )
        else:
            self._encoder_args = [
                "-c:v", "libx264",
                "-preset", "slow",
                "-crf", "16",
                *FFMPEG_COLOR_FLAGS,
            ]

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

    @property
    def encoder_name(self) -> str:
        args = self.get_encoder_args(force_videotoolbox=False)
        idx = args.index("-c:v") if "-c:v" in args else -1
        return args[idx + 1] if idx >= 0 else "unknown"
