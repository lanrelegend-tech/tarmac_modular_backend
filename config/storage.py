"""Cloudinary-backed image storage with server-side WebP compression.

Public images are delivered by Cloudinary. Sensitive image fields use the
authenticated delivery type and are fetched only after the API's own access
checks have passed.
"""

from io import BytesIO
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
from django.utils.deconstruct import deconstructible
from PIL import Image, ImageOps, UnidentifiedImageError


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
        content.seek(0)
        try:
            with Image.open(content) as source:
                image = ImageOps.exif_transpose(source)
                image.thumbnail((1920, 1920), Image.Resampling.LANCZOS)
                has_alpha = image.mode in ("RGBA", "LA") or "transparency" in image.info
                image = image.convert("RGBA" if has_alpha else "RGB")
                output = BytesIO()
                image.save(output, format="WEBP", quality=80, method=6)
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
            raise ValueError("Upload a valid image file.") from error

        folder = Path(name.replace("\\", "/")).parent.as_posix()
        if folder == ".":
            folder = "images"
        public_id = f"tarmac/{folder}/{uuid4().hex}"
        return public_id, ContentFile(output.getvalue(), name=f"{uuid4().hex}.webp")

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
        return cloudinary.CloudinaryImage(asset_id).build_url(secure=True, format="webp")

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
