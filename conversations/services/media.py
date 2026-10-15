"""Images in Moods: recognised by their bytes, stored once by hash.

Three ways in, one store (models.Media):
  - a person pastes, drops or attaches one in the composer (views_auth.api_media);
  - a person pastes one into a terminal session, and the transcript line
    carries it as a base64 image block;
  - a tool returns one -- a screenshot, an image the agent read.

The importer lifts image blocks out of each line before anything else
reads it (lift_images), storing the bytes and leaving markdown in their
place, ![image](/moods/media/<sha256>.<ext>). So the record's text stays
text, an image pasted twice is stored once, and every view already knows
how to show a link.

Only PNG, JPEG, GIF and WebP, decided by their first bytes. SVG is never
accepted: it can carry script. A HEIC/HEIF photo -- what an iPhone's camera
takes -- is turned into a JPEG on the way in (jpeg_from_heif), since most
browsers can't show HEIC: upright, at most MAX_EDGE on its long side, and
without its metadata, so a phone photo's GPS position isn't published. A
JPEG or PNG loses its metadata the same way (without_metadata): EXIF, XMP
and text chunks are cut out of the file, the picture itself untouched --
unless its sharer unticks "remove location" as they attach it (strip=False;
Justin and Sky, 15 Oct: tour photos want it gone, the odd one may not).
Unticked, a HEIC photo's EXIF and XMP are carried into its JPEG. Audio -- voice memos, messages read aloud --
only where it's asked for (store(..., audio=True)), never lifted from a
transcript.
"""

import base64
import binascii
import hashlib
import io
import json
import re

MAX_BYTES = 8 * 1024 * 1024
# What a picture's sharer lets others do with it: CC BY-SA 4.0 unless they
# say CC0, told so where they attach it (models.Media.license). It goes with
# the picture to PickiPedia (services/wiki_upload.py).
LICENSES = {'cc-by-sa-4.0': 'CC BY-SA 4.0', 'cc0': 'CC0'}
MAX_AUDIO_BYTES = 25 * 1024 * 1024
# The ftyp brands of HEIC/HEIF stills (ISO/IEC 23008-12): heic is what an
# iPhone writes; mif1/msf1 are the general HEIF image brands.
HEIF_BRANDS = {b'heic', b'heix', b'heim', b'heis', b'hevc', b'hevx', b'mif1', b'msf1'}
# A converted photo's longest side, in pixels: a 48 MP phone photo would make
# a JPEG bigger than anything else stored here, for no gain on a screen.
MAX_EDGE = 4096

_SIGNATURES = (
    (b'\x89PNG\r\n\x1a\n', 'image/png'),
    (b'\xff\xd8\xff', 'image/jpeg'),
    (b'GIF87a', 'image/gif'),
    (b'GIF89a', 'image/gif'),
)
# /motions/media/ in what was stored before Moods were Moods (October 2026): still found, still served.
MEDIA_PATH = re.compile(r'/(?:moods|motions)/media/([0-9a-f]{64})\.(png|jpg|gif|webp)')
AUDIO_PATH = re.compile(r'/(?:moods|motions)/media/([0-9a-f]{64})\.(webm|ogg|m4a|mp3|wav)')


def sniff_audio(data):
    """The audio type these bytes really are, or None: what browsers record
    (WebM, Ogg, MP4/AAC on Safari) and what a voice comes back as (MP3)."""
    if data.startswith(b'\x1a\x45\xdf\xa3'):
        return 'audio/webm'
    if data.startswith(b'OggS'):
        return 'audio/ogg'
    if data[4:8] == b'ftyp':
        return 'audio/mp4'
    if data.startswith(b'ID3') or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return 'audio/mpeg'
    if data[:4] == b'RIFF' and data[8:12] == b'WAVE':
        return 'audio/wav'
    return None


def sniff(data):
    """The image type these bytes really are, or None."""
    for signature, mime in _SIGNATURES:
        if data.startswith(signature):
            return mime
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


def is_heif(data):
    """A HEIC/HEIF still image, by its ftyp box."""
    return data[4:8] == b'ftyp' and data[8:12] in HEIF_BRANDS


def jpeg_from_heif(data, keep=False):
    """JPEG bytes for a HEIC/HEIF photo, or None if it can't be read.

    Upright (libheif applies the photo's rotation; exif_transpose catches an
    EXIF one), no larger than MAX_EDGE, and with no metadata carried over --
    unless keep: then its EXIF (location, camera, time) and XMP go into the
    JPEG, less the rotation tag, which the turned picture no longer needs.
    Encoding is deterministic, so the same photo sent twice is stored once.
    """
    try:
        import pillow_heif
        from PIL import Image, ImageOps
        pillow_heif.register_heif_opener()
        with Image.open(io.BytesIO(data)) as opened:
            picture = ImageOps.exif_transpose(opened)
            if picture.mode not in ('RGB', 'L'):
                picture = picture.convert('RGB')
            kept = {}
            if keep:
                exif = picture.getexif()  # exif_transpose has dropped the rotation tag
                exif.pop(0x0112, None)
                if len(exif) or exif.get_ifd(0x8825):
                    kept['exif'] = exif.tobytes()
                if opened.info.get('xmp'):
                    kept['xmp'] = opened.info['xmp']
            picture.thumbnail((MAX_EDGE, MAX_EDGE))
            out = io.BytesIO()
            picture.save(out, 'JPEG', quality=88, optimize=True, **kept)
            return out.getvalue()
    except Exception:
        return None


