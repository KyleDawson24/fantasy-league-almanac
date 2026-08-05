# Privacy

League-member names in this repository's seeds are synthetic stand-ins for
the real league's members; the real data stays on the maintainer's machine
and a private remote. **No contact information -- email, phone, address --
is ever committed, in any form, in any file.**

Platform member GUIDs are retained. They are the identifiers the
identity-resolution layer joins on -- removing them outright would break the
joins the project is built around. They could be swapped for synthetic ids
via a mapping file, but every occurrence across seeds, fixtures, and payload
copies would have to move in lockstep, forever -- a standing tax for little
gain, since the GUIDs are opaque on their own: they resolve to a person only
inside the platform's own private, authenticated league context. So the
committed seeds are name-anonymized, not identifier-anonymized.

The regression corpora that render real names are private by design, and the
tests that need them skip in a public clone rather than fail.

**On names in this repository's history.** The published almanacs and
screenshots use anonymized team and owner names as a courtesy. League
members have been told about the project and asked to speak up if it bothers
them; some have said it's fine and others haven't replied -- so this is
notice and a standing offer, not blanket consent. A good-faith pass keeps
real names out of committed files, but older commits may contain some real
team names or first names. Spot one that bothers you: open an issue and
we'll take another pass.

If you are a league member and want your stand-in changed, email the address
in the [README](../README.md#contact).
