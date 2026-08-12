"""Video, carousel, and caption generation orchestration."""

from __future__ import annotations

import asyncio
from pathlib import Path

from backend.database import create_job, list_images, update_job
from backend.models.caption_engine import CaptionEngine
from backend.models.carousel_compositor import CarouselCompositor
from backend.models.depth_engine import DepthParallaxEngine
import backend.services.video_service  # noqa: F401 — DepthEngine render compat
from backend.services.caption_service import CaptionService


class RenderService:
    def __init__(
        self,
        depth_engine: DepthParallaxEngine | None = None,
        carousel: CarouselCompositor | None = None,
        caption_engine: CaptionEngine | None = None,
        caption_service: CaptionService | None = None,
    ) -> None:
        self.depth = depth_engine or DepthParallaxEngine()
        self.carousel = carousel or CarouselCompositor()
        self.captions = caption_engine or CaptionEngine()
        self.caption_service = caption_service or CaptionService(
            caption_engine=self.captions
        )

    def _paths_for_category(self, category: str) -> list[str]:
        rows = list_images(category=category)
        return [row["filepath"] for row in rows]

    async def generate_reel(
        self,
        image_path: str,
        category: str,
        *,
        motion: str = "push_in",
        duration_sec: float = 5.0,
        fps: int = 30,
    ) -> str:
        job_id = create_job("reel", [image_path], category=category)
        update_job(job_id, status="running")
        try:
            if self.depth._model is None:
                self.depth.load()
            output = self.depth.render_reel(
                image_path,
                category=category,
                motion=motion,
                duration_sec=duration_sec,
                fps=fps,
            )
            update_job(job_id, status="completed", output_path=str(output))
            return str(output)
        except Exception as exc:
            update_job(job_id, status="failed", error=str(exc))
            raise

    async def generate_carousel(self, category: str) -> list[str]:
        paths = self._paths_for_category(category)
        if not paths:
            raise ValueError(f"No images found for category: {category}")

        job_id = create_job("carousel", paths, category=category)
        update_job(job_id, status="running")
        try:
            slides = self.carousel.build_carousel(paths, category)
            output_paths = [str(p) for p in slides]
            update_job(
                job_id,
                status="completed",
                output_path=str(slides[0].parent),
            )
            return output_paths
        except Exception as exc:
            update_job(job_id, status="failed", error=str(exc))
            raise

    async def generate_caption(
        self,
        category: str,
        *,
        image_path: str | None = None,
        image_context: str | None = None,
    ) -> str:
        job_id = create_job(
            "caption",
            [image_path] if image_path else [],
            category=category,
        )
        update_job(job_id, status="running")
        try:
            if image_path:
                result = await self.caption_service.generate_enriched_caption(
                    image_path,
                    category=category,
                    use_enriched_prompt=True,
                    save=True,
                )
                caption = result["caption"]
                output_path = result.get("saved_path")
            else:
                caption = await self.caption_service.generate_category_caption(
                    category, image_context=image_context
                )
                path = await self.captions.generate_and_save(
                    category,
                    image_context=image_context,
                    use_enriched_prompt=bool(image_context),
                )
                output_path = str(path)

            update_job(job_id, status="completed", output_path=output_path)
            return caption
        except Exception as exc:
            update_job(job_id, status="failed", error=str(exc))
            raise

    async def generate_category_bundle(self, category: str) -> dict:
        """Generate carousel + enriched caption + reel from best-scoring image."""
        paths = self._paths_for_category(category)
        if not paths:
            raise ValueError(f"No images for category: {category}")

        hero_image = paths[0]
        carousel_task = self.generate_carousel(category)
        caption_task = self.generate_caption(category, image_path=hero_image)
        reel_task = self.generate_reel(hero_image, category)

        carousel, caption, reel = await asyncio.gather(
            carousel_task, caption_task, reel_task
        )
        return {
            "category": category,
            "carousel_slides": carousel,
            "caption": caption,
            "reel_path": reel,
            "source_image": hero_image,
        }
