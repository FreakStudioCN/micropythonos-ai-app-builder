from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import socket
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx


MAX_APP_RUNTIME_BYTES = 1_048_576
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
MAX_DECODED_PIXELS = 4_194_304
MAX_REDIRECTS = 4
ASSET_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SAFE_LICENSE_MARKERS = (
    "cc0",
    "public domain",
    "cc by",
    "cc-by",
    "creative commons attribution",
)
RESTRICTED_LICENSE_MARKERS = (
    "noncommercial",
    "non-commercial",
    "no derivatives",
    "no-derivatives",
    "cc by-nc",
    "cc-by-nc",
    "cc by-nd",
    "cc-by-nd",
)


class VisualAssetError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        owner: str = "backend",
        retryable: bool = True,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.owner = owner
        self.retryable = retryable
        self.details = details or {}

    def structured(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "stage": "generate",
            "phase": "mpos-gen-app-web",
            "owner": self.owner,
            "retryable": self.retryable,
            "details": self.details,
            "logs": [],
        }


@dataclass(frozen=True)
class BuiltVisualAsset:
    asset_id: str
    purpose: str
    runtime_path: str
    preview_path: Path
    runtime_file: Path
    metadata_path: Path
    source_record_path: Path | None
    runtime_format: str
    width: int
    height: int
    runtime_bytes: int
    runtime_sha256: str
    fallback: str
    generation_mode: str
    source_page_url: str = ""
    license: str = ""
    attribution: str = ""

    def public_metadata(self) -> dict[str, Any]:
        return {
            "id": self.asset_id,
            "purpose": self.purpose,
            "runtime_path": self.runtime_path,
            "runtime_format": self.runtime_format,
            "width": self.width,
            "height": self.height,
            "runtime_bytes": self.runtime_bytes,
            "runtime_sha256": self.runtime_sha256,
            "fallback": self.fallback,
            "generation_mode": self.generation_mode,
            "source_page_url": self.source_page_url,
            "license": self.license,
            "attribution": self.attribution,
        }


def native_visual_asset_plan() -> dict[str, Any]:
    return {
        "schema_version": "mpos-visual-asset-plan-v1",
        "decision_mode": "automatic",
        "render_strategy": "lvgl_native",
        "runtime_byte_budget": MAX_APP_RUNTIME_BYTES,
        "assets": [],
        "lvgl_elements": [],
    }


def _asset_id(prompt: str) -> str:
    if any(term in prompt.casefold() for term in ("logo", "徽标", "标志")):
        return "brand_artwork"
    if any(term in prompt.casefold() for term in ("背景", "background", "纹理", "texture")):
        return "scene_background"
    if any(term in prompt.casefold() for term in ("精灵", "sprite", "角色", "character")):
        return "character_artwork"
    return "hero_artwork"


def heuristic_visual_asset_plan(
    prompt: str,
    *,
    allow_web: bool,
) -> dict[str, Any]:
    normalized = prompt.casefold()
    if any(term in normalized for term in ("不要图片", "禁用图片", "no images", "lvgl only")):
        return native_visual_asset_plan()
    visual_terms = (
        "插画", "背景", "纹理", "精灵", "角色", "logo", "徽标", "图片",
        "主题", "皮卡丘", "海绵宝宝", "illustration", "background", "texture",
        "sprite", "character", "artwork", "image", "pikachu", "spongebob",
    )
    if not any(term in normalized for term in visual_terms):
        return native_visual_asset_plan()
    recognizable_terms = (
        "皮卡丘", "海绵宝宝", "logo", "徽标", "品牌", "产品", "名人", "meme",
        "pikachu", "spongebob", "official", "brand", "product",
    )
    generation_mode = (
        "web" if allow_web and any(term in normalized for term in recognizable_terms)
        else "procedural"
    )
    asset_id = _asset_id(prompt)
    asset = {
        "id": asset_id,
        "purpose": "decorative_static_artwork",
        "reason": "The request calls for recognizable or visually rich static artwork.",
        "required": False,
        "dynamic": False,
        "interactive": False,
        "contains_text": False,
        "width": 160,
        "height": 120,
        "transparent": "背景" not in normalized and "background" not in normalized,
        "generation_mode": generation_mode,
        "fallback": "Show a native LVGL card with a simple geometric motif.",
    }
    if generation_mode == "web":
        asset["search_query"] = f"{prompt[:160]} reusable artwork transparent"
    return {
        "schema_version": "mpos-visual-asset-plan-v1",
        "decision_mode": "automatic",
        "render_strategy": "hybrid",
        "runtime_byte_budget": MAX_APP_RUNTIME_BYTES,
        "assets": [asset],
        "lvgl_elements": ["native_controls", "dynamic_text", "focus_state"],
    }


