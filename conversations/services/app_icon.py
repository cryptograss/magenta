"""magenta's icon: the PickiPedia logo's ring of pickers around the campfire,
as a magenta silhouette (File:Magenta logo.png on PickiPedia).

Drawn once, from the logo, into conversations/assets/logo/, and served from
there: the server needs no imaging library. Two kinds:

  icon-<size>.png  the app icon: the silhouette on white, inside the middle
                   76% so a launcher that crops icons to a circle ("maskable")
                   loses none of it. 180 for iOS, 192 and 512 for the rest.
  mark-<size>.png  the silhouette alone, on nothing: the browser tab, the top
                   bar (as a mask, so it takes the theme's magenta), and a
                   notification's badge, which Android draws from its alpha.
"""

import functools
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / 'assets' / 'logo'
ICON_SIZES = (180, 192, 512)
MARK_SIZES = (32, 96, 192)


@functools.lru_cache(maxsize=8)
def png(size, kind='icon'):
    """The PNG's bytes; KeyError for a size there isn't one of."""
    if size not in (ICON_SIZES if kind == 'icon' else MARK_SIZES):
        raise KeyError(size)
    return (ASSETS / f'{kind}-{size}.png').read_bytes()
