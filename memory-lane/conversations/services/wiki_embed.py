"""A magenta message as PickiPedia shows it: a call to Template:Magenta message, words and all (Justin, 25 Oct:
"a really nice way to embed magenta messages on pickipedia ... some number of characters of the message,
tuneable in the template ... and the mood and blockheight / datetime ... click to go to the message").

The words travel in the call itself, so the card reads on its own after the message is edited or gone, and
the wiki never has to ask magenta. Every character wikitext would read as markup is written as an entity
(& < > { } | [ ] ~ =), so a message's own pipes, braces, links or tildes can neither break the call nor turn
into anything; Module:Magenta message decodes them and shows the words exactly as said.
"""

from .mood_view import prose, reply_to

_ENTITIES = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '{': '&#123;', '}': '&#125;', '|': '&#124;',
             '[': '&#91;', ']': '&#93;', '~': '&#126;', '=': '&#61;'}


def escaped(text):
    return ''.join(_ENTITIES.get(c, c) for c in text)


def words_of(message):
    """What the message says, without a reply's ↩ line."""
    return reply_to(prose(message.content) or '')[1].strip()


def template_call(message, chars=None):
    """{{Magenta message|...}} for `message`; `chars`: how many characters to show ('all', a number, or the
    template's own default when None)."""
    from .wiki_auth import names
    sender = message.sender_id
    fields = [('id', str(message.id)), ('who', sender)]
    if names().get(sender):
        fields.append(('user', names()[sender]))
    if message.mood_id:
        fields.append(('mood', message.mood.slug))
    fields.append(('at', message.created_at.strftime('%Y-%m-%dT%H:%M:%SZ')))
    if message.eth_blockheight:
        fields.append(('block', str(message.eth_blockheight)))
    if chars is not None:
        fields.append(('chars', str(chars)))
    fields.append(('text', escaped(words_of(message))))
    return '{{Magenta message\n' + ''.join(f'|{k}={v}\n' for k, v in fields) + '}}'