async def analyze_visual_asset_plan(
    prompt: str,
    *,
    allow_web: bool,
    allow_external: bool = False,
) -> dict[str, Any]:
    """Ask the configured model for a semantic plan, with a deterministic fallback."""
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key or key == "replace_with_your_deepseek_api_key":
        return heuristic_visual_asset_plan(prompt, allow_web=allow_web)
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
    system = (
        "Analyze visual implementation for a MicroPythonOS LVGL App. Return JSON only with "
        "one visual_asset_plan matching mpos-visual-asset-plan-v1. Automatically choose "
        "lvgl_native, raster_asset, or hybrid. Keep controls, text, focus, live data, and "
        "simple geometry native. Raster assets must be static, non-interactive, contain no "
        "text, use lowercase snake_case ids, include purpose/reason/required/dynamic/interactive/"
        "contains_text/width/height/transparent/generation_mode/fallback, and stay within a "
        "1048576 runtime byte budget. Use procedural for original generic art. Use web with a "
        "specific search_query only for a recognizable named subject when allow_web is true. "
        "Never use uploaded. Use external only when allow_external is true."
    )
    context = {
        "prompt": prompt,
        "allow_web": allow_web,
        "allow_external": allow_external,
        "runtime_byte_budget": MAX_APP_RUNTIME_BYTES,
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
        "max_tokens": 2200,
        "thinking": {"type": "disabled"},
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
            )
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
        raw = message.get("content") or message.get("reasoning_content") or ""
        parsed = json.loads(raw)
        plan = parsed.get("visual_asset_plan", parsed)
        if not isinstance(plan, dict):
            raise ValueError("visual plan is not an object")
        return plan
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return heuristic_visual_asset_plan(prompt, allow_web=allow_web)