def _jpeg_orientation(data):
    """The EXIF orientation of a JPEG (1 is upright), or 1 if unreadable."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as opened:
            return opened.getexif().get(0x0112, 1) or 1
    except Exception:
        return 1


def _jpeg_without_metadata(data):
    """The JPEG with its APP1-APP15 and comment segments cut out: EXIF (GPS,
    camera, time), XMP, Photoshop/IPTC. APP0 (JFIF) stays; the image data is
    copied byte for byte. None if the segments don't parse."""
    out, i = bytearray(data[:2]), 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker == 0xDA:  # start of scan: the rest is the picture
            return bytes(out + data[i:])
        length = int.from_bytes(data[i + 2:i + 4], 'big')
        if length < 2:
            return None
        if not (0xE1 <= marker <= 0xEF or marker == 0xFE):
            out += data[i:i + 2 + length]
        i += 2 + length
    return None


# PNG chunks that carry metadata, never the picture: EXIF, text (where XMP lives too), time.
_PNG_METADATA = {b'eXIf', b'tEXt', b'iTXt', b'zTXt', b'tIME'}


def _png_without_metadata(data):
    """The PNG with its metadata chunks dropped, the rest copied as is; None if it doesn't parse."""
    out, i = bytearray(data[:8]), 8
    while i + 12 <= len(data):
        length = int.from_bytes(data[i:i + 4], 'big')
        kind = data[i + 4:i + 8]
        end = i + 12 + length
        if end > len(data):
            return None
        if kind not in _PNG_METADATA:
            out += data[i:end]
        i = end
        if kind == b'IEND':
            return bytes(out)
    return None


def without_metadata(data, mime):
    """The picture as stored: a JPEG or PNG without where and when it was
    taken, or by what. A JPEG turned by its EXIF is re-encoded upright first
    (jpeg_from_heif does that, and any format Pillow reads), since cutting the
    EXIF would otherwise leave it on its side. Anything else, or anything that
    doesn't parse, as it came."""
    if mime == 'image/jpeg':
        if _jpeg_orientation(data) != 1:
            upright = jpeg_from_heif(data)
            if upright:
                return upright
        return _jpeg_without_metadata(data) or data
    if mime == 'image/png':
        return _png_without_metadata(data) or data
    return data


_TIFF_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
_XMP_GPS = re.compile(rb'(exif:GPS\w+)(="[^"]*"|>[^<]*<)')


def heic_without_location(data):
    """The HEIC with its GPS blanked in place, every other byte as it came; None if it can't be done.

    Re-encoding a HEIC would cost the very fidelity the original is kept for,
    so the EXIF GPS directory is emptied where it lies: its values zeroed,
    its entry count set to none. XMP GPS fields are blanked to spaces. The
    file keeps its length, so nothing that points into it moves.
    """
    try:
        import pillow_heif
        exif = pillow_heif.open_heif(data).info.get('exif')
    except Exception:
        return None
    out = bytearray(data)
    tiff = -1
    if exif:  # libheif's copy of the EXIF item: where it lies in the file is where to blank it
        body = exif[6:] if exif.startswith(b'Exif\x00\x00') else exif
        tiff = out.find(body)
        if tiff < 0:
            return None
    order = {b'II': 'little', b'MM': 'big'}.get(bytes(out[tiff:tiff + 2])) if tiff >= 0 else None
    if tiff >= 0 and order is None:
        return None

    def num(offset, size):
        if offset < 0 or offset + size > len(out):
            raise ValueError
        return int.from_bytes(out[offset:offset + size], order)

    try:
        ifd0 = tiff + num(tiff + 4, 4) if order else None
        gps = None
        for i in range(num(ifd0, 2) if order else 0):
            entry = ifd0 + 2 + 12 * i
            if num(entry, 2) == 0x8825:
                gps = tiff + num(entry + 8, 4)
        if gps is not None:
            count = num(gps, 2)
            for i in range(count):
                entry = gps + 2 + 12 * i
                size = _TIFF_TYPE_SIZES.get(num(entry + 2, 2), 1) * num(entry + 4, 4)
                if size > 4:
                    where = tiff + num(entry + 8, 4)
                    num(where, size)  # in bounds
                    out[where:where + size] = bytes(size)
                out[entry:entry + 12] = bytes(12)
            out[gps:gps + 2] = bytes(2)
    except ValueError:
        return None
    out = bytearray(_XMP_GPS.sub(lambda m: m.group(1) + m.group(2)[:2] + b' ' * (len(m.group(2)) - 3)
                                 + m.group(2)[-1:], bytes(out)))
    return bytes(out)


