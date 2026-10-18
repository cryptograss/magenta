"""Whose faces are in a picture sent into a Mood: asked of the faces service on hunter
(memory-lane faces/), offered as names, and named by a person.

faces finds the faces and says, for each, whose it looks like -- by the reference
photos people mark on PickiPedia ({{Face reference|Person}}). That is only ever an
offer: a likeness above faces' line is often right and sometimes not (a small face
at an angle can pass for someone else), so nothing is called anyone's until someone
here says so. Who said so, and when, is kept (FaceName), and their names go with the
picture when it goes up to PickiPedia ({{Depicts|...}}, services/wiki_upload.py).

The picture is asked about once, when someone presses "Who's in it?"; the answer is
kept (FaceLook), with the model that gave it, so opening it again asks nobody. Unset
FACES_URL or FACES_KEY: there's no button, and nothing is sent anywhere.
"""

import logging

from django.conf import settings
from django.utils import timezone

log = logging.getLogger(__name__)

TIMEOUT = 30  # seconds: a phone's photo, looked at on hunter's two CPUs
MAX_NAME = 120
# What faces can read (its engine decodes JPEG, PNG and WebP); a GIF or a HEIC original isn't asked about.
READABLE = ('image/jpeg', 'image/png', 'image/webp')


class FacesError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def enabled():
    return bool(_url() and getattr(settings, 'FACES_KEY', ''))


def _url():
    return (getattr(settings, 'FACES_URL', '') or '').rstrip('/')


def _post(data, http=None):
    import requests
    try:
        r = (http or requests).post(f'{_url()}/match', data=data, timeout=TIMEOUT,
                                    headers={'Authorization': f'Bearer {settings.FACES_KEY}',
                                             'Content-Type': 'application/octet-stream'})
    except Exception as e:  # requests' own errors, or a test's
        log.warning('faces: %s', e)
        raise FacesError("the faces service didn't answer; try again in a minute")
    if r.status_code == 401:
        log.error('faces refused our key: FACES_KEY here and on hunter differ')
        raise FacesError("the faces service didn't take magenta's key", status=503)
    if r.status_code != 200:
        raise FacesError(f'the faces service said {r.status_code}')
    return r.json()


def look(media, http=None):
    """The faces in a picture, asked of the faces service (once: after that, as kept). The FaceLook."""
    from conversations.models import FaceLook
    kept = FaceLook.objects.filter(media=media).first()
    if kept is not None:
        return kept
    if not enabled():
        raise FacesError("finding faces isn't set up here", status=503)
    if media.mime not in READABLE:
        raise FacesError('only photos (JPEG, PNG, WebP) can be asked about', status=400)
    said = _post(bytes(media.data), http)
    faces = [{'box': [int(v) for v in f['box']][:4],
              'match': _who(f.get('match')),
              'candidates': [c for c in (_who(c) for c in f.get('candidates') or []) if c][:3]}
             for f in said.get('faces') or [] if len(f.get('box') or []) == 4]
    look, _ = FaceLook.objects.update_or_create(media=media, defaults={
        'model': str(said.get('model') or '')[:80], 'width': int(said.get('width') or 0),
        'height': int(said.get('height') or 0), 'faces': faces})
    return look


def _who(m):
    """A match or candidate as kept: {'person', 'similarity'}; None if it names nobody."""
    if not isinstance(m, dict) or not str(m.get('person') or '').strip():
        return None
    return {'person': str(m['person']).strip()[:MAX_NAME], 'similarity': round(float(m.get('similarity') or 0), 3)}


def name(media, box, person, by):
    """Say whose face the one at `box` is (person ''/None: take that back). Only a face faces found."""
    from conversations.models import FaceLook, FaceName
    look = FaceLook.objects.filter(media=media).first()
    try:
        box = [int(v) for v in box]
    except (TypeError, ValueError):
        raise FacesError('which face?', status=400)
    if look is None or box not in [f['box'] for f in look.faces]:
        raise FacesError('no such face in that picture', status=404)
    person = ' '.join(str(person or '').replace('_', ' ').split())[:MAX_NAME]
    if person.startswith(('[[', '{{')) or any(c in person for c in '[]{}|#<>'):
        raise FacesError('a name, as its PickiPedia page is titled', status=400)
    if not person:
        FaceName.objects.filter(media=media, box=box).delete()
        return None
    person = person[:1].upper() + person[1:]  # as PickiPedia titles it
    named, _ = FaceName.objects.update_or_create(media=media, box=box, defaults={
        'person': person, 'by_id': getattr(by, 'pk', by), 'at': timezone.now()})
    return named


def described(media):
    """What's known of a picture's faces, for the page: {'looked', 'width', 'height', 'faces': [{box,
    match, candidates, named: {person, by, at} | None}]}."""
    from conversations.models import FaceLook, FaceName
    look = FaceLook.objects.filter(media=media).first()
    if look is None:
        return {'looked': False, 'can_look': enabled() and media.mime in READABLE}
    named = {tuple(n.box): n for n in FaceName.objects.filter(media=media)}
    faces = []
    for f in look.faces:
        n = named.get(tuple(f['box']))
        faces.append({**f, 'named': {'person': n.person, 'by': n.by_id, 'at': n.at.isoformat()} if n else None})
    return {'looked': True, 'model': look.model, 'width': look.width, 'height': look.height, 'faces': faces}


def people_in(media):
    """Whom people here have named in a picture, left to right, each once."""
    from conversations.models import FaceName
    seen = []
    for n in sorted(FaceName.objects.filter(media=media), key=lambda n: n.box[0]):
        if n.person not in seen:
            seen.append(n.person)
    return seen