def procedural_spec(asset: dict[str, Any], prompt: str) -> dict[str, Any]:
    seed = hashlib.sha256((prompt + str(asset["id"])).encode("utf-8")).digest()
    color_a = f"#{seed[0]:02x}{seed[1]:02x}{seed[2]:02x}"
    color_b = f"#{seed[3]:02x}{seed[4]:02x}{seed[5]:02x}"
    accent = f"#{seed[6]:02x}{seed[7]:02x}{seed[8]:02x}ff"
    width = int(asset["width"])
    height = int(asset["height"])
    transparent = bool(asset.get("transparent"))
    shapes: list[dict[str, Any]] = []
    if not transparent:
        shapes.append({
            "type": "gradient", "x": 0, "y": 0, "width": width, "height": height,
            "start_color": color_a, "end_color": color_b, "direction": "vertical",
        })
    shapes.extend([
        {
            "type": "circle", "cx": width // 2, "cy": height // 2,
            "radius": max(4, min(width, height) // 4), "color": accent,
        },
        {
            "type": "polygon",
            "points": [
                [width // 2, max(0, height // 8)],
                [max(0, width * 7 // 8), height * 3 // 4],
                [max(0, width // 8), height * 3 // 4],
            ],
            "color": f"#{seed[9]:02x}{seed[10]:02x}{seed[11]:02x}cc",
        },
    ])
    return {
        "schema_version": "mpos-visual-asset-spec-v1",
        "id": asset["id"],
        "width": width,
        "height": height,
        "background": "#00000000" if transparent else color_a,
        "supersample": 2,
        "shapes": shapes,
        "runtime_format": "auto",
    }


def _safe_public_ip(hostname: str) -> None:
    try:
        records = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise VisualAssetError("VISUAL_ASSET_FETCH_FAILED", "Image host could not be resolved", owner="external") from exc
    for record in records:
        address = ipaddress.ip_address(record[4][0].split("%", 1)[0])
        if not address.is_global:
            raise VisualAssetError(
                "VISUAL_ASSET_FETCH_FAILED",
                "Image URL resolves to a non-public address",
                owner="external",
                details={"host": hostname},
            )


async def _safe_get(url: str, *, max_bytes: int = MAX_DOWNLOAD_BYTES) -> tuple[bytes, str, str]:
    current = url
    async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5), follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            parsed = urlparse(current)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise VisualAssetError("VISUAL_ASSET_FETCH_FAILED", "Only credential-free HTTPS image URLs are allowed", owner="external")
            _safe_public_ip(parsed.hostname)
            async with client.stream("GET", current, headers={"Accept": "image/png,image/jpeg,image/webp"}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise VisualAssetError("VISUAL_ASSET_FETCH_FAILED", "Image redirect has no location", owner="external")
                    current = urljoin(current, location)
                    continue
                try:
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    raise VisualAssetError(
                        "VISUAL_ASSET_FETCH_FAILED",
                        f"Image host returned HTTP {response.status_code}",
                        owner="external",
                    ) from exc
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type not in {"image/png", "image/jpeg", "image/webp"}:
                    raise VisualAssetError("VISUAL_ASSET_FETCH_FAILED", "Remote response is not a supported image", owner="external")
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > max_bytes:
                        raise VisualAssetError("VISUAL_ASSET_FETCH_FAILED", "Remote image exceeds byte budget", owner="external")
                return bytes(chunks), content_type, current
        raise VisualAssetError("VISUAL_ASSET_FETCH_FAILED", "Remote image exceeded redirect limit", owner="external")


def _license_is_reusable(value: str) -> bool:
    normalized = re.sub(r"<[^>]+>", " ", value or "").casefold()
    return (
        any(marker in normalized for marker in SAFE_LICENSE_MARKERS)
        and not any(marker in normalized for marker in RESTRICTED_LICENSE_MARKERS)
    )


async def search_wikimedia_image(query: str) -> dict[str, Any]:
    params = {
        "action": "query",
        "format": "json",
        "origin": "*",
        "generator": "search",
        "gsrnamespace": "6",
        "gsrsearch": query,
        "gsrlimit": "8",
        "prop": "imageinfo|info",
        "inprop": "url",
        "iiprop": "url|mime|size|extmetadata",
        "iiurlwidth": "1024",
    }
    endpoint = "https://commons.wikimedia.org/w/api.php"
    _safe_public_ip("commons.wikimedia.org")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5)) as client:
            response = await client.get(endpoint, params=params, headers={"Accept": "application/json"})
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", {})
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        raise VisualAssetError("VISUAL_ASSET_SEARCH_FAILED", "Web image search failed", owner="external") from exc
    for page in pages.values() if isinstance(pages, dict) else []:
        info = (page.get("imageinfo") or [{}])[0]
        metadata = info.get("extmetadata") or {}
        license_name = str((metadata.get("LicenseShortName") or {}).get("value") or "")
        usage_terms = str((metadata.get("UsageTerms") or {}).get("value") or "")
        if not _license_is_reusable(f"{license_name} {usage_terms}"):
            continue
        image_url = str(info.get("thumburl") or info.get("url") or "")
        source_page = str(page.get("fullurl") or info.get("descriptionurl") or "")
        if not image_url.startswith("https://") or not source_page.startswith("https://"):
            continue
        return {
            "source_page_url": source_page,
            "image_url": image_url,
            "license": license_name or usage_terms,
            "license_url": str((metadata.get("LicenseUrl") or {}).get("value") or ""),
            "attribution": str((metadata.get("Artist") or {}).get("value") or ""),
        }
    raise VisualAssetError(
        "VISUAL_ASSET_RIGHTS_UNVERIFIED",
        "No reusable Web image with verifiable redistribution rights was found",
        owner="external",
        retryable=False,
    )


def _pillow_image(raw: bytes) -> Any:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise VisualAssetError(
            "VISUAL_ASSET_TOOLCHAIN_MISSING",
            "Pillow is required for trusted Web image decoding",
            owner="toolchain",
        ) from exc
    try:
        image = Image.open(BytesIO(raw))
        image.verify()
        image = Image.open(BytesIO(raw))
        image = ImageOps.exif_transpose(image)
        if image.width * image.height > MAX_DECODED_PIXELS:
            raise VisualAssetError("VISUAL_ASSET_FETCH_FAILED", "Decoded image exceeds pixel budget", owner="external")
        return image.convert("RGBA")
    except VisualAssetError:
        raise
    except Exception as exc:
        raise VisualAssetError("VISUAL_ASSET_FETCH_FAILED", "Downloaded bytes are not a valid image", owner="external") from exc


def _lvgl_bytes(image: Any, transparent: bool) -> tuple[bytes, str]:
    pixels = image.tobytes()
    has_alpha = transparent and any(pixels[index] != 255 for index in range(3, len(pixels), 4))
    selected = "RGB565A8" if has_alpha else "RGB565"
    rgb = bytearray()
    alpha = bytearray()
    for index in range(0, len(pixels), 4):
        red, green, blue, opacity = pixels[index:index + 4]
        color = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
        rgb.extend(struct.pack("<H", color))
        if has_alpha:
            alpha.append(opacity)
    stride = image.width * 2
    header = struct.pack("<BBHHHHH", 0x19, 0x14 if has_alpha else 0x12, 0, image.width, image.height, stride, 0)
    return header + bytes(rgb + alpha), selected


async def build_web_asset(
    asset: dict[str, Any],
    *,
    query: str,
    preview_path: Path,
    runtime_path: Path,
    metadata_path: Path,
    source_record_path: Path,
) -> BuiltVisualAsset:
    selected = await search_wikimedia_image(query)
    raw, mime, resolved_url = await _safe_get(selected["image_url"])
    image = _pillow_image(raw)
    image.thumbnail((int(asset["width"]), int(asset["height"])))
    if image.width <= 0 or image.height <= 0:
        raise VisualAssetError("VISUAL_ASSET_FETCH_FAILED", "Decoded image has invalid dimensions", owner="external")
    clean = BytesIO()
    image.save(clean, format="PNG", optimize=True)
    clean_bytes = clean.getvalue()
    runtime, runtime_format = _lvgl_bytes(image, bool(asset.get("transparent")))
    if len(runtime) > MAX_APP_RUNTIME_BYTES:
        raise VisualAssetError("VISUAL_ASSET_BUDGET_EXCEEDED", "Runtime image exceeds byte budget", owner="app")
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_bytes(clean_bytes)
    runtime_path.write_bytes(runtime)
    runtime_hash = hashlib.sha256(runtime).hexdigest()
    source_record = {
        "schema_version": "mpos-visual-asset-source-v1",
        "asset_id": asset["id"],
        "search_query": query,
        "source_page_url": selected["source_page_url"],
        "image_url": selected["image_url"],
        "resolved_image_url": resolved_url,
        "source_domain": urlparse(selected["source_page_url"]).hostname,
        "license": selected["license"],
        "license_url": selected["license_url"],
        "attribution": selected["attribution"],
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "mime": mime,
        "size": len(raw),
        "width": image.width,
        "height": image.height,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    source_record_path.write_text(json.dumps(source_record, ensure_ascii=False, indent=2), encoding="utf-8")
    metadata = {
        "schema_version": "mpos-visual-asset-build-v1",
        "id": asset["id"],
        "width": image.width,
        "height": image.height,
        "runtime_format": runtime_format,
        "preview_sha256": hashlib.sha256(clean_bytes).hexdigest(),
        "runtime_sha256": runtime_hash,
        "preview_bytes": len(clean_bytes),
        "runtime_bytes": len(runtime),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return BuiltVisualAsset(
        asset_id=asset["id"], purpose=asset["purpose"],
        runtime_path=f"assets/images/{asset['id']}.bin", preview_path=preview_path,
        runtime_file=runtime_path, metadata_path=metadata_path,
        source_record_path=source_record_path, runtime_format=runtime_format,
        width=image.width, height=image.height, runtime_bytes=len(runtime),
        runtime_sha256=runtime_hash, fallback=asset["fallback"], generation_mode="web",
        source_page_url=selected["source_page_url"], license=selected["license"],
        attribution=selected["attribution"],
    )


def binary_file_payload(asset: BuiltVisualAsset) -> dict[str, Any]:
    return {
        "path": asset.runtime_path,
        "encoding": "base64",
        "content_base64": base64.b64encode(asset.runtime_file.read_bytes()).decode("ascii"),
        "mime": "application/octet-stream",
        "role": "app_runtime_image",
        "sha256": asset.runtime_sha256,
    }