def store(data, added_by=None, audio=False, strip=True):
    """The Media for these bytes, stored if new; None if not an image (or,
    with audio=True, a sound) we take. A HEIC photo is stored as a JPEG.
    A JPEG or PNG is stored without its metadata unless strip=False;
    a HEIC photo's then comes with it into the JPEG."""
    from conversations.models import Media

    if not data or len(data) > (MAX_AUDIO_BYTES if audio else MAX_BYTES):
        return None
    original = None
    if not audio and is_heif(data):
        sent = data
        data = jpeg_from_heif(data, keep=not strip)
        if data is None:
            return None
        original = _keep_original(sent if not strip else heic_without_location(sent), added_by)
    mime = sniff_audio(data) if audio else sniff(data)
    if mime is None:
        return None
    if not audio and strip:
        data = without_metadata(data, mime)
    sha = hashlib.sha256(data).hexdigest()
    media, _ = Media.objects.get_or_create(
        sha256=sha, defaults={'mime': mime, 'data': data, 'size': len(data), 'added_by': added_by,
                              'original': original})
    if original is not None and media.original_id is None:
        media.original = original
        media.save(update_fields=['original'])
    return media


def _keep_original(data, added_by):
    """The HEIC as sent (its location blanked if asked), stored beside its JPEG; None if it can't be.
    Not shown in a Mood (browsers can't), but there to download whole."""
    from conversations.models import Media
    if not data:
        return None  # couldn't blank its location without re-encoding: only the JPEG is kept
    kept, _ = Media.objects.get_or_create(
        sha256=hashlib.sha256(data).hexdigest(),
        defaults={'mime': 'image/heic', 'data': data, 'size': len(data), 'added_by': added_by})
    return kept


def relicense(media, license, by):
    """Set a picture's license (LICENSES): only whoever shared it may. True if it's theirs to set."""
    if license not in LICENSES or by is None or media.added_by_id != getattr(by, 'pk', by):
        return False
    if media.license != license:
        type(media).objects.filter(pk=media.pk).update(license=license)
        media.license = license
    if media.original_id:  # the photo as sent goes out under the same terms as what it's shown as
        type(media).objects.filter(pk=media.original_id, added_by_id=media.added_by_id).update(license=license)
    return True


def store_base64(text, added_by=None):
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError, TypeError):
        return None
    return store(data, added_by)


def markdown(media, alt='image'):
    """The picture, and a link to the photo as sent where one was kept (a HEIC's original)."""
    shown = f'![{alt}]({media.url})'
    original = getattr(media, 'original', None)
    return f'{shown} [original HEIC]({original.url})' if original else shown


def _image_bytes(block):
    """base64 text of an image block, in Anthropic's shape or MCP's."""
    if not isinstance(block, dict) or block.get('type') != 'image':
        return None
    source = block.get('source')
    if isinstance(source, dict) and source.get('type') == 'base64':
        return source.get('data')
    if isinstance(block.get('data'), str):  # MCP: {"type": "image", "data": ..., "mimeType": ...}
        return block['data']
    return None


def lift_images(line, person=None):
    """A transcript line with every image block stored and replaced by markdown.

    `person` is credited with images in a message they typed (pasted into a
    terminal); images in tool results are credited to nobody.
    """
    if '"image"' not in line:
        return line
    try:
        event = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return line
    message = event.get('message') if isinstance(event, dict) else None
    typed = (isinstance(message, dict) and message.get('role') == 'user'
             and not any(isinstance(b, dict) and b.get('type') == 'tool_result'
                         for b in (message.get('content') or []) if isinstance(message.get('content'), list)))
    # A tool's images are tool output: kept only when tool output is
    # (settings.TOOL_RESULT_CONTENT_CHARS), like its text.
    from django.conf import settings
    keep_tool_images = settings.TOOL_RESULT_CONTENT_CHARS > 0
    found = False

    def walk(value):
        nonlocal found
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, dict):
            data = _image_bytes(value)
            if data is not None:
                found = True
                keep = typed or keep_tool_images
                media = store_base64(data, person if typed else None) if keep else None
                return {'type': 'text', 'text': markdown(media) if media else '[image omitted]'}
            return {k: walk(v) for k, v in value.items()}
        return value

    event = walk(event)
    return json.dumps(event) if found else line


def urls_in(text):
    """Media URLs a text refers to, in order, without repeats."""
    seen, out = set(), []
    for match in MEDIA_PATH.finditer(text or ''):
        url = match.group(0)
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out
