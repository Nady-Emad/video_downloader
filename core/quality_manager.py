"""Central quality-selection logic. Every engine asks this module what to download."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from engines.base_engine import FormatInfo, MediaInfo

# (label, key). Keys that are numbers are maximum heights (short side of the frame).
VIDEO_QUALITY_CHOICES: List[Tuple[str, str]] = [
    ("Best Available", "best"),
    ("8K (4320p)", "4320"),
    ("4K (2160p)", "2160"),
    ("1440p (2K)", "1440"),
    ("1080p (Full HD)", "1080"),
    ("720p (HD)", "720"),
    ("480p (SD)", "480"),
    ("360p", "360"),
    ("240p", "240"),
    ("144p", "144"),
    ("Audio Only (MP3)", "audio"),
    ("Custom Quality", "custom"),
]
HEIGHT_KEYS = [int(k) for _, k in VIDEO_QUALITY_CHOICES if k.isdigit()]

VIDEO_FORMAT_CHOICES = [("MP4", "mp4"), ("MKV", "mkv"), ("WEBM", "webm")]

# (label, kbps). 0 = keep the best available audio without re-encoding to a chosen bitrate.
AUDIO_QUALITY_CHOICES: List[Tuple[str, int]] = [
    ("MP3 – 320 kbps", 320),
    ("MP3 – 256 kbps", 256),
    ("MP3 – 192 kbps", 192),
    ("MP3 – 128 kbps", 128),
    ("Best Available Audio", 0),
]

PRIORITY_CHOICES = [("Best quality", "quality"), ("Smaller file size", "size")]
CUSTOM_AUDIO_FORMATS = [("MP3 (FFmpeg)", "mp3"), ("Best available (no conversion)", "best")]


@dataclass
class QualitySpec:
    mode: str = "video"            # video | audio
    max_height: Optional[int] = None
    container: str = "mp4"         # mp4 | mkv | webm
    audio_format: str = "mp3"      # mp3 | best   (used in audio mode / custom)
    audio_bitrate: int = 0         # kbps, 0 = best
    priority: str = "quality"      # quality | size
    key: str = "best"
    label: str = "Best Available"
    codec: str = "auto"            # auto | h264 | vp9 | av1   (preferred video codec)
    compat: bool = True            # compatibility mode: MP4 + H.264 + AAC, transcoding only when needed

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QualitySpec":
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (data or {}).items() if k in allowed})

    @property
    def is_audio(self) -> bool:
        return self.mode == "audio"

    def cache_key(self) -> str:
        return (f"{self.mode}|{self.max_height}|{self.container}|{self.audio_format}|{self.audio_bitrate}|"
                f"{self.priority}|{self.codec}|{self.compat}")


def label_for_key(key: str) -> str:
    for label, k in VIDEO_QUALITY_CHOICES:
        if k == key:
            return label
    return key


def build_spec(video_key: str, container: str, audio_bitrate: int,
               custom: Optional[Dict[str, Any]] = None) -> QualitySpec:
    """Translate the GUI choices into a QualitySpec."""
    container = container if container in ("mp4", "mkv", "webm") else "mp4"
    if video_key == "audio":
        return QualitySpec(mode="audio", container=container, audio_format="mp3",
                           audio_bitrate=audio_bitrate, key="audio", label="Audio Only (MP3)")
    if video_key == "custom":
        c = custom or {}
        height = c.get("max_height")
        fmt = c.get("audio_format", "mp3")
        spec = QualitySpec(
            mode="video",
            max_height=int(height) if height else None,
            container=c.get("container", container),
            audio_format=fmt,
            audio_bitrate=int(c.get("audio_bitrate", audio_bitrate) or 0),
            priority=c.get("priority", "quality"),
            key="custom",
            label=f"Custom ({height}p)" if height else "Custom (best)",
        )
        return spec
    if video_key.isdigit():
        return QualitySpec(mode="video", max_height=int(video_key), container=container,
                           audio_bitrate=audio_bitrate, key=video_key, label=label_for_key(video_key))
    return QualitySpec(mode="video", container=container, audio_bitrate=audio_bitrate,
                       key="best", label="Best Available")


# ----------------------------------------------------------------------------- presets
PRESET_CHOICES = [("Best Quality", "best"), ("Balanced", "balanced"), ("Small File Size", "small"),
                  ("Compatible with Most Players", "compatible"), ("Custom", "custom")]


def preset_spec(preset: str, container: str = "mp4", audio_bitrate: int = 192) -> QualitySpec:
    """One-click quality presets. 'custom' is handled by the manual controls, so it is not built here."""
    if preset == "balanced":          # good picture, moderate size, plays everywhere
        return QualitySpec(mode="video", max_height=1080, container="mp4", audio_bitrate=audio_bitrate,
                           codec="h264", compat=True, key="1080", label="Balanced (1080p)")
    if preset == "small":
        return QualitySpec(mode="video", max_height=480, container="mp4", audio_bitrate=audio_bitrate,
                           priority="size", codec="h264", compat=True, key="480", label="Small File Size (480p)")
    if preset == "compatible":
        return QualitySpec(mode="video", max_height=1080, container="mp4", audio_bitrate=audio_bitrate,
                           codec="h264", compat=True, key="1080", label="Compatible (MP4 H.264 AAC)")
    spec = build_spec("best", container, audio_bitrate)
    spec.compat = False               # best quality: keep whatever the best streams are
    spec.label = "Best Quality"
    return spec


# ----------------------------------------------------------------------------- resolution logic
def available_heights(formats: Sequence[FormatInfo]) -> List[int]:
    return sorted({f.height for f in formats if f.has_video and f.height}, reverse=True)


def pick_height(heights: Sequence[int], max_height: Optional[int]) -> Optional[int]:
    """Highest available height not exceeding max_height. None when nothing qualifies."""
    if not heights:
        return None
    if max_height is None:
        return max(heights)
    candidates = [h for h in heights if h <= max_height]
    return max(candidates) if candidates else None


def enabled_video_keys(formats: Sequence[FormatInfo]) -> Set[str]:
    """Quality keys that should be enabled once formats are known."""
    keys = {"best", "audio", "custom"}
    heights = available_heights(formats)
    for preset in HEIGHT_KEYS:
        if any(preset * 0.9 <= h <= preset for h in heights):
            keys.add(str(preset))
    return keys


# ----------------------------------------------------------------------------- yt-dlp selectors
def ytdlp_format(spec: QualitySpec, ffmpeg_ok: bool) -> str:
    """yt-dlp format expression. Without FFmpeg only single-file (pre-merged) formats are used."""
    if spec.is_audio:
        return "ba/b"
    ext = spec.container
    if ffmpeg_ok:
        if ext == "webm":
            return "bv*[ext=webm]+ba[ext=webm]/b[ext=webm]/bv*+ba/b"
        return "bv*+ba/b"
    if ext in ("mp4", "webm"):
        return f"b[ext={ext}]/b"
    return "b"


def effective_codec(spec: QualitySpec) -> str:
    """Video codec to prefer. MP4 (and compatibility mode) always means H.264: AV1/VP9 inside MP4 is
    what makes many players show a black picture while the audio plays."""
    if spec.compat and spec.container == "mp4":
        return "h264"
    if spec.codec != "auto":
        return spec.codec
    if spec.container == "mp4":
        return "h264"
    if spec.container == "webm":
        return "vp9"
    return "auto"


_VCODEC_SORT = {"h264": "vcodec:h264", "vp9": "vcodec:vp9", "av1": "vcodec:av01"}


def ytdlp_sort(spec: QualitySpec) -> List[str]:
    """yt-dlp format_sort list. `res:N` prefers the best resolution not above N.

    NOTE: never use the bare `codec` field here: yt-dlp ranks it av01 > vp9 > h264, which made MP4
    downloads pick AV1/VP9 video that many players cannot decode (black video, working audio).
    """
    sort: List[str] = [f"res:{spec.max_height}" if spec.max_height else "res"]
    codec = effective_codec(spec)
    if codec in _VCODEC_SORT:
        sort.append(_VCODEC_SORT[codec])
    if codec == "h264" or (spec.container == "mp4"):
        sort.append("acodec:m4a")        # AAC audio plays everywhere; Opus-in-MP4 does not
    elif spec.container == "webm":
        sort.append("acodec:opus")
    if spec.priority == "size":
        sort += ["+size", "+br"]
    else:
        sort += ["fps", "br"]
    return sort


# ----------------------------------------------------------------------------- summary
@dataclass
class SelectionSummary:
    available_resolutions: List[int] = field(default_factory=list)
    selected_resolution: Optional[int] = None
    video_codec: str = "-"
    audio_codec: str = "-"
    estimated_size: Optional[int] = None
    container: str = "mp4"
    needs_ffmpeg: bool = False
    notes: List[str] = field(default_factory=list)
    possible: bool = True


def _short_codec(codec: str) -> str:
    return (codec or "-").split(".")[0] if codec and codec != "none" else "-"


def summarize_selection(info: Optional[MediaInfo], spec: QualitySpec, ffmpeg_ok: bool) -> SelectionSummary:
    """Describe what will be downloaded for `spec` given extracted formats."""
    summary = SelectionSummary(container=spec.container)
    if info is None or not info.formats:
        summary.notes.append("Format details are not available yet; the engine will choose at download time.")
        summary.needs_ffmpeg = not ffmpeg_ok and not spec.is_audio
        return summary

    formats = info.formats
    summary.available_resolutions = available_heights(formats)

    audio_formats = [f for f in formats if f.has_audio and not f.has_video]
    best_audio = max(audio_formats, key=lambda f: (f.abr or 0, f.filesize or 0), default=None)

    if spec.is_audio:
        source = best_audio or max((f for f in formats if f.has_audio), key=lambda f: f.abr or 0, default=None)
        if source is None:
            summary.possible = False
            summary.notes.append("No audio stream found.")
            return summary
        summary.audio_codec = _short_codec(source.acodec)
        summary.estimated_size = source.filesize
        if spec.audio_format == "mp3":
            summary.container = "mp3"
            summary.needs_ffmpeg = True
            if not ffmpeg_ok:
                summary.container = source.ext or "m4a"
                summary.notes.append("FFmpeg missing: MP3 conversion unavailable, the original audio format will be saved.")
        else:
            summary.container = source.ext or "m4a"
        return summary

    target = pick_height(summary.available_resolutions, spec.max_height)
    if target is None:
        summary.possible = False
        lowest = min(summary.available_resolutions) if summary.available_resolutions else None
        summary.notes.append(
            f"No resolution at or below {spec.max_height}p is available"
            + (f" (lowest is {lowest}p)." if lowest else ".")
        )
        return summary

    if spec.max_height and target < spec.max_height:
        summary.notes.append(f"{spec.max_height}p is not available; using the closest lower resolution, {target}p.")

    at_target = [f for f in formats if f.has_video and f.height == target]
    muxed = [f for f in at_target if f.has_audio]
    video_only = [f for f in at_target if not f.has_audio]

    def _rank(f: FormatInfo) -> Tuple[int, float, int]:
        return (1 if f.ext == spec.container else 0, f.fps or 0, f.filesize or 0)

    if ffmpeg_ok and video_only:
        video = max(video_only, key=_rank)
        summary.needs_ffmpeg = True
        summary.video_codec = _short_codec(video.vcodec)
        summary.audio_codec = _short_codec(best_audio.acodec) if best_audio else "-"
        sizes = [video.filesize, best_audio.filesize if best_audio else 0]
        summary.estimated_size = sum(s for s in sizes if s) or None
    elif muxed:
        chosen = max(muxed, key=_rank)
        summary.video_codec = _short_codec(chosen.vcodec)
        summary.audio_codec = _short_codec(chosen.acodec)
        summary.estimated_size = chosen.filesize
        if not ffmpeg_ok and video_only:
            summary.notes.append("FFmpeg missing: using a single-file stream, which may have lower quality.")
    elif video_only and not ffmpeg_ok:
        # Fall back to the best pre-merged stream (any height not above the limit).
        muxed_all = [f for f in formats if f.has_video and f.has_audio and (f.height or 0) <= (spec.max_height or 10 ** 6)]
        if muxed_all:
            chosen = max(muxed_all, key=lambda f: (f.height or 0, f.filesize or 0))
            summary.selected_resolution = chosen.height
            summary.video_codec = _short_codec(chosen.vcodec)
            summary.audio_codec = _short_codec(chosen.acodec)
            summary.estimated_size = chosen.filesize
            summary.notes.append(
                f"FFmpeg missing: only pre-merged streams can be used, so {chosen.height}p is the best possible."
            )
            return summary
        summary.possible = False
        summary.needs_ffmpeg = True
        summary.notes.append("This video only offers separate video/audio streams. Install FFmpeg to download it.")
        return summary

    summary.selected_resolution = target
    if spec.container == "webm" and summary.video_codec.startswith(("avc", "h264")):
        summary.notes.append("WEBM requested but the best stream is H.264; a WEBM-compatible stream will be preferred.")
    return summary


def describe_ffmpeg_requirement(spec: QualitySpec, ffmpeg_ok: bool) -> str:
    if ffmpeg_ok:
        return "FFmpeg detected: merging and MP3 conversion available."
    if spec.is_audio and spec.audio_format == "mp3":
        return "FFmpeg is required for MP3 conversion; the original audio format will be saved instead."
    return "FFmpeg not found: only single-file streams can be downloaded (maximum quality is limited)."
