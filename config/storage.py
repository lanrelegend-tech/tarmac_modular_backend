"""Cloudinary-backed image storage with server-side WebP compression.

Public images are delivered by Cloudinary. Sensitive image fields use the
authenticated delivery type and are fetched only after the API's own access
checks have passed.
"""

from io import BytesIO
import mimetypes
from pathlib import Path
from time import time
from uuid import uuid4

import cloudinary
import cloudinary.uploader
import requests
from cloudinary.utils import private_download_url
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.http import FileResponse
from django.utils.deconstruct import deconstructible
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework.exceptions import ValidationError

MAX_UPLOAD_IMAGE_PIXELS = 30_000_000
MAX_RAW_IMAGE_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_UPLOAD_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}


def optimized_image_response(image_field, cache_seconds=600):
    """Stream a compressed image and let each signed-in browser reuse it briefly."""
    content_type = "image/webp" if getattr(image_field.storage, "cloudinary_enabled", False) else mimetypes.guess_type(image_field.name)[0]
    response = FileResponse(
        image_field.open("rb"),
        content_type=content_type or "image/webp",
    )
    response["Cache-Control"] = f"private, max-age={cache_seconds}, stale-while-revalidate=60"
    response["Vary"] = "Cookie"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def optimize_uploaded_image(content, max_size=(1600, 1600), quality=78, max_output_bytes=2 * 1024 * 1024):
    """Normalize an uploaded raster image to a small, orientation-correct WebP."""
    if getattr(content, "size", 0) > MAX_RAW_IMAGE_UPLOAD_BYTES:
        raise ValidationError("Images must be 25 MB or smaller before compression.")
    content.seek(0)
    try:
        with Image.open(content) as source:
            if source.format not in ALLOWED_UPLOAD_IMAGE_FORMATS:
                raise ValidationError("Use a JPG, PNG, or WEBP image.")
            width, height = source.size
            if width <= 0 or height <= 0 or width * height > MAX_UPLOAD_IMAGE_PIXELS:
                raise ValidationError("Image dimensions are too large. Resize the image and try again.")
            image = ImageOps.exif_transpose(source)
            image.thumbnail(max_size, Image.Resampling.LANCZOS)
            has_alpha = image.mode in ("RGBA", "LA") or "transparency" in image.info
            image = image.convert("RGBA" if has_alpha else "RGB")
            minimum_dimension = max(480, min(max_size) // 2)
            current_quality = quality
            while True:
                output = BytesIO()
                image.save(output, format="WEBP", quality=current_quality, method=5)
                if output.tell() <= max_output_bytes:
                    break
                if current_quality > 52:
                    current_quality = max(52, current_quality - 8)
                    continue
                width, height = image.size
                if max(width, height) <= minimum_dimension:
                    raise ValidationError("This image cannot be compressed enough. Choose a smaller image.")
                next_size = (max(1, int(width * 0.85)), max(1, int(height * 0.85)))
                image = image.resize(next_size, Image.Resampling.LANCZOS)
                current_quality = 64
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValidationError("Upload a valid image file.") from error
    return ContentFile(output.getvalue(), name=f"{uuid4().hex}.webp")


@deconstructible
class CloudinaryImageStorage(FileSystemStorage):
    """Compress uploads to WebP before saving to Cloudinary."""

    private = False

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("location", settings.MEDIA_ROOT)
        super().__init__(*args, **kwargs)

    @property
    def cloudinary_enabled(self):
        return bool(getattr(settings, "CLOUDINARY_URL", ""))

    def _optimized_image(self, content, name):
        folders = {part.lower() for part in Path(name.replace("\\", "/")).parts}
        # Profile photos appear throughout the app as small avatars, so
        # storing a 2K original for them only wastes transfer and storage.
        if "profiles" in folders or "profile_change_requests" in folders:
            max_size, quality = (512, 512), 76
        elif "site" in folders:
            max_size, quality = (960, 960), 80
        elif "reviews" in folders or "lost_found" in folders:
            max_size, quality = (1200, 1200), 77
        elif "student_ids" in folders or "report_evidence" in folders:
            # Keep text and submitted evidence legible for admin review.
            max_size, quality = (1920, 1920), 84
        else:
            max_size, quality = (1600, 1600), 78
        max_output_bytes = 3 * 1024 * 1024 if "student_ids" in folders else 2 * 1024 * 1024
        optimized = optimize_uploaded_image(content, max_size=max_size, quality=quality, max_output_bytes=max_output_bytes)
        folder = Path(name.replace("\\", "/")).parent.as_posix()
        if folder == ".":
            folder = "images"
        public_id = f"tarmac/{folder}/{uuid4().hex}"
        return public_id, optimized

    def _save(self, name, content):
        public_id, optimized = self._optimized_image(content, name)
        if not self.cloudinary_enabled:
            if not settings.DEBUG:
                raise RuntimeError("Configure CLOUDINARY_URL before accepting production image uploads.")
            local_name = f"{public_id.removeprefix('tarmac/')}.webp"
            return super()._save(local_name, optimized)

        options = {
            "public_id": public_id,
            "resource_type": "image",
            "overwrite": False,
            "invalidate": True,
            "type": "authenticated" if self.private else "upload",
        }
        result = cloudinary.uploader.upload(optimized, **options)
        stored_id = result["public_id"]
        return f"__tarmac_private__/{stored_id}.webp" if self.private else stored_id

    def _private_asset(self, name):
        prefix = "__tarmac_private__/"
        return name[len(prefix):].removesuffix(".webp") if name.startswith(prefix) else None

    def _asset_url(self, name):
        asset_id = self._private_asset(name) or name
        if self.private:
            return private_download_url(
                asset_id,
                "webp",
                resource_type="image",
                type="authenticated",
                expires_at=int(time()) + 300,
            )
        folders = {part.lower() for part in Path(asset_id).parts}
        width = 512 if "profiles" in folders or "profile_change_requests" in folders else 1000 if "products" in folders or "reviews" in folders or "lost_found" in folders else 1200
        return self.optimized_url(asset_id, width=width)

    def optimized_url(self, name, width=1200, height=None, crop="limit"):
        """Return a CDN-transformed URL sized for browser display, not the original."""
        asset_id = self._private_asset(name) or name
        if self.private:
            return self.url(name)
        transformations = [{"width": width, "crop": crop}]
        if height:
            transformations[0]["height"] = height
        transformations.append({"quality": "auto:good"})
        return cloudinary.CloudinaryImage(asset_id).build_url(
            secure=True, format="webp", transformation=transformations
        )

    def _open(self, name, mode="rb"):
        if self.cloudinary_enabled and (not self.private or self._private_asset(name)):
            response = requests.get(self._asset_url(name), timeout=20)
            if response.status_code == 200:
                file = ContentFile(response.content, name=name)
                file.mode = mode
                return file
            if response.status_code != 404:
                response.raise_for_status()
        # Allows local development and old local uploads to remain readable.
        return super()._open(name, mode)

    def url(self, name):
        if self.cloudinary_enabled and (not self.private or self._private_asset(name)):
            return self._asset_url(name)
        return super().url(name)

    def delete(self, name):
        if self.cloudinary_enabled and (not self.private or self._private_asset(name)):
            asset_id = self._private_asset(name) or name
            cloudinary.uploader.destroy(
                asset_id,
                resource_type="image",
                type="authenticated" if self.private else "upload",
                invalidate=True,
            )
            return
        return super().delete(name)

    def exists(self, name):
        # Each upload gets a server-generated UUID, so this avoids a remote
        # Admin API lookup for every save while preserving local fallback.
        return super().exists(name) if not self.cloudinary_enabled else False

    def size(self, name):
        if self.cloudinary_enabled and (not self.private or self._private_asset(name)):
            response = requests.head(self._asset_url(name), timeout=10)
            response.raise_for_status()
            return int(response.headers.get("content-length", 0))
        return super().size(name)


@deconstructible
class PrivateCloudinaryImageStorage(CloudinaryImageStorage):
    private = True
